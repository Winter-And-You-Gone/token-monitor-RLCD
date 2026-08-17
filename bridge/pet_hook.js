#!/usr/bin/env node
// Lightweight RLCD pet state hook.
// Usage:
//   node bridge/pet_hook.js UserPromptSubmit
//   node bridge/pet_hook.js --state working --agent codex
//
// Reads optional JSON from stdin and posts a compact event/state payload to
// the local bridge. This intentionally stays small; Clawd-on-Desk remains the
// reference for richer desktop behavior.

const http = require("node:http");
const https = require("node:https");
const fs = require("node:fs");
const path = require("node:path");

// Bridge auth is mandatory when the bridge binds 0.0.0.0 (see bridge/.env).
// Claude Code spawns this hook as a child process that does NOT inherit
// bridge/.env, so without a system-level RLCD_AUTH_TOKEN every state POST is
// rejected with 401 and the device pet never leaves idle. Fall back to reading
// bridge/.env (sibling of this file) for RLCD_AUTH_TOKEN / RLCD_BRIDGE_URL so
// the hook works without extra environment setup.
function loadBridgeEnv() {
  if (process.env.RLCD_AUTH_TOKEN && process.env.RLCD_BRIDGE_URL) return;
  let envPath;
  try {
    envPath = path.join(__dirname, ".env");
  } catch {
    return;
  }
  let text;
  try {
    text = fs.readFileSync(envPath, "utf8");
  } catch {
    return;
  }
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    if (key === "RLCD_AUTH_TOKEN" && !process.env.RLCD_AUTH_TOKEN) {
      process.env.RLCD_AUTH_TOKEN = val;
    } else if (key === "RLCD_BRIDGE_URL" && !process.env.RLCD_BRIDGE_URL) {
      process.env.RLCD_BRIDGE_URL = val;
    }
  }
}
loadBridgeEnv();

const EVENT_TO_STATE = {
  SessionStart: "idle",
  SessionEnd: "sleeping",
  UserPromptSubmit: "thinking",
  PreToolUse: "working",
  PostToolUse: "working",
  PostToolUseFailure: "error",
  Stop: "attention",
  StopFailure: "error",
  ApiError: "error",
  SubagentStart: "juggling",
  SubagentStop: "working",
  PreCompact: "sweeping",
  PostCompact: "attention",
  Notification: "notification",
  PermissionRequest: "notification",
  Elicitation: "notification",
  WorktreeCreate: "carrying",
  waking: "waking",
  yawning: "yawning",
  dozing: "dozing",
  collapsing: "collapsing",
  carrying: "carrying",
  sweeping: "sweeping",
};

const CODEX_EVENT_TO_STATE = {
  SessionStart: "idle",
  UserPromptSubmit: "thinking",
  PreToolUse: "working",
  PermissionRequest: "notification",
  PostToolUse: "working",
  Stop: "codex-turn-end",
  Notification: "notification",
  SubagentStart: "juggling",
  SubagentStop: "working",
  "event_msg:context_compacted": "sweeping",
  "event_msg:turn_aborted": "idle",
  "event_msg:task_complete": "codex-turn-end",
  "session_meta": "idle",
};

const ANTIGRAVITY_EVENT_TO_STATE = {
  PreInvocation: "thinking",
  PreToolUse: "working",
  PostToolUse: "working",
  PostInvocation: "idle",
  Stop: "attention",
};

const DSH_EVENT_TO_STATE = {
  SessionStart: "idle",
  UserPromptSubmit: "thinking",
  PreToolUse: "working",
  PostToolUse: "working",
  PostToolUseFailure: "error",
  Stop: "codex-turn-end",
  SubagentStart: "juggling",
  SubagentStop: "working",
};

const VALID_STATES = new Set([
  "idle",
  "yawning",
  "dozing",
  "collapsing",
  "thinking",
  "working",
  "juggling",
  "sweeping",
  "error",
  "attention",
  "notification",
  "completed",
  "carrying",
  "sleeping",
  "waking",
]);
const SPECIAL_STATES = new Set(["codex-turn-end"]);

function argValue(name) {
  const idx = process.argv.indexOf(name);
  if (idx < 0) return "";
  const value = process.argv[idx + 1] || "";
  return value.startsWith("--") ? "" : value;
}

function positionalEventArg() {
  const valueFlags = new Set(["--event", "--state", "--agent", "--sessions", "--subagents"]);
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (!arg) continue;
    if (arg.startsWith("--")) {
      if (valueFlags.has(arg)) i += 1;
      continue;
    }
    return arg;
  }
  return "";
}

