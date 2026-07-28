from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

import bridge  # noqa: E402


class PetStateMappingTests(unittest.TestCase):
    def test_generic_post_tool_use_stays_working(self) -> None:
        state = bridge._pet_state_from_event({"event": "PostToolUse", "agent": "claude"})

        self.assertEqual(state.state, "working")
        self.assertEqual(state.agent, "claude-code")

    def test_antigravity_post_tool_use_stays_working(self) -> None:
        state = bridge._pet_state_from_event({"event": "PostToolUse", "agent_id": "antigravity-cli"})

        self.assertEqual(state.state, "working")
        self.assertEqual(state.agent, "antigravity-cli")

    def test_antigravity_post_tool_use_error(self) -> None:
        state = bridge._pet_state_from_event({
            "event": "PostToolUse",
            "agentId": "ag",
            "error": "tool failed",
        })

        self.assertEqual(state.state, "error")

    def test_antigravity_invocation_events_are_supported(self) -> None:
        start = bridge._pet_state_from_event({
            "hookEventName": "PreInvocation",
            "conversationId": "c1",
        })
        done = bridge._pet_state_from_event({
            "eventName": "PostInvocation",
            "conversationId": "c1",
        })

        self.assertEqual(start.state, "thinking")
        self.assertEqual(start.agent, "antigravity-cli")
        self.assertEqual(done.state, "idle")

    def test_antigravity_stop_distinguishes_background_work(self) -> None:
        working = bridge._pet_state_from_event({
            "event": "Stop",
            "agent_id": "antigravity-cli",
            "fullyIdle": False,
        })
        idle = bridge._pet_state_from_event({
            "event": "Stop",
            "agent_id": "antigravity-cli",
            "fullyIdle": True,
        })
        generic = bridge._pet_state_from_event({"event": "Stop", "agent": "claude"})

        self.assertEqual(working.state, "working")
        self.assertEqual(idle.state, "idle")
        self.assertEqual(generic.state, "idle")

    def test_explicit_state_still_wins_for_antigravity_payloads(self) -> None:
        state = bridge._pet_state_from_event({
            "state": "carrying",
            "event": "PostToolUse",
            "agent_id": "antigravity-cli",
        })

        self.assertEqual(state.state, "carrying")

    def test_codex_turn_end_is_internal_state(self) -> None:
        state = bridge._pet_state_from_event({
            "state": "codex-turn-end",
            "event": "Stop",
            "agent": "codex",
        })

        self.assertEqual(state.state, "codex-turn-end")

    def test_codex_context_compacted_maps_to_sweeping(self) -> None:
        state = bridge._pet_state_from_event({
            "event": "event_msg:context_compacted",
            "agent": "codex",
        })

        self.assertEqual(state.state, "sweeping")
        self.assertEqual(state.agent, "codex")

    def test_codex_jsonl_context_compacted_line_posts_sweeping(self) -> None:
        events: list[dict[str, object]] = []
        entry = bridge._CodexLogEntry(offset=0, session_id="session-1")
        line = json.dumps({"type": "event_msg", "payload": {"type": "context_compacted"}})

        applied = bridge._codex_process_jsonl_line(line, entry, apply_event=events.append)

        self.assertTrue(applied)
        self.assertEqual(events, [{
            "state": "sweeping",
            "event": "event_msg:context_compacted",
            "agent": "codex",
            "session_id": "session-1",
        }])

    def test_codex_jsonl_poll_skips_history_then_reads_appends(self) -> None:
        events: list[dict[str, object]] = []
        bridge._codex_log_entries.clear()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                session_dir = bridge._codex_session_dirs(root)[0]
                session_dir.mkdir(parents=True)
                rollout = session_dir / (
                    "rollout-2026-06-03T12-00-00-"
                    "123e4567-e89b-12d3-a456-426614174000.jsonl"
                )
                old_line = json.dumps({"type": "event_msg", "payload": {"type": "context_compacted"}})
                rollout.write_text(old_line + "\n", encoding="utf-8")

                bridge._codex_log_poll(root=root, bootstrap=True, apply_event=events.append)
                self.assertEqual(events, [])

                new_line = json.dumps({"type": "event_msg", "payload": {"type": "context_compacted"}})
                with rollout.open("a", encoding="utf-8") as fh:
                    fh.write(new_line + "\n")

                applied = bridge._codex_log_poll(root=root, apply_event=events.append)

                self.assertEqual(applied, 1)
                self.assertEqual(events[0]["state"], "sweeping")
                self.assertEqual(events[0]["event"], "event_msg:context_compacted")
                self.assertEqual(events[0]["agent"], "codex")
                self.assertEqual(events[0]["session_id"], "123e4567-e89b-12d3-a456-426614174000")
        finally:
            bridge._codex_log_entries.clear()

    def test_codex_transcript_session_id_is_normalized(self) -> None:
        session_id = bridge._pet_session_id({
            "transcript_path": (
                "C:/Users/Winter/.codex/sessions/2026/05/31/"
                "rollout-2026-05-31T12-00-00-123e4567-e89b-12d3-a456-426614174000.jsonl"
            ),
        }, "codex")

        self.assertEqual(session_id, "codex:123e4567-e89b-12d3-a456-426614174000")

    def test_working_assets_follow_session_tiers(self) -> None:
        self.assertEqual(bridge._pet_asset_for("working", sessions=1), "clawd-working-typing.svg")
        self.assertEqual(bridge._pet_asset_for("working", sessions=2), "clawd-headphones-groove.svg")
        self.assertEqual(bridge._pet_asset_for("working", sessions=3), "clawd-working-building.svg")
        self.assertEqual(bridge._pet_asset_for("working", sessions=4), "clawd-working-building.svg")
        self.assertEqual(bridge._pet_asset_for("juggling", subagents=1), "clawd-working-juggling.svg")


class PetRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        bridge._reset_pet_state_for_tests()
        self._hold_sec = bridge.PET_COMPLETED_HOLD_SEC
        self._sleep_enabled = bridge.PET_SLEEP_SEQUENCE_ENABLED
        self._mouse_idle_sec = bridge.PET_MOUSE_IDLE_TIMEOUT_SEC
        self._idle_look_sec = bridge.PET_IDLE_LOOK_DURATION_SEC
        self._mouse_sleep_sec = bridge.PET_MOUSE_SLEEP_TIMEOUT_SEC
        self._yawn_sec = bridge.PET_YAWN_DURATION_SEC
        self._deep_sleep_sec = bridge.PET_DEEP_SLEEP_TIMEOUT_SEC
        self._collapse_sec = bridge.PET_COLLAPSE_DURATION_SEC
        self._wake_sec = bridge.PET_WAKE_DURATION_SEC
        bridge.PET_COMPLETED_HOLD_SEC = 0
        bridge.PET_SLEEP_SEQUENCE_ENABLED = True
        bridge.PET_MOUSE_IDLE_TIMEOUT_SEC = 0.02
        bridge.PET_IDLE_LOOK_DURATION_SEC = 0.06
        bridge.PET_MOUSE_SLEEP_TIMEOUT_SEC = 0.1
        bridge.PET_YAWN_DURATION_SEC = 0.02
        bridge.PET_DEEP_SLEEP_TIMEOUT_SEC = 0.14
        bridge.PET_COLLAPSE_DURATION_SEC = 0.02
        bridge.PET_WAKE_DURATION_SEC = 0.02

    def tearDown(self) -> None:
        bridge.PET_COMPLETED_HOLD_SEC = self._hold_sec
        bridge.PET_SLEEP_SEQUENCE_ENABLED = self._sleep_enabled
        bridge.PET_MOUSE_IDLE_TIMEOUT_SEC = self._mouse_idle_sec
        bridge.PET_IDLE_LOOK_DURATION_SEC = self._idle_look_sec
        bridge.PET_MOUSE_SLEEP_TIMEOUT_SEC = self._mouse_sleep_sec
        bridge.PET_YAWN_DURATION_SEC = self._yawn_sec
        bridge.PET_DEEP_SLEEP_TIMEOUT_SEC = self._deep_sleep_sec
        bridge.PET_COLLAPSE_DURATION_SEC = self._collapse_sec
        bridge.PET_WAKE_DURATION_SEC = self._wake_sec
        bridge._reset_pet_state_for_tests()

    def wait_for_pet_state(self, expected: str, timeout: float = 1.0) -> bridge.PetState:
        deadline = time.time() + timeout
        state = bridge._get_pet_state()
        while time.time() < deadline:
            state = bridge._get_pet_state()
            if state.state == expected:
                return state
            time.sleep(0.005)
        self.fail(f"expected pet state {expected!r}, got {state.state!r}")

    def wait_for_pet_asset(self, expected: str, timeout: float = 1.0) -> bridge.PetState:
        deadline = time.time() + timeout
        state = bridge._get_pet_state()
        while time.time() < deadline:
            state = bridge._get_pet_state()
            if state.asset == expected:
                return state
            time.sleep(0.005)
        self.fail(f"expected pet asset {expected!r}, got {state.asset!r}")

    def test_idle_sleep_sequence_advances_through_all_rest_states(self) -> None:
        bridge._pet_display_state("idle", schedule_return=False)

        idle_look = self.wait_for_pet_asset("clawd-idle-reading.svg")
        self.assertEqual(idle_look.state, "idle")
        self.wait_for_pet_state("yawning")
        self.wait_for_pet_state("dozing")
        self.wait_for_pet_state("collapsing")
        state = self.wait_for_pet_state("sleeping")

        self.assertEqual(state.asset, "clawd-sleeping.svg")

    def test_mouse_activity_returns_idle_animation_to_follow(self) -> None:
        bridge._pet_display_state("idle", schedule_return=False)
        self.wait_for_pet_asset("clawd-idle-reading.svg")

        state = bridge._pet_handle_mouse_activity()

        self.assertIsNotNone(state)
        self.assertEqual(state.state, "idle")
        self.assertEqual(state.event, "mouse-move")
        self.assertEqual(state.asset, "clawd-idle-follow.svg")

    def test_mouse_activity_wakes_sleeping_pet(self) -> None:
        bridge._pet_display_state("sleeping", schedule_return=False)

        state = bridge._pet_handle_mouse_activity()

        self.assertIsNotNone(state)
        self.assertEqual(state.state, "waking")
        self.assertEqual(state.asset, "clawd-wake.svg")
        self.wait_for_pet_state("idle")

    def test_mouse_activity_does_not_interrupt_work(self) -> None:
        bridge._apply_pet_event({
            "event": "PostToolUse",
            "agent": "codex",
            "session_id": "working",
        })

        state = bridge._pet_handle_mouse_activity()

        self.assertIsNone(state)
        self.assertEqual(bridge._get_pet_state().state, "working")

    def test_sleeping_pet_wakes_before_resuming_work(self) -> None:
        bridge._pet_display_state("sleeping", schedule_return=False)

        state = bridge._apply_pet_event({
            "event": "UserPromptSubmit",
            "agent": "codex",
            "session_id": "s1",
        })
        self.assertEqual(state.state, "waking")
        self.assertEqual(state.asset, "clawd-wake.svg")

        state = self.wait_for_pet_state("thinking")
        self.assertEqual(state.agent, "codex")
    def test_active_session_priority_ignores_headless_sessions(self) -> None:
        state = bridge._apply_pet_event({
            "event": "PostToolUse",
            "agent": "claude",
            "session_id": "headless",
            "headless": True,
        })
        self.assertEqual(state.state, "idle")

        state = bridge._apply_pet_event({
            "event": "UserPromptSubmit",
            "agent": "codex",
            "session_id": "thinking",
        })
        self.assertEqual(state.state, "thinking")

        state = bridge._apply_pet_event({
            "event": "PostToolUse",
            "agent": "claude",
            "session_id": "working",
        })
        self.assertEqual(state.state, "working")

        state = bridge._apply_pet_event({
            "event": "SubagentStart",
            "agent": "claude",
            "session_id": "juggling",
        })
        self.assertEqual(state.state, "juggling")

    def test_session_end_falls_back_to_remaining_session(self) -> None:
        bridge._apply_pet_event({"event": "PostToolUse", "agent": "claude", "session_id": "s1"})
        bridge._apply_pet_event({"event": "UserPromptSubmit", "agent": "codex", "session_id": "s2"})

        state = bridge._apply_pet_event({"event": "SessionEnd", "agent": "claude", "session_id": "s1"})
        self.assertEqual(state.state, "thinking")

        state = bridge._apply_pet_event({"event": "SessionEnd", "agent": "codex", "session_id": "s2"})
        self.assertEqual(state.state, "idle")

    def test_equal_priority_uses_latest_session_agent(self) -> None:
        bridge._apply_pet_event({"event": "PostToolUse", "agent": "codex", "session_id": "codex-session"})

        state = bridge._apply_pet_event({
            "event": "PostToolUse",
            "agent": "claude",
            "session_id": "claude-session",
        })

        self.assertEqual(state.state, "working")
        self.assertEqual(state.agent, "claude-code")

    def test_subagent_juggling_holds_until_subagent_stop(self) -> None:
        bridge._apply_pet_event({"event": "PostToolUse", "agent": "claude", "session_id": "s1"})

        state = bridge._apply_pet_event({"event": "SubagentStart", "agent": "claude", "session_id": "s1"})
        self.assertEqual(state.state, "juggling")

        state = bridge._apply_pet_event({"event": "PostToolUse", "agent": "claude", "session_id": "s1"})
        self.assertEqual(state.state, "juggling")

        state = bridge._apply_pet_event({"event": "SubagentStop", "agent": "claude", "session_id": "s1"})
        self.assertEqual(state.state, "working")

    def test_codex_stop_resolves_based_on_tool_use(self) -> None:
        bridge._apply_pet_event({"event": "UserPromptSubmit", "agent": "codex", "session_id": "s1"})

        state = bridge._apply_pet_event({"event": "Stop", "agent": "codex", "session_id": "s1"})
        self.assertEqual(state.state, "idle")

        bridge._reset_pet_state_for_tests()
        bridge._apply_pet_event({"event": "PreToolUse", "agent": "codex", "session_id": "s2"})

        state = bridge._apply_pet_event({"event": "Stop", "agent": "codex", "session_id": "s2"})
        self.assertEqual(state.state, "attention")

    def test_codex_stop_with_stop_hook_active_preserves_state(self) -> None:
        bridge._apply_pet_event({"event": "PreToolUse", "agent": "codex", "session_id": "s1"})

        state = bridge._apply_pet_event({
            "event": "Stop",
            "agent": "codex",
            "session_id": "s1",
            "stop_hook_active": True,
        })
        self.assertEqual(state.state, "working")

    def test_codex_jsonl_turn_aborted_resets_to_idle(self) -> None:
        events: list[dict[str, object]] = []
        entry = bridge._CodexLogEntry(offset=0, session_id="session-1")
        line = json.dumps({"type": "event_msg", "payload": {"type": "turn_aborted"}})

        applied = bridge._codex_process_jsonl_line(line, entry, apply_event=events.append)

        self.assertTrue(applied)
        self.assertEqual(events[0]["state"], "idle")
        self.assertEqual(events[0]["event"], "event_msg:turn_aborted")

    def test_codex_jsonl_task_started_is_thinking_not_working(self) -> None:
        events: list[dict[str, object]] = []
        entry = bridge._CodexLogEntry(offset=0, session_id="session-1")
        line = json.dumps({"type": "event_msg", "payload": {"type": "task_started"}})

        applied = bridge._codex_process_jsonl_line(line, entry, apply_event=events.append)

        self.assertTrue(applied)
        self.assertEqual(events[0]["state"], "thinking")

    def test_codex_jsonl_web_search_call_is_working(self) -> None:
        events: list[dict[str, object]] = []
        entry = bridge._CodexLogEntry(offset=0, session_id="session-1")
        line = json.dumps({"type": "response_item", "payload": {"type": "web_search_call"}})

        applied = bridge._codex_process_jsonl_line(line, entry, apply_event=events.append)

        self.assertTrue(applied)
        self.assertEqual(events[0]["state"], "working")

    def test_attention_does_not_override_remaining_work(self) -> None:
        bridge._apply_pet_event({"event": "PreToolUse", "agent": "codex", "session_id": "s1"})
        bridge._apply_pet_event({"event": "PreToolUse", "agent": "claude", "session_id": "s2"})

        state = bridge._apply_pet_event({"event": "Stop", "agent": "codex", "session_id": "s1"})

        self.assertEqual(state.state, "working")
        self.assertEqual(state.asset, "clawd-working-typing.svg")

    def test_completed_does_not_override_remaining_work(self) -> None:
        bridge._apply_pet_event({"event": "PreToolUse", "agent": "codex", "session_id": "s1"})
        bridge._apply_pet_event({"event": "PreToolUse", "agent": "claude", "session_id": "s2"})

        state = bridge._apply_pet_event({
            "state": "completed",
            "event": "Stop",
            "agent": "codex",
            "session_id": "s1",
        })

        self.assertEqual(state.state, "working")
        self.assertEqual(state.asset, "clawd-working-typing.svg")


if __name__ == "__main__":
    unittest.main()
