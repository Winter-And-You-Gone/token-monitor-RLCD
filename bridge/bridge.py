"""RLCD bridge daemon.

GET /api/usage           -> live usage (cached 60s)
GET /api/usage?mock=1    -> deterministic mock payload, for firmware bring-up
GET /healthz             -> liveness

Run: `uv run bridge.py` (defaults to 127.0.0.1:7777). Set RLCD_HOST=0.0.0.0
only together with RLCD_AUTH_TOKEN when exposing it to the LAN.
"""
from __future__ import annotations

import os
import ipaddress
import json
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Header, HTTPException, Query
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse


def _load_dotenv(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end > 0:
                value = value[1:end]
        else:
            value = value.split(" #", 1)[0].strip()
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(Path(__file__).with_name(".env"))

from schema import (
    Bucket,
    ClaudeUsage,
    CodexRadar,
    DeepSeek,
    ModelBreakdown,
    OtherAgentUsage,
    PetState,
    RadarPoint,
    RadarTrend,
    UsageReport,
    Weather,
)
from sources.claude_local import fetch_claude, fetch_other_agents
from sources.weather import fetch_weather
from sources.deepseek import fetch_deepseek
from sources.codexradar import fetch_codexradar


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.0, value)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}

REFRESH_INTERVAL_SEC = int(os.environ.get("RLCD_REFRESH_SEC", "45"))
PET_ACTIVE_TTL_SEC = int(os.environ.get("RLCD_PET_ACTIVE_TTL_SEC", "90"))
PET_COMPLETED_HOLD_SEC = float(os.environ.get("RLCD_PET_COMPLETED_HOLD_SEC", "2.0"))
PET_SLEEP_SEQUENCE_ENABLED = _bool_env("RLCD_PET_SLEEP_SEQUENCE", True)
PET_MOUSE_IDLE_TIMEOUT_SEC = _float_env("RLCD_PET_MOUSE_IDLE_SEC", 20.0)
PET_IDLE_LOOK_DURATION_SEC = _float_env("RLCD_PET_IDLE_LOOK_SEC", 14.0)
PET_MOUSE_SLEEP_TIMEOUT_SEC = _float_env("RLCD_PET_MOUSE_SLEEP_SEC", 300.0)
PET_YAWN_DURATION_SEC = _float_env("RLCD_PET_YAWN_SEC", 3.0)
PET_DEEP_SLEEP_TIMEOUT_SEC = _float_env("RLCD_PET_DEEP_SLEEP_SEC", 600.0)
PET_COLLAPSE_DURATION_SEC = _float_env("RLCD_PET_COLLAPSE_SEC", 0.8)
PET_WAKE_DURATION_SEC = _float_env("RLCD_PET_WAKE_SEC", 1.5)
PET_IDLE_LOOK_ASSET = os.environ.get("RLCD_PET_IDLE_LOOK_ASSET", "clawd-idle-reading.svg").strip()
PET_MOUSE_MONITOR_ENABLED = _bool_env("RLCD_PET_MOUSE_MONITOR", os.name == "nt")
PET_MOUSE_MONITOR_POLL_SEC = max(0.1, _float_env("RLCD_PET_MOUSE_POLL_SEC", 0.5))
PET_MOUSE_MONITOR_MIN_DELTA = max(1.0, _float_env("RLCD_PET_MOUSE_MIN_DELTA", 1.0))
INCLUDE_OTHERS = os.environ.get("RLCD_INCLUDE_OTHERS", "1") != "0"
AUTH_TOKEN = os.environ.get("RLCD_AUTH_TOKEN") or None  # blank/unset = no auth
ALLOW_QUERY_TOKEN = os.environ.get("RLCD_ALLOW_QUERY_TOKEN", "0") == "1"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _start_refresher()
    yield


app = FastAPI(title="RLCD bridge", version="0.1.0", lifespan=_lifespan)
SIM_PATH = Path(__file__).with_name("sim.html")
ASSET_DIR = Path(__file__).resolve().parents[1] / "docs" / "assets"
CLAWD_SVG_DIR = Path(__file__).resolve().parents[1] / "clawd-on-desk" / "assets" / "svg"
CLAWD_RLCD_GIF_DIR = Path(__file__).with_name("assets") / "clawd_rlcd" / "size-56" / "gifs"