function hasPayloadError(payload) {
  if (!payload || typeof payload !== "object") return false;
  const error = payload.error;
  return error !== undefined && error !== null && error !== false && error !== "";
}

function hasStopError(payload) {
  if (hasPayloadError(payload)) return true;
  const reason = payload && typeof payload.terminationReason === "string"
    ? payload.terminationReason.toLowerCase()
    : "";
  return reason.includes("error") || reason.includes("failed") || reason.includes("failure");
}

function isAntigravityAgent(agent) {
  if (typeof agent !== "string") return false;
  const normalized = agent.trim().toLowerCase();
  return normalized === "ag"
    || normalized === "agy"
    || normalized === "antigravity"
    || normalized === "antigravity-cli";
}

function normalizeAgent(agent) {
  if (typeof agent !== "string") return "";
  const normalized = agent.trim().toLowerCase();
  if (normalized === "claude" || normalized === "claude-code") return "claude-code";
  if (normalized === "codex" || normalized === "codex-cli") return "codex";
  if (normalized === "ag" || normalized === "agy" || normalized === "antigravity" || normalized === "antigravity-cli") {
    return "antigravity-cli";
  }
  if (normalized === "dsh" || normalized === "deepseek-harness" || normalized === "deepseek") return "dsh";
  return agent.trim();
}

function isCodexAgent(agent) {
  return normalizeAgent(agent) === "codex";
}

function isDshAgent(agent) {
  return normalizeAgent(agent) === "dsh";
}

function codexSessionIdFromTranscript(transcript) {
  if (typeof transcript !== "string" || !transcript.trim()) return "";
  const name = transcript.replace(/\\/g, "/").split("/").pop() || "";
  const match = name.match(/^rollout-.+-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$/i);
  return match ? match[1] : "";
}

function dshSessionIdFromTranscript(transcript) {
  if (typeof transcript !== "string" || !transcript.trim()) return "";
  const match = transcript.replace(/\\/g, "/").match(/session-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
  return match ? match[1] : "";
}

function looksLikeCodexPayload(payload) {
  if (!payload || typeof payload !== "object") return false;
  if (typeof payload.codexOriginator === "string" || typeof payload.codexSource === "string") return true;
  if (typeof payload.codex_originator === "string" || typeof payload.codex_source === "string") return true;
  return !!codexSessionIdFromTranscript(firstString(payload.transcript_path, payload.transcriptPath));
}

function looksLikeAntigravityPayload(payload) {
  return !!(payload && typeof payload === "object" && (
    typeof payload.conversationId === "string"
    || Array.isArray(payload.workspacePaths)
    || (payload.toolCall && typeof payload.toolCall === "object")
    || Object.prototype.hasOwnProperty.call(payload, "fullyIdle")
    || Object.prototype.hasOwnProperty.call(payload, "terminationReason")
    || typeof payload.artifactDirectoryPath === "string"
  ));
}

function looksLikeDshPayload(payload) {
  if (!payload || typeof payload !== "object") return false;
  const transcript = firstString(payload.transcript_path, payload.transcriptPath);
  return !!dshSessionIdFromTranscript(transcript);
}

function resolveAgent(payload) {
  const agent = firstString(
    argValue("--agent"),
    payload.agent,
    payload.agent_id,
    payload.agentId,
  );
  if (agent) return normalizeAgent(agent);
  if (looksLikeCodexPayload(payload)) return "codex";
  if (looksLikeAntigravityPayload(payload)) return "antigravity-cli";
  return looksLikeDshPayload(payload) ? "dsh" : "claude-code";
}

function resolveState(event, requestedState, payload, agent) {
  if (VALID_STATES.has(requestedState) || SPECIAL_STATES.has(requestedState)) return requestedState;
  if (isCodexAgent(agent)) {
    if (event === "Stop" && payload && payload.stop_hook_active === true) return "idle";
    if (event === "PostToolUse" && hasPayloadError(payload)) return "error";
    if (event === "Stop" && hasStopError(payload)) return "error";
    return CODEX_EVENT_TO_STATE[event] || EVENT_TO_STATE[event] || "idle";
  }
  if (isAntigravityAgent(agent) || looksLikeAntigravityPayload(payload)) {
    if (event === "PostToolUse" && hasPayloadError(payload)) return "error";
    if (event === "Stop" && hasStopError(payload)) return "error";
    if (event === "Stop" && payload && payload.fullyIdle === false) return "working";
    return ANTIGRAVITY_EVENT_TO_STATE[event] || EVENT_TO_STATE[event] || "idle";
  }
  if (isDshAgent(agent)) {
    if (event === "PostToolUse" && hasPayloadError(payload)) return "error";
    if (event === "Stop" && hasStopError(payload)) return "error";
    return DSH_EVENT_TO_STATE[event] || EVENT_TO_STATE[event] || "idle";
  }
  return EVENT_TO_STATE[event] || "idle";
}

function readStdin() {
  return new Promise((resolve) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => { data += chunk; });
    process.stdin.on("end", () => resolve(data));
    if (process.stdin.isTTY) resolve("");
  });
}

