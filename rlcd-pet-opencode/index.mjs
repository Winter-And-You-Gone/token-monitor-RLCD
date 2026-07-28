// rlcd-pet-opencode - forwards opencode session/tool events to the RLCD bridge.
//
// opencode's plugin runs in-process (Bun runtime) and sees every lifecycle
// event.  Clawd on Desk ships its own opencode-plugin that POSTs these to
// Clawd (port 23333); this sibling plugin POSTs the same events to the RLCD
// bridge (port 7777) so the RLCD pet counts opencode as an active agent.
//
// Event -> state mapping mirrors pet_hook.js (single source of truth) and
// Clawd's opencode-plugin/translateEvent.  fire-and-forget: a slow/stopped
// bridge never blocks opencode.

import { readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const AGENT_ID = "opencode";

// Read bridge auth from bridge/.env (project root is one level above this
// plugin directory).  Same env keys pet_hook.js reads.
function loadBridgeEnv() {
  const dir = dirname(fileURLToPath(import.meta.url));
  let text;
  try {
    text = readFileSync(join(dir, "..", "bridge", ".env"), "utf8");
  } catch {
    return {};
  }
  const env = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'")))
      val = val.slice(1, -1);
    env[key] = val;
  }
  return env;
}

const _env = loadBridgeEnv();
const BRIDGE_URL = _env.RLCD_BRIDGE_URL || "http://127.0.0.1:7777";
const AUTH_TOKEN = _env.RLCD_AUTH_TOKEN || "";

// Per-session dedup: skip consecutive identical states to limit POST volume.
const _lastState = new Map();

// opencode event -> { state, event } mirroring pet_hook.js EVENT_TO_STATE.
// Event shapes (from runtime observation, same as Clawd's opencode-plugin):
//   { type: "session.status", properties: { sessionID, status: { type } } }
//   { type: "message.part.updated", properties: { part: { type, tool, state: { status } } } }
function translateEvent(event) {
  if (!event || typeof event.type !== "string") return null;
  const props = event.properties || {};
  switch (event.type) {
    case "session.created":
      return { state: "idle", event: "SessionStart" };
    case "session.status":
      if (props.status && props.status.type === "busy")
        return { state: "thinking", event: "UserPromptSubmit" };
      return null;
    case "message.part.updated": {
      const part = props.part;
      if (!part || typeof part !== "object") return null;
      if (part.type === "tool") {
        const status = part.state && part.state.status;
        if (status === "running") return { state: "working", event: "PreToolUse" };
        if (status === "completed") return { state: "working", event: "PostToolUse" };
        if (status === "error") return { state: "error", event: "PostToolUseFailure" };
        return null;
      }
      if (part.type === "compaction") return { state: "sweeping", event: "PreCompact" };
      return null;
    }
    case "session.compacted":
      return { state: "sweeping", event: "PreCompact" };
    case "session.idle":
      return { state: "attention", event: "Stop" };
    case "session.error":
      return { state: "error", event: "StopFailure" };
    case "session.deleted":
    case "server.instance.disposed":
      return { state: "sleeping", event: "SessionEnd" };
    default:
      return null;
  }
}

function getSessionId(event) {
  const props = event.properties || {};
  return props.sessionID || event.sessionID || "default";
}

function postToBridge(state, eventName, sessionId) {
  const fullSid = `opencode:${sessionId}`;
  if (_lastState.get(fullSid) === state) return;
  _lastState.set(fullSid, state);

  const body = JSON.stringify({
    state,
    event: eventName,
    agent: AGENT_ID,
    session_id: fullSid,
  });
  const url = new URL("/api/pet/state", BRIDGE_URL).toString();
  const headers = { "Content-Type": "application/json" };
  if (AUTH_TOKEN) headers["X-RLCD-Token"] = AUTH_TOKEN;
  fetch(url, { method: "POST", headers, body }).catch(() => {});
}

const plugin = async () => ({
  event: async ({ event }) => {
    try {
      const mapped = translateEvent(event);
      if (!mapped) return;
      postToBridge(mapped.state, mapped.event, getSessionId(event));
    } catch {
      // fail-open: plugin errors must never interrupt opencode
    }
  },
});

export default plugin;