PET_STATE_ASSETS = {
    "idle": "clawd-idle-follow.svg",
    "yawning": "clawd-idle-yawn.svg",
    "dozing": "clawd-idle-doze.svg",
    "collapsing": "clawd-collapse-sleep.svg",
    "thinking": "clawd-working-thinking.svg",
    "working": "clawd-working-typing.svg",
    "juggling": "clawd-working-juggling.svg",
    "sweeping": "clawd-working-sweeping.svg",
    "error": "clawd-error.svg",
    "attention": "clawd-happy.svg",
    "notification": "clawd-notification.svg",
    "completed": "clawd-happy.svg",
    "carrying": "clawd-working-carrying.svg",
    "sleeping": "clawd-sleeping.svg",
    "waking": "clawd-wake.svg",
}
PET_IDLE_ANIMATION_ASSETS = {
    "clawd-idle-reading.svg",
}
PET_ASSET_TO_RLCD_GIF = {
    "clawd-idle-follow.svg": "clawd-idle.gif",
    "clawd-idle-reading.svg": "clawd-idle-reading.gif",
    "clawd-idle-yawn.svg": "clawd-idle-reading.gif",
    "clawd-idle-doze.svg": "clawd-sleeping.gif",
    "clawd-collapse-sleep.svg": "clawd-sleeping.gif",
    "clawd-working-thinking.svg": "clawd-thinking.gif",
    "clawd-working-typing.svg": "clawd-typing.gif",
    "clawd-headphones-groove.svg": "clawd-headphones-groove.gif",
    "clawd-working-juggling.svg": "clawd-juggling.gif",
    "clawd-working-sweeping.svg": "clawd-sweeping.gif",
    "clawd-error.svg": "clawd-error.gif",
    "clawd-happy.svg": "clawd-happy.gif",
    "clawd-notification.svg": "clawd-notification.gif",
    "clawd-working-carrying.svg": "clawd-carrying.gif",
    "clawd-sleeping.svg": "clawd-sleeping.gif",
    "clawd-wake.svg": "clawd-mini-enter.gif",
    "clawd-working-building.svg": "clawd-building.gif",
}
STATE_PRIORITY = {
    "error": 8,
    "notification": 7,
    "sweeping": 6,
    "attention": 5,
    "carrying": 4,
    "juggling": 4,
    "working": 3,
    "thinking": 2,
    "idle": 1,
    "sleeping": 0,
}
ONESHOT_STATES = {"attention", "error", "sweeping", "notification", "carrying"}
SUPPRESSIBLE_ONESHOT_STATES = {"attention", "completed"}
SLEEP_SEQUENCE_STATES = {"yawning", "dozing", "collapsing", "sleeping", "waking"}
PET_EVENT_STATES = {
    "SessionStart": "idle",
    "SessionEnd": "sleeping",
    "UserPromptSubmit": "thinking",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PostToolUseFailure": "error",
    "Stop": "idle",
    "StopFailure": "error",
    "ApiError": "error",
    "SubagentStart": "juggling",
    "SubagentStop": "working",
    "PreCompact": "sweeping",
    "PostCompact": "attention",
    "Notification": "notification",
    "PermissionRequest": "notification",
    "Elicitation": "notification",
    "WorktreeCreate": "carrying",
}
CODEX_EVENT_STATES = {
    "SessionStart": "idle",
    "UserPromptSubmit": "thinking",
    "PreToolUse": "working",
    "PermissionRequest": "notification",
    "PostToolUse": "working",
    "Stop": "codex-turn-end",
    "Notification": "notification",
    "SubagentStart": "juggling",
    "SubagentStop": "working",
    "event_msg:context_compacted": "sweeping",
}
ANTIGRAVITY_EVENT_STATES = {
    "PreInvocation": "thinking",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PostInvocation": "idle",
    "Stop": "idle",
}
MAX_PET_SESSIONS = int(os.environ.get("RLCD_PET_MAX_SESSIONS", "20"))
CODEX_JSONL_MONITOR_ENABLED = _bool_env("RLCD_CODEX_JSONL_MONITOR", True)
CODEX_JSONL_POLL_SEC = _float_env("RLCD_CODEX_JSONL_POLL_SEC", 1.5)
CODEX_JSONL_RECENT_SEC = _float_env("RLCD_CODEX_JSONL_RECENT_SEC", 120.0)
CODEX_SESSION_DIR = Path(os.environ.get("RLCD_CODEX_SESSION_DIR", "") or (Path.home() / ".codex" / "sessions"))
CODEX_JSONL_EVENT_STATES = {
    # Codex-internal events read directly from session jsonl, independent of
    # ~/.codex/hooks.json (which Clawd on Desk rewrites, breaking codex's
    # trusted_hash and silently disabling all hooks).
    # Mirrors clawd-on-desk/agents/codex.js logEventMap.
    "session_meta": "idle",
    "event_msg:context_compacted": "sweeping",
    "compacted": "sweeping",
    "event_msg:thread_rolled_back": "sweeping",
    # Turn lifecycle: user_message/task_started = thinking (model reasoning
    # starts before any tool actually runs, matching clawd's logEventMap).
    "event_msg:user_message": "thinking",
    "event_msg:task_started": "thinking",
    "response_item:reasoning": "thinking",
    "event_msg:agent_reasoning": "thinking",
    # Active work signals: tool calls, command exec, patches, guardian review.
    "response_item:function_call": "working",
    "response_item:custom_tool_call": "working",
    "response_item:web_search_call": "working",
    "response_item:tool_search_call": "working",
    "response_item:tool_search_output": "working",
    "response_item:function_call_output": "working",
    "response_item:custom_tool_call_output": "working",
    "event_msg:guardian_assessment": "working",
    "event_msg:exec_command_end": "working",
    "event_msg:patch_apply_end": "working",
    "event_msg:web_search_end": "working",
    "event_msg:mcp_tool_call_end": "working",
    # Turn end / abort: codex-turn-end is resolved by _codex_stop_state to
    # attention (happy) when the turn had tool use, else idle.
    "event_msg:task_complete": "codex-turn-end",
    "event_msg:turn_aborted": "idle",
}
# Events that mark the start of a new codex turn (reset had_tool_use).
CODEX_TURN_RESET_EVENTS = {
    "UserPromptSubmit",
    "event_msg:user_message",
    "event_msg:task_started",
    "session_meta",
}
# Events that indicate codex actually invoked a tool (set had_tool_use=True).
CODEX_TOOL_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "response_item:function_call",
    "response_item:custom_tool_call",
    "response_item:web_search_call",
    "response_item:tool_search_call",
    "response_item:tool_search_output",
    "response_item:function_call_output",
    "response_item:custom_tool_call_output",
    "event_msg:guardian_assessment",
    "event_msg:exec_command_end",
    "event_msg:patch_apply_end",
    "event_msg:web_search_end",
    "event_msg:mcp_tool_call_end",
}

_cache_lock = threading.Lock()
_cache: dict[str, object] = {"report": None, "ts": 0.0, "error": None, "other": []}
_pet_lock = threading.RLock()
_pet_cond = threading.Condition(_pet_lock)
_pet_state = PetState(updated_at=datetime.now(timezone.utc))


@dataclass
class _PetSession:
    state: str
    agent: str
    event: str
    updated_at: datetime
    resume_state: str | None = None
    last_tool_boundary_at: datetime | None = None
    headless: bool = False
    had_tool_use: bool = False


@dataclass
class _CodexLogEntry:
    offset: int
    session_id: str
    partial: str = ""


_pet_sessions: dict[str, _PetSession] = {}
_codex_log_entries: dict[Path, _CodexLogEntry] = {}
_codex_log_monitor_started = False
_pet_sleep_timer: threading.Timer | None = None
_pet_idle_started_at: datetime | None = None
_weather_override_lock = threading.Lock()
_weather_override_cache: dict[tuple[object, ...], dict[str, object]] = {}
_weather_override_inflight: set[tuple[object, ...]] = set()
_weather_override_failures: dict[tuple[object, ...], float] = {}
WEATHER_OVERRIDE_TTL = int(os.environ.get("RLCD_WEATHER_OVERRIDE_TTL", "600"))
WEATHER_OVERRIDE_RETRY_SEC = int(os.environ.get("RLCD_WEATHER_OVERRIDE_RETRY_SEC", "30"))
OTHER_AGENT_ORDER = ("codex", "gemini", "copilot")



def _pet_stamp(state: PetState) -> str:
    if state.updated_at is None:
        return ""
    return state.updated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _pet_asset_for(state: str, subagents: int = 0, sessions: int = 0) -> str:
    if state == "juggling":
        return "clawd-working-juggling.svg"
    if state == "working":
        # Tiered working animation, mirroring clawd-on-desk workingTiers
        # (themes/clawd/theme.json). `sessions` is the cross-agent active
        # session count from _pet_active_session_count(), so opening a second
        # or third agent (claude desktop / codex / opencode / ZCode ...) bumps
        # the tier even though no single agent has multiple sessions.
        if sessions >= 3:
            return "clawd-working-building.svg"
        if sessions >= 2:
            return "clawd-headphones-groove.svg"
        return "clawd-working-typing.svg"
    return PET_STATE_ASSETS.get(state, PET_STATE_ASSETS["idle"])


def _state_priority(state: str) -> int:
    return STATE_PRIORITY.get(state, 0)