function firstString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function resolveSessionId(payload, agent) {
  const source = payload && typeof payload === "object" ? payload : {};
  const direct = firstString(source.session_id, source.sessionId);
  const transcript = firstString(source.transcript_path, source.transcriptPath);
  if (isCodexAgent(agent)) {
    const raw = codexSessionIdFromTranscript(transcript) || direct || "default";
    return raw.startsWith("codex:") ? raw : `codex:${raw}`;
  }
  if (isAntigravityAgent(agent)) {
    const conversation = firstString(source.conversationId, source.conversation_id);
    const raw = direct || conversation || transcript.replace(/\\/g, "/").split("/").slice(-2, -1)[0] || "default";
    return raw.startsWith("antigravity:") ? raw : `antigravity:${raw}`;
  }
  if (isDshAgent(agent)) {
    // Normalize away a "session-" prefix so hook payloads and the bridge's
    // JSONL poller (keyed by the session-<uuid> dir name) share one record.
    const raw = firstString(direct, dshSessionIdFromTranscript(transcript)) || "default";
    const stripped = raw.startsWith("session-") ? raw.slice("session-".length) : raw;
    return stripped.startsWith("dsh:") ? stripped : `dsh:${stripped}`;
  }
  if (direct) return direct;
  const conversation = firstString(source.conversationId, source.conversation_id);
  if (conversation) return conversation.startsWith("antigravity:") ? conversation : `antigravity:${conversation}`;
  return `${agent || "agent"}:default`;
}

function postJson(url, body, token) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const data = Buffer.from(JSON.stringify(body));
    const transport = parsed.protocol === "https:" ? https : http;
    const req = transport.request(parsed, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": String(data.length),
        ...(token ? { "X-RLCD-Token": token } : {}),
      },
      timeout: 2500,
    }, (res) => {
      res.resume();
      res.on("end", () => {
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) resolve();
        else reject(new Error(`bridge returned HTTP ${res.statusCode}`));
      });
    });
    req.on("error", reject);
    req.on("timeout", () => req.destroy(new Error("bridge request timed out")));
    req.end(data);
  });
}

(async () => {
  const eventArg = positionalEventArg();
  const stdinText = await readStdin();
  let payload = {};
  try {
    payload = stdinText.trim() ? JSON.parse(stdinText) : {};
  } catch {
    payload = {};
  }

  const event = firstString(
    argValue("--event"),
    eventArg,
    payload.hook_event_name,
    payload.hookEventName,
    payload.event,
    payload.event_name,
    payload.eventName,
  );
  const agent = resolveAgent(payload);
  const requestedState = firstString(argValue("--state"), payload.state);
  const state = resolveState(event, requestedState, payload, agent);

  const body = {
    state,
    event,
    agent,
    session_id: resolveSessionId(payload, agent),
    sessions: Number(argValue("--sessions") || payload.sessions || payload.session_count || 0),
    subagents: Number(argValue("--subagents") || payload.subagents || payload.subagent_count || 0),
  };
  if (payload && typeof payload === "object") {
    if (payload.stop_hook_active === true) body.stop_hook_active = true;
    if (typeof payload.terminationReason === "string") body.terminationReason = payload.terminationReason;
    if (payload.error !== undefined && payload.error !== null && payload.error !== false && payload.error !== "") {
      body.error = payload.error;
    }
    if (typeof payload.headless === "boolean") body.headless = payload.headless;
  }

  const base = process.env.RLCD_BRIDGE_URL || "http://127.0.0.1:7777";
  const url = new URL("/api/pet/state", base).toString();
  await postJson(url, body, process.env.RLCD_AUTH_TOKEN || "");
})().catch((err) => {
  console.error(`[rlcd-pet-hook] ${err.message}`);
  process.exitCode = process.env.RLCD_PET_HOOK_STRICT === "1" ? 1 : 0;
});
