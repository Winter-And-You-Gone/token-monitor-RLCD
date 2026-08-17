from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / "bridge" / "pet_hook.js"


class _HookCaptureHandler(BaseHTTPRequestHandler):
    posts: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8")
        self.__class__.posts.append(json.loads(body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format: str, *args: object) -> None:
        return


class PetHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("node") is None:
            raise unittest.SkipTest("node is not available")

    def setUp(self) -> None:
        _HookCaptureHandler.posts = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _HookCaptureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def run_hook(self, args: list[str], payload: dict[str, object] | None = None) -> dict[str, object]:
        env = {
            **os.environ,
            "RLCD_BRIDGE_URL": f"http://127.0.0.1:{self.server.server_port}",
            "RLCD_PET_HOOK_STRICT": "1",
        }
        result = subprocess.run(
            ["node", str(HOOK_PATH), *args],
            input=json.dumps(payload or {}),
            text=True,
            capture_output=True,
            env=env,
            cwd=str(REPO_ROOT),
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(_HookCaptureHandler.posts), 1)
        return _HookCaptureHandler.posts[0]

    def test_antigravity_pre_invocation_is_thinking(self) -> None:
        body = self.run_hook(["PreInvocation"], {"conversationId": "c1"})

        self.assertEqual(body["state"], "thinking")
        self.assertEqual(body["event"], "PreInvocation")
        self.assertEqual(body["agent"], "antigravity-cli")

    def test_antigravity_post_tool_use_stays_working(self) -> None:
        body = self.run_hook(["PostToolUse", "--agent", "antigravity-cli"])

        self.assertEqual(body["state"], "working")

    def test_antigravity_post_tool_use_error(self) -> None:
        body = self.run_hook(["PostToolUse"], {
            "agent_id": "antigravity-cli",
            "error": "tool failed",
        })

        self.assertEqual(body["state"], "error")

    def test_generic_post_tool_use_stays_working(self) -> None:
        body = self.run_hook(["PostToolUse"], {"agent": "claude"})

        self.assertEqual(body["state"], "working")
        self.assertEqual(body["agent"], "claude-code")

    def test_codex_stop_uses_turn_end_and_transcript_session(self) -> None:
        body = self.run_hook(["Stop"], {
            "hook_event_name": "Stop",
            "transcript_path": (
                "C:/Users/<user>/.codex/sessions/2026/05/31/"
                "rollout-2026-05-31T12-00-00-123e4567-e89b-12d3-a456-426614174000.jsonl"
            ),
        })

        self.assertEqual(body["state"], "codex-turn-end")
        self.assertEqual(body["agent"], "codex")
        self.assertEqual(body["session_id"], "codex:123e4567-e89b-12d3-a456-426614174000")

    def test_dsh_events_map_via_cc_names(self) -> None:
        prompt = self.run_hook(["UserPromptSubmit", "--agent", "dsh"])
        _HookCaptureHandler.posts = []
        tool = self.run_hook(["PreToolUse", "--agent", "dsh"])
        _HookCaptureHandler.posts = []
        failure = self.run_hook(["PostToolUseFailure", "--agent", "dsh"])

        self.assertEqual(prompt["state"], "thinking")
        self.assertEqual(prompt["agent"], "dsh")
        self.assertEqual(tool["state"], "working")
        self.assertEqual(failure["state"], "error")

    def test_dsh_stop_is_turn_end_and_namespaced_session(self) -> None:
        body = self.run_hook(["Stop"], {
            "session_id": "session-123e4567-e89b-12d3-a456-426614174000",
            "transcript_path": (
                "C:/Users/<user>/.dsh/sessions/--X-workspace--/"
                "session-123e4567-e89b-12d3-a456-426614174000/session.jsonl.zstd"
            ),
        })

        self.assertEqual(body["state"], "codex-turn-end")
        self.assertEqual(body["agent"], "dsh")
        self.assertEqual(body["session_id"], "dsh:123e4567-e89b-12d3-a456-426614174000")

    def test_dsh_payload_sniffed_from_transcript(self) -> None:
        body = self.run_hook(["PostToolUse"], {
            "transcript_path": (
                "C:/Users/<user>/.dsh/sessions/--X-workspace--/"
                "session-123e4567-e89b-12d3-a456-426614174000/session.jsonl.zstd"
            ),
        })

        self.assertEqual(body["agent"], "dsh")
        self.assertEqual(body["session_id"], "dsh:123e4567-e89b-12d3-a456-426614174000")


if __name__ == "__main__":
    unittest.main()