PET_SLEEP_NEXT_STATE = {
    "idle": "yawning",
    "yawning": "dozing",
    "dozing": "collapsing",
    "collapsing": "sleeping",
}
PET_SLEEP_TIMER_STATES = set(PET_SLEEP_NEXT_STATE) | {"waking"}
PET_WAKEABLE_STATES = {"dozing", "collapsing", "sleeping"}
PET_MOUSE_CANCEL_SLEEP_STATES = {"yawning"} | PET_WAKEABLE_STATES
PET_WAKE_TRANSITION_TARGET_STATES = {"idle", "thinking", "working", "juggling"}


def _pet_idle_elapsed(now: datetime | None = None) -> float:
    if _pet_idle_started_at is None:
        return 0.0
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - _pet_idle_started_at).total_seconds())


def _pet_idle_asset() -> str:
    asset = PET_IDLE_LOOK_ASSET or PET_STATE_ASSETS["yawning"]
    valid_assets = set(PET_STATE_ASSETS.values()) | PET_IDLE_ANIMATION_ASSETS
    return asset if asset in valid_assets else "clawd-idle-reading.svg"


def _pet_sleep_delay(state: PetState) -> float | None:
    if not PET_SLEEP_SEQUENCE_ENABLED:
        return None
    if state.state == "idle":
        elapsed = _pet_idle_elapsed()
        if state.asset == _pet_idle_asset():
            return max(0.0, PET_IDLE_LOOK_DURATION_SEC)
        if elapsed < PET_MOUSE_IDLE_TIMEOUT_SEC:
            return max(0.0, PET_MOUSE_IDLE_TIMEOUT_SEC - elapsed)
        return max(0.0, PET_MOUSE_SLEEP_TIMEOUT_SEC - elapsed)
    if state.state == "yawning":
        return PET_YAWN_DURATION_SEC
    if state.state == "dozing":
        return max(0.0, PET_DEEP_SLEEP_TIMEOUT_SEC - PET_MOUSE_SLEEP_TIMEOUT_SEC - PET_YAWN_DURATION_SEC)
    if state.state == "collapsing":
        return PET_COLLAPSE_DURATION_SEC
    if state.state == "waking":
        return PET_WAKE_DURATION_SEC
    return None


def _pet_cancel_sleep_timer_locked() -> None:
    global _pet_sleep_timer
    timer = _pet_sleep_timer
    _pet_sleep_timer = None
    if timer is not None:
        timer.cancel()


def _pet_schedule_sleep_timer_locked(state: PetState) -> None:
    global _pet_sleep_timer
    _pet_cancel_sleep_timer_locked()
    if state.updated_at is None or state.state not in PET_SLEEP_TIMER_STATES:
        return
    delay = _pet_sleep_delay(state)
    if delay is None:
        return
    stamp = state.updated_at
    displayed_state = state.state
    displayed_agent = state.agent

    def _advance() -> None:
        global _pet_sleep_timer
        with _pet_cond:
            if _pet_sleep_timer is timer:
                _pet_sleep_timer = None
            if _pet_state.state != displayed_state or _pet_state.updated_at != stamp:
                return
            if displayed_state != "waking":
                resolved_state, _ = _pet_resolved_display_state()
                if resolved_state != "idle":
                    return
        if displayed_state == "waking":
            resolved_state, resolved_agent = _pet_resolved_display_state()
            _pet_display_state(
                resolved_state,
                agent=resolved_agent,
                event="waking-timeout",
                schedule_return=False,
                wake_from_sleep=False,
            )
            return
        if displayed_state == "idle":
            elapsed = _pet_idle_elapsed()
            if state.asset == _pet_idle_asset():
                _pet_display_state(
                    "idle",
                    agent=displayed_agent,
                    event="sleep-idle-follow",
                    asset_override=PET_STATE_ASSETS["idle"],
                    schedule_return=False,
                    wake_from_sleep=False,
                    preserve_idle_started=True,
                )
                return
            if elapsed < PET_MOUSE_SLEEP_TIMEOUT_SEC:
                _pet_display_state(
                    "idle",
                    agent=displayed_agent,
                    event="sleep-idle-look",
                    asset_override=_pet_idle_asset(),
                    schedule_return=False,
                    wake_from_sleep=False,
                    preserve_idle_started=True,
                )
                return
        next_state = PET_SLEEP_NEXT_STATE.get(displayed_state)
        if next_state:
            _pet_display_state(
                next_state,
                agent=displayed_agent,
                event=f"sleep-{next_state}",
                schedule_return=False,
                wake_from_sleep=False,
            )

    timer = threading.Timer(delay, _advance)
    timer.daemon = True
    _pet_sleep_timer = timer
    timer.start()


def _pet_should_wake_before_display(target_state: str, event: str = "") -> bool:
    if not PET_SLEEP_SEQUENCE_ENABLED or PET_WAKE_DURATION_SEC <= 0:
        return False
    if target_state not in PET_WAKE_TRANSITION_TARGET_STATES:
        return False
    if (event or "").startswith(("sleep-", "waking-timeout")):
        return False
    with _pet_cond:
        return _pet_state.state in PET_WAKEABLE_STATES


def _pet_handle_mouse_activity() -> PetState | None:
    global _pet_idle_started_at
    if not PET_SLEEP_SEQUENCE_ENABLED:
        return None
    now = datetime.now(timezone.utc)
    action = ""
    current: PetState | None = None
    with _pet_cond:
        current = _pet_state.model_copy()
        resolved_state, _ = _pet_resolved_display_state()
        if resolved_state != "idle":
            return None
        if current.state == "idle":
            if current.asset == PET_STATE_ASSETS["idle"]:
                _pet_idle_started_at = now
                _pet_schedule_sleep_timer_locked(current)
                return current
            action = "idle-follow"
        elif current.state in PET_MOUSE_CANCEL_SLEEP_STATES:
            action = "wake" if current.state in PET_WAKEABLE_STATES else "idle-follow"
        else:
            return None
    if action == "idle-follow":
        return _pet_display_state(
            "idle",
            agent=current.agent if current else "",
            event="mouse-move",
            asset_override=PET_STATE_ASSETS["idle"],
            schedule_return=False,
            wake_from_sleep=False,
        )
    if action == "wake":
        return _pet_display_state(
            "idle",
            agent=current.agent if current else "",
            event="mouse-move",
            schedule_return=False,
            wake_from_sleep=True,
        )
    return None


def _read_windows_cursor_pos() -> tuple[int, int] | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    point = POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return None
    return int(point.x), int(point.y)


def _mouse_monitor_loop() -> None:
    last = _read_windows_cursor_pos()
    while True:
        time.sleep(PET_MOUSE_MONITOR_POLL_SEC)
        current = _read_windows_cursor_pos()
        if current is None:
            continue
        if last is None:
            last = current
            continue
        dx = abs(current[0] - last[0])
        dy = abs(current[1] - last[1])
        if dx >= PET_MOUSE_MONITOR_MIN_DELTA or dy >= PET_MOUSE_MONITOR_MIN_DELTA:
            last = current
            _pet_handle_mouse_activity()


_mouse_monitor_started = False


def _start_mouse_monitor() -> None:
    global _mouse_monitor_started
    if _mouse_monitor_started or not PET_MOUSE_MONITOR_ENABLED:
        return
    if _read_windows_cursor_pos() is None:
        return
    _mouse_monitor_started = True
    threading.Thread(target=_mouse_monitor_loop, name="pet-mouse-monitor", daemon=True).start()


def _pet_active_session_count() -> int:
    with _pet_cond:
        return sum(
            1
            for session in _pet_sessions.values()
            if not session.headless and session.state in {"working", "thinking", "juggling"}
        )


def _pet_juggling_session_count() -> int:
    with _pet_cond:
        return sum(
            1
            for session in _pet_sessions.values()
            if not session.headless and session.state == "juggling"
        )


def _pet_resolved_display_state() -> tuple[str, str]:
    with _pet_cond:
        best_state = "idle"
        best_agent = ""
        best_priority = _state_priority(best_state)
        best_updated_at: datetime | None = None
        has_non_headless = False
        for session in _pet_sessions.values():
            if session.headless:
                continue
            has_non_headless = True
            priority = _state_priority(session.state)
            if priority > best_priority or (
                priority == best_priority
                and best_updated_at is not None
                and session.updated_at >= best_updated_at
            ):
                best_state = session.state
                best_agent = session.agent
                best_priority = priority
                best_updated_at = session.updated_at
        if not has_non_headless:
            return "idle", ""
        return best_state, best_agent


def _pet_display_state(
    state: str,
    *,
    agent: str = "",
    event: str = "",
    sessions_hint: int = 0,
    subagents_hint: int = 0,
    schedule_return: bool = True,
    wake_from_sleep: bool = True,
    asset_override: str = "",
    preserve_idle_started: bool = False,
) -> PetState:
    global _pet_state, _pet_idle_started_at
    state = state if state in PET_STATE_ASSETS else "idle"
    if wake_from_sleep and _pet_should_wake_before_display(state, event):
        return _pet_display_state(
            "waking",
            agent=agent,
            event=event or "wake",
            sessions_hint=sessions_hint,
            subagents_hint=subagents_hint,
            schedule_return=False,
            wake_from_sleep=False,
        )
    active_sessions = max(_pet_active_session_count(), max(0, int(sessions_hint or 0)))
    subagents = max(_pet_juggling_session_count(), max(0, int(subagents_hint or 0)))
    next_state = PetState(
        state=state,
        agent=(agent or "")[:32],
        event=(event or "")[:40],
        sessions=active_sessions,
        subagents=subagents,
        asset=asset_override or _pet_asset_for(state, subagents=subagents, sessions=active_sessions),
        updated_at=datetime.now(timezone.utc),
    )
    with _pet_cond:
        if state == "idle":
            if not preserve_idle_started or _pet_idle_started_at is None:
                _pet_idle_started_at = next_state.updated_at
        else:
            _pet_idle_started_at = None
        _pet_state = next_state
        result = _pet_state.model_copy()
        _pet_schedule_sleep_timer_locked(result)
        _pet_cond.notify_all()
    if schedule_return and (state in ONESHOT_STATES or state == "completed"):
        _schedule_pet_auto_return(result.updated_at, state)
    return result


def _set_pet_state(update: PetState) -> PetState:
    return _pet_display_state(
        update.state,
        agent=update.agent,
        event=update.event,
        sessions_hint=update.sessions,
        subagents_hint=update.subagents,
    )


def _schedule_pet_auto_return(stamp: datetime | None, displayed_state: str) -> None:
    if stamp is None or PET_COMPLETED_HOLD_SEC <= 0:
        return

    def _return_to_resolved() -> None:
        with _pet_cond:
            if _pet_state.state != displayed_state or _pet_state.updated_at != stamp:
                return
        state, agent = _pet_resolved_display_state()
        _pet_display_state(state, agent=agent, event=f"{displayed_state}-timeout", schedule_return=False)

    timer = threading.Timer(PET_COMPLETED_HOLD_SEC, _return_to_resolved)
    timer.daemon = True
    timer.start()


def _cleanup_stale_pet_sessions() -> bool:
    now = datetime.now(timezone.utc)
    changed = False
    stale_active = {"thinking", "working", "juggling", "sweeping", "carrying", "waking"}
    for session_id, session in list(_pet_sessions.items()):
        if session.state in stale_active:
            age = (now - session.updated_at).total_seconds()
            if age > PET_ACTIVE_TTL_SEC:
                del _pet_sessions[session_id]
                changed = True
    return changed


def _evict_old_pet_sessions() -> None:
    if MAX_PET_SESSIONS <= 0:
        return
    while len(_pet_sessions) > MAX_PET_SESSIONS:
        idle_ids = [
            (session.updated_at, session_id)
            for session_id, session in _pet_sessions.items()
            if session.state == "idle"
        ]
        candidates = idle_ids or [
            (session.updated_at, session_id)
            for session_id, session in _pet_sessions.items()
        ]
        _, oldest_id = min(candidates, key=lambda item: item[0])
        _pet_sessions.pop(oldest_id, None)


def _get_pet_state() -> PetState:
    changed = False
    with _pet_cond:
        changed = _cleanup_stale_pet_sessions()
        state = _pet_state.model_copy()
    active_states = {"thinking", "working", "juggling", "sweeping", "carrying", "waking"}
    if changed:
        resolved_state, agent = _pet_resolved_display_state()
        return _pet_display_state(resolved_state, agent=agent, event="timeout", schedule_return=False)
    if state.state in active_states and state.updated_at is not None:
        age = (datetime.now(timezone.utc) - state.updated_at).total_seconds()
        if age > PET_ACTIVE_TTL_SEC and not _pet_sessions:
            return _pet_display_state("idle", agent=state.agent, event="timeout", schedule_return=False)
    return state


def _pet_state_from_event(data: dict[str, object]) -> PetState:
    raw_state = _pet_string(data, "state")
    event = _pet_string(data, "event", "hook_event_name", "hookEventName", "event_name", "eventName")
    agent = _pet_agent(data)
    state = _pet_state_for_event(data, raw_state, event, agent)
    return PetState(
        state=state,
        agent=agent,
        event=event,
        sessions=_pet_int(data, "sessions", "session_count"),
        subagents=_pet_int(data, "subagents", "subagent_count"),
    )


def _pet_string(data: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _pet_int(data: dict[str, object], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return 0


def _pet_agent(data: dict[str, object]) -> str:
    agent = _pet_string(data, "agent", "agent_id", "agentId")
    if agent:
        return _normalize_pet_agent(agent)
    if _looks_like_antigravity_payload(data):
        return "antigravity-cli"
    if _looks_like_codex_payload(data):
        return "codex"
    return ""


def _normalize_pet_agent(agent: str) -> str:
    normalized = agent.strip().lower()
    if normalized in {"claude", "claude-code"}:
        return "claude-code"
    if normalized in {"codex", "codex-cli"}:
        return "codex"
    if normalized in {"ag", "agy", "antigravity", "antigravity-cli"}:
        return "antigravity-cli"
    return agent.strip()


def _pet_session_id(data: dict[str, object], agent: str) -> str:
    session_id = _pet_string(data, "session_id", "sessionId")
    if session_id:
        if _is_codex_agent(agent) and not session_id.startswith("codex:"):
            return f"codex:{session_id}"
        if _is_antigravity_agent(agent) and not session_id.startswith("antigravity:"):
            return f"antigravity:{session_id}"
        return session_id
    conversation_id = _pet_string(data, "conversationId", "conversation_id")
    if conversation_id:
        return conversation_id if conversation_id.startswith("antigravity:") else f"antigravity:{conversation_id}"
    transcript = _pet_string(data, "transcriptPath", "transcript_path")
    if transcript:
        codex_id = _codex_session_id_from_transcript(transcript)
        if _is_codex_agent(agent) or codex_id:
            raw = codex_id or Path(transcript).stem or "default"
            return raw if raw.startswith("codex:") else f"codex:{raw}"
        raw = Path(transcript).parent.name or transcript
        return raw if raw.startswith("antigravity:") else f"antigravity:{raw}"
    return f"{agent or 'agent'}:default"


def _pet_headless(data: dict[str, object]) -> bool:
    value = data.get("headless")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _codex_session_id_from_transcript(transcript: str) -> str:
    if not transcript:
        return ""
    name = Path(transcript.replace("\\", "/")).name
    match = re.match(
        r"^rollout-.+-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
        name,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _codex_jsonl_key(obj: object) -> str:
    if not isinstance(obj, dict):
        return ""
    item_type = obj.get("type")
    if not isinstance(item_type, str) or not item_type:
        return ""
    payload = obj.get("payload")
    subtype = ""
    if isinstance(payload, dict) and isinstance(payload.get("type"), str):
        subtype = str(payload.get("type") or "")
    return f"{item_type}:{subtype}" if subtype else item_type


def _codex_process_jsonl_line(line: str, entry: _CodexLogEntry, apply_event=None) -> bool:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    key = _codex_jsonl_key(obj)
    state = CODEX_JSONL_EVENT_STATES.get(key)
    if not state:
        return False
    if apply_event is None:
        apply_event = _apply_pet_event
    apply_event({
        "state": state,
        "event": key,
        "agent": "codex",
        "session_id": entry.session_id,
    })
    return True


def _codex_session_dirs(root: Path = CODEX_SESSION_DIR, now: datetime | None = None) -> list[Path]:
    current = now or datetime.now()
    dirs: list[Path] = []
    seen: set[Path] = set()
    for days_ago in range(2):
        day = current - timedelta(days=days_ago)
        path = root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        if path not in seen:
            seen.add(path)
            dirs.append(path)
    return dirs


def _codex_rollout_files(root: Path = CODEX_SESSION_DIR) -> list[Path]:
    files: list[Path] = []
    for directory in _codex_session_dirs(root):
        try:
            files.extend(path for path in directory.iterdir() if path.name.startswith("rollout-") and path.suffix == ".jsonl")
        except OSError:
            continue
    return files


def _codex_log_entry(file_path: Path, offset: int) -> _CodexLogEntry | None:
    session_id = _codex_session_id_from_transcript(str(file_path))
    if not session_id:
        return None
    return _CodexLogEntry(offset=max(0, int(offset)), session_id=session_id)


def _codex_poll_log_file(file_path: Path, *, start_at_end: bool = False, apply_event=None) -> int:
    try:
        stat = file_path.stat()
    except OSError:
        return 0
    entry = _codex_log_entries.get(file_path)
    if entry is None:
        entry = _codex_log_entry(file_path, stat.st_size if start_at_end else 0)
        if entry is None:
            return 0
        _codex_log_entries[file_path] = entry
        if start_at_end:
            return 0
    if stat.st_size < entry.offset:
        entry.offset = 0
        entry.partial = ""
    if stat.st_size <= entry.offset:
        return 0
    try:
        with file_path.open("rb") as fh:
            fh.seek(entry.offset)
            data = fh.read(stat.st_size - entry.offset)
    except OSError:
        return 0
    entry.offset = stat.st_size
    text = entry.partial + data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    entry.partial = lines.pop() or ""
    applied = 0
    for line in lines:
        if line.strip() and _codex_process_jsonl_line(line, entry, apply_event=apply_event):
            applied += 1
    return applied


def _codex_log_poll(*, root: Path = CODEX_SESSION_DIR, bootstrap: bool = False, apply_event=None) -> int:
    applied = 0
    now_ts = time.time()
    files = _codex_rollout_files(root)
    active = set(files)
    for file_path in files:
        first_seen = file_path not in _codex_log_entries
        if first_seen and not bootstrap:
            try:
                if now_ts - file_path.stat().st_mtime > CODEX_JSONL_RECENT_SEC:
                    continue
            except OSError:
                continue
        applied += _codex_poll_log_file(file_path, start_at_end=bootstrap and first_seen, apply_event=apply_event)
    for tracked_path in list(_codex_log_entries):
        if tracked_path not in active:
            _codex_log_entries.pop(tracked_path, None)
    return applied


def _start_codex_log_monitor() -> None:
    global _codex_log_monitor_started
    if _codex_log_monitor_started or not CODEX_JSONL_MONITOR_ENABLED or CODEX_JSONL_POLL_SEC <= 0:
        return
    _codex_log_monitor_started = True
    try:
        _codex_log_poll(bootstrap=True)
    except Exception:
        pass

    def _loop() -> None:
        while True:
            try:
                _codex_log_poll()
            except Exception:
                pass
            time.sleep(CODEX_JSONL_POLL_SEC)

    threading.Thread(target=_loop, name="codex-jsonl-monitor", daemon=True).start()


def _looks_like_codex_payload(data: dict[str, object]) -> bool:
    if (
        isinstance(data.get("codexOriginator"), str)
        or isinstance(data.get("codexSource"), str)
        or isinstance(data.get("codex_originator"), str)
        or isinstance(data.get("codex_source"), str)
    ):
        return True
    transcript = _pet_string(data, "transcript_path", "transcriptPath")
    return bool(transcript and _codex_session_id_from_transcript(transcript))


def _is_codex_agent(agent: str) -> bool:
    return agent.strip().lower() == "codex"


def _codex_stop_state(session: _PetSession | None) -> str:
    if session and session.had_tool_use and not session.headless:
        return "attention"
    return "idle"


def _codex_had_tool_use(event: str, existing: _PetSession | None) -> bool:
    if event in CODEX_TURN_RESET_EVENTS:
        return False
    if event in CODEX_TOOL_EVENTS:
        return True
    return existing.had_tool_use if existing else False


def _is_antigravity_agent(agent: str) -> bool:
    return _normalize_pet_agent(agent) == "antigravity-cli"


def _looks_like_antigravity_payload(data: dict[str, object]) -> bool:
    return (
        isinstance(data.get("conversationId"), str)
        or isinstance(data.get("workspacePaths"), list)
        or isinstance(data.get("toolCall"), dict)
        or "fullyIdle" in data
        or "terminationReason" in data
        or isinstance(data.get("artifactDirectoryPath"), str)
    )


def _pet_has_payload_error(data: dict[str, object]) -> bool:
    error = data.get("error")
    return error not in (None, False, "")


def _pet_has_stop_error(data: dict[str, object]) -> bool:
    if _pet_has_payload_error(data):
        return True
    reason = data.get("terminationReason")
    if not isinstance(reason, str):
        return False
    lower = reason.lower()
    return "error" in lower or "failed" in lower or "failure" in lower


def _pet_state_for_event(data: dict[str, object], raw_state: str, event: str, agent: str) -> str:
    if raw_state == "codex-turn-end":
        return raw_state
    if event == "Stop" and raw_state == "attention":
        return "idle"
    if raw_state in PET_STATE_ASSETS:
        return raw_state
    if _is_codex_agent(agent):
        if event == "PostToolUse" and _pet_has_payload_error(data):
            return "error"
        if event == "Stop" and _pet_has_stop_error(data):
            return "error"
        return CODEX_EVENT_STATES.get(event, PET_EVENT_STATES.get(event, "idle"))
    if _is_antigravity_agent(agent) or _looks_like_antigravity_payload(data):
        if event == "PostToolUse" and _pet_has_payload_error(data):
            return "error"
        if event == "Stop" and _pet_has_stop_error(data):
            return "error"
        if event == "Stop" and data.get("fullyIdle") is False:
            return "working"
        return ANTIGRAVITY_EVENT_STATES.get(event, PET_EVENT_STATES.get(event, "idle"))
    return PET_EVENT_STATES.get(event, "idle")


def _pet_bool(data: dict[str, object], *keys: str) -> bool:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _apply_pet_event(data: dict[str, object]) -> PetState:
    update = _pet_state_from_event(data)
    event = update.event
    agent = update.agent
    session_id = _pet_session_id(data, agent)
    headless = _pet_headless(data)
    now = datetime.now(timezone.utc)

    with _pet_cond:
        _cleanup_stale_pet_sessions()
        existing = _pet_sessions.get(session_id)
        effective_agent = agent or (existing.agent if existing else "")
        effective_headless = headless or (existing.headless if existing else False)

        if event == "Stop" and _is_codex_agent(effective_agent) and _pet_bool(data, "stop_hook_active"):
            resolved_state, resolved_agent = _pet_resolved_display_state()
            return _pet_display_state(
                resolved_state,
                agent=resolved_agent,
                event=event,
                sessions_hint=update.sessions,
                subagents_hint=update.subagents,
                schedule_return=False,
            )

        if update.state == "codex-turn-end":
            update.state = _codex_stop_state(existing)

        if event == "SessionEnd":
            ending = existing
            _pet_sessions.pop(session_id, None)
            _evict_old_pet_sessions()
            if update.state == "sweeping" and not (ending and ending.headless):
                return _pet_display_state(
                    "sweeping",
                    agent=effective_agent,
                    event=event,
                    sessions_hint=update.sessions,
                    subagents_hint=update.subagents,
                )
            resolved_state, resolved_agent = _pet_resolved_display_state()
            return _pet_display_state(
                resolved_state,
                agent=resolved_agent,
                event=event,
                sessions_hint=update.sessions,
                subagents_hint=update.subagents,
                schedule_return=False,
            )

        if event in {"SubagentStop", "subagentStop"}:
            if existing and existing.state == "juggling":
                if existing.resume_state:
                    existing.state = existing.resume_state
                    existing.resume_state = None
                    existing.event = event
                    existing.updated_at = now
                    existing.agent = effective_agent or existing.agent
                else:
                    _pet_sessions.pop(session_id, None)
            elif existing:
                existing.event = event
                existing.updated_at = now
                existing.agent = effective_agent or existing.agent
            else:
                _pet_sessions.pop(session_id, None)
            _evict_old_pet_sessions()
            resolved_state, resolved_agent = _pet_resolved_display_state()
            return _pet_display_state(
                resolved_state,
                agent=resolved_agent,
                event=event,
                sessions_hint=update.sessions,
                subagents_hint=update.subagents,
                schedule_return=False,
            )

        if update.state in ONESHOT_STATES or update.state in SLEEP_SEQUENCE_STATES or update.state == "completed":
            previous = existing
            _pet_sessions[session_id] = _PetSession(
                state="idle",
                agent=effective_agent,
                event=event,
                updated_at=now,
                resume_state=None,
                last_tool_boundary_at=previous.last_tool_boundary_at if previous else None,
                headless=effective_headless,
                had_tool_use=previous.had_tool_use if previous else False,
            )
            _evict_old_pet_sessions()
            if update.state in SUPPRESSIBLE_ONESHOT_STATES:
                resolved_state, resolved_agent = _pet_resolved_display_state()
                if resolved_state in {"thinking", "working", "juggling"}:
                    return _pet_display_state(
                        resolved_state,
                        agent=resolved_agent,
                        event=event,
                        sessions_hint=update.sessions,
                        subagents_hint=update.subagents,
                        schedule_return=False,
                    )
            return _pet_display_state(
                update.state,
                agent=effective_agent,
                event=event,
                sessions_hint=update.sessions,
                subagents_hint=update.subagents,
            )

        resume_state = None
        if event in {"SubagentStart", "subagentStart"}:
            resume_state = existing.state if existing and existing.state != "juggling" else (
                existing.resume_state if existing else None
            )
        elif existing:
            resume_state = existing.resume_state

        if existing and existing.state == "juggling" and update.state == "working" and event not in {"SubagentStop", "subagentStop"}:
            existing.updated_at = now
            existing.event = event
            existing.agent = effective_agent or existing.agent
            existing.last_tool_boundary_at = now if event in {"PostToolUse", "PostToolUseFailure"} else existing.last_tool_boundary_at
            existing.had_tool_use = _codex_had_tool_use(event, existing)
        else:
            _pet_sessions[session_id] = _PetSession(
                state=update.state,
                agent=effective_agent,
                event=event,
                updated_at=now,
                resume_state=resume_state,
                last_tool_boundary_at=now if event in {"PostToolUse", "PostToolUseFailure"} else (
                    existing.last_tool_boundary_at if existing else None
                ),
                headless=effective_headless,
                had_tool_use=_codex_had_tool_use(event, existing),
            )
        _evict_old_pet_sessions()
        resolved_state, resolved_agent = _pet_resolved_display_state()
        return _pet_display_state(
            resolved_state,
            agent=resolved_agent,
            event=event,
            sessions_hint=update.sessions,
            subagents_hint=update.subagents,
            schedule_return=False,
        )


def _reset_pet_state_for_tests() -> None:
    global _pet_state, _pet_idle_started_at
    with _pet_cond:
        _pet_cancel_sleep_timer_locked()
        _pet_sessions.clear()
        _codex_log_entries.clear()
        _pet_idle_started_at = None
        _pet_state = PetState(updated_at=datetime.now(timezone.utc))
        _pet_cond.notify_all()


def _order_other_agents(rows: list[OtherAgentUsage]) -> list[OtherAgentUsage]:
    def key(row: OtherAgentUsage) -> tuple[int, str]:
        agent = (row.agent or "").lower()
        try:
            return (OTHER_AGENT_ORDER.index(agent), agent)
        except ValueError:
            return (len(OTHER_AGENT_ORDER), agent)

    return sorted(rows, key=key)


def _merge_other_agents(
    fresh: list[OtherAgentUsage],
    fallback: list[OtherAgentUsage],
) -> list[OtherAgentUsage]:
    merged: dict[str, OtherAgentUsage] = {}
    for row in fallback:
        if row.agent:
            merged[row.agent.lower()] = row
    for row in fresh:
        if row.agent:
            merged[row.agent.lower()] = row
    return _order_other_agents(list(merged.values()))


def _cached_other_agents_locked() -> list[OtherAgentUsage]:
    cached = _cache.get("other")
    if isinstance(cached, list):
        return [row for row in cached if isinstance(row, OtherAgentUsage)]
    report = _cache.get("report")
    if isinstance(report, UsageReport):
        return list(report.other)
    return []


def _fetch_other_agents_with_fallback() -> list[OtherAgentUsage]:
    if not INCLUDE_OTHERS:
        return []
    with _cache_lock:
        fallback = _cached_other_agents_locked()
    try:
        fresh = fetch_other_agents()
    except Exception:
        return fallback
    if not fresh:
        return fallback
    return _merge_other_agents(fresh, fallback)


def _build_live_report() -> UsageReport:
    claude, ds_today = fetch_claude()
    others = _fetch_other_agents_with_fallback()
    return UsageReport(
        updated_at=datetime.now(timezone.utc),
        claude=claude,
        other=others,
        weather=fetch_weather(),
        deepseek=fetch_deepseek(ds_today),
        codexradar=fetch_codexradar(),
        pet=_get_pet_state(),
    )


def _get_cached() -> tuple[UsageReport | None, str | None]:
    # Non-blocking: a background thread keeps the cache warm, so clients
    # (the ESP32, with a short HTTP timeout) never wait on a cold ccusage run.
    with _cache_lock:
        return _cache.get("report"), _cache.get("error")


def _weather_override_key(
    lat: float | None,
    lon: float | None,
    city: str | None,
) -> tuple[object, ...]:
    label = (city or "").strip()
    if lat is not None and lon is not None:
        return ("coord", round(float(lat), 4), round(float(lon), 4), label.casefold())
    return ("city", label.casefold())


def _refresh_weather_override(
    key: tuple[object, ...],
    lat: float | None,
    lon: float | None,
    city: str | None,
) -> None:
    try:
        weather = fetch_weather(lat, lon, city)
        with _weather_override_lock:
            if weather is not None:
                _weather_override_cache[key] = {"weather": weather, "ts": time.time()}
                _weather_override_failures.pop(key, None)
            else:
                _weather_override_failures[key] = time.time()
    finally:
        with _weather_override_lock:
            _weather_override_inflight.discard(key)


def _get_weather_override(
    lat: float | None,
    lon: float | None,
    city: str | None,
) -> tuple[Weather | None, bool]:
    key = _weather_override_key(lat, lon, city)
    now = time.time()
    with _weather_override_lock:
        cached = _weather_override_cache.get(key)
        if cached and now - float(cached["ts"]) < WEATHER_OVERRIDE_TTL:
            return cached["weather"], False  # type: ignore
        stale_weather = cached["weather"] if cached else None
        failed_at = _weather_override_failures.get(key)
        if failed_at is not None and now - failed_at < WEATHER_OVERRIDE_RETRY_SEC:
            return stale_weather, False  # type: ignore
        if key not in _weather_override_inflight:
            _weather_override_inflight.add(key)
            threading.Thread(
                target=_refresh_weather_override,
                args=(key, lat, lon, city),
                name="weather-override",
                daemon=True,
            ).start()
        return stale_weather, True  # type: ignore


def _refresh_once() -> None:
    try:
        rep = _build_live_report()
        with _cache_lock:
            _cache.update(report=rep, ts=time.time(), error=None, other=rep.other)
    except Exception as e:
        others = _fetch_other_agents_with_fallback()
        with _cache_lock:
            if others:
                _cache["other"] = others
                report = _cache.get("report")
                if isinstance(report, UsageReport):
                    _cache["report"] = report.model_copy(update={"other": others})
            _cache["error"] = f"{type(e).__name__}: {e}"


def _refresher_loop() -> None:
    while True:
        _refresh_once()
        time.sleep(REFRESH_INTERVAL_SEC)


_refresher_started = False


def _start_refresher() -> None:
    global _refresher_started, _pet_idle_started_at
    if _refresher_started:
        _start_codex_log_monitor()
        _start_mouse_monitor()
        return
    _refresher_started = True
    with _pet_cond:
        if _pet_state.state == "idle" and _pet_state.updated_at is not None:
            _pet_idle_started_at = _pet_state.updated_at
            _pet_schedule_sleep_timer_locked(_pet_state.model_copy())
    threading.Thread(target=_refresher_loop, name="usage-refresher", daemon=True).start()
    _start_codex_log_monitor()
    _start_mouse_monitor()


def _mock_report() -> UsageReport:
    now = datetime.now(timezone.utc)
    return UsageReport(
        updated_at=now,
        source="mock",
        claude=ClaudeUsage(
            today=Bucket(tokens_used=382_000, cost_usd=9.14),
            month=Bucket(tokens_used=8_400_000, cost_usd=187.22),
            lifetime=Bucket(tokens_used=18_200_000, cost_usd=214.07),
            by_model=[
                ModelBreakdown(model="claude-opus-4-7", tokens=12_900_000, cost_usd=180.00),
                ModelBreakdown(model="claude-sonnet-4-6", tokens=4_400_000, cost_usd=28.00),
                ModelBreakdown(model="claude-haiku-4-5", tokens=900_000, cost_usd=6.07),
            ],
        ),
        other=[
            OtherAgentUsage(
                agent="codex",
                today=Bucket(tokens_used=124_000, cost_usd=0.31),
                month=Bucket(tokens_used=1_800_000, cost_usd=4.40),
                lifetime=Bucket(tokens_used=5_200_000, cost_usd=11.90),
            ),
        ],
        weather=Weather(temp_c=24.3, code=2, condition="Partly", icon="partly", city="SHENZHEN"),
        deepseek=DeepSeek(balance=70.79, currency="CNY", granted=0.0, topped=70.79,
                          today_tokens=2_400_000, available=True),
        pet=PetState(state="thinking", agent="mock", event="UserPromptSubmit",
                     sessions=1, asset=_pet_asset_for("thinking"), updated_at=now),
        codexradar=CodexRadar(
            updated_at="mock",
            available=True,
            points=[
                RadarPoint(model="sol", effort="ultra", iq=92.4, price=27.59, minutes=55, passed=69, tasks=112),
                RadarPoint(model="sol", effort="max", iq=103.1, price=8.90, minutes=34, passed=77, tasks=112),
                RadarPoint(model="sol", effort="xhigh", iq=92.4, price=6.92, minutes=28, passed=69, tasks=112),
                RadarPoint(model="terra", effort="ultra", iq=95.1, price=14.60, minutes=43, passed=71, tasks=112),
                RadarPoint(model="terra", effort="max", iq=95.1, price=4.74, minutes=30, passed=71, tasks=112),
                RadarPoint(model="terra", effort="xhigh", iq=76.3, price=2.53, minutes=19, passed=57, tasks=112),
                RadarPoint(model="luna", effort="max", iq=93.8, price=2.39, minutes=32, passed=70, tasks=112),
                RadarPoint(model="luna", effort="xhigh", iq=67.0, price=1.54, minutes=22, passed=50, tasks=112),
            ],
        ),
    )

@app.get("/healthz")
def healthz():
    ts = float(_cache.get("ts", 0.0) or 0)
    return {
        "ok": True,
        "ready": ts > 0,
        "cache_age_sec": int(time.time() - ts) if ts > 0 else None,
    }


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(SIM_PATH)


@app.get("/sim", include_in_schema=False)
def simulator():
    return FileResponse(SIM_PATH)


@app.get("/sim/assets/{asset_name}", include_in_schema=False)
def simulator_asset(asset_name: str):
    allowed = {"claudecode.svg", "deepseek.svg", "codex.svg"}
    if asset_name not in allowed:
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(ASSET_DIR / asset_name, media_type="image/svg+xml")


@app.get("/sim/clawd/{asset_name}", include_in_schema=False)
def simulator_clawd_asset(asset_name: str):
    allowed = set(PET_ASSET_TO_RLCD_GIF)
    if asset_name not in allowed:
        raise HTTPException(status_code=404, detail="asset not found")
    rlcd_gif = PET_ASSET_TO_RLCD_GIF.get(asset_name)
    if rlcd_gif:
        gif_path = CLAWD_RLCD_GIF_DIR / rlcd_gif
        if gif_path.exists():
            return FileResponse(gif_path, media_type="image/gif", headers={"Cache-Control": "no-store"})
    return FileResponse(CLAWD_SVG_DIR / asset_name, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


def _check_auth(token_header: str | None, token_query: str | None) -> None:
    if AUTH_TOKEN is None:
        return
    presented = token_header or (token_query if ALLOW_QUERY_TOKEN else None)
    if presented != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing token")


def _host_is_loopback(host: str) -> bool:
    value = (host or "").strip().strip("[]").lower()
    if value in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _validated_bind_host() -> str:
    host = os.environ.get("RLCD_HOST") or "127.0.0.1"
    if AUTH_TOKEN is None and not _host_is_loopback(host):
        raise SystemExit(
            "Refusing to bind RLCD bridge beyond loopback without RLCD_AUTH_TOKEN. "
            "Set RLCD_AUTH_TOKEN or use RLCD_HOST=127.0.0.1."
        )
    return host


@app.get("/api/pet/state")
def get_pet_state(
    token: str | None = Query(None),
    x_rlcd_token: str | None = Header(None),
    wait: float = Query(0),
    since: str | None = Query(None),
):
    _check_auth(x_rlcd_token, token)
    wait = max(0.0, min(float(wait or 0), 30.0))
    deadline = time.time() + wait
    with _pet_cond:
        while wait > 0:
            state = _pet_state.model_copy()
            stamp = _pet_stamp(state)
            if not since or stamp != since:
                break
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            _pet_cond.wait(timeout=remaining)
    return _get_pet_state().model_dump(mode="json")


@app.post("/api/pet/state")
def post_pet_state(
    data: dict[str, object],
    token: str | None = Query(None),
    x_rlcd_token: str | None = Header(None),
):
    _check_auth(x_rlcd_token, token)
    return _apply_pet_event(data).model_dump(mode="json")


@app.get("/api/usage")
def get_usage(
    mock: int = Query(0),
    token: str | None = Query(None),
    weather_lat: float | None = Query(None),
    weather_lon: float | None = Query(None),
    weather_city: str | None = Query(None),
    x_rlcd_token: str | None = Header(None),
):
    _check_auth(x_rlcd_token, token)
    if (weather_lat is None) != (weather_lon is None):
        raise HTTPException(status_code=400, detail="weather_lat and weather_lon must be provided together")
    if mock:
        payload = _mock_report().model_dump(mode="json")
        payload["pet"] = _get_pet_state().model_dump(mode="json")
        if weather_lat is not None or weather_city:
            weather = fetch_weather(weather_lat, weather_lon, weather_city)
            if weather is not None:
                payload["weather"] = weather.model_dump(mode="json")
        return payload
    rep, err = _get_cached()
    if rep is None:
        return JSONResponse(
            status_code=503,
            content={"error": err or "no data yet", "hint": "is ccusage installed and is ~/.claude populated?"},
        )
    payload = rep.model_dump(mode="json")
    payload["pet"] = _get_pet_state().model_dump(mode="json")
    if weather_lat is not None or weather_city:
        weather, pending = _get_weather_override(weather_lat, weather_lon, weather_city)
        if weather is not None:
            payload["weather"] = weather.model_dump(mode="json")
        payload["weather_pending"] = pending
    if err:
        payload["stale"] = True
        payload["error"] = err
    return payload


def main():
    import sys
    import uvicorn

    # Under pythonw.exe (Task Scheduler has no console), sys.stdout/stderr are
    # None and uvicorn's StreamHandler crashes on first log write. Redirect to
    # files so the daemon survives and leaves a trail; noop when stdout exists.
    if sys.stdout is None or sys.stderr is None:
        _log_dir = Path(__file__).resolve().parent
        if sys.stdout is None:
            sys.stdout = open(_log_dir / "bridge-server.out.log", "a", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(_log_dir / "bridge-server.err.log", "a", encoding="utf-8")

    host = _validated_bind_host()
    port = int(os.environ.get("RLCD_PORT", "7777"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
