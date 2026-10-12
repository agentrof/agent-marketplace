#!/usr/bin/env python3
"""Exercise native recovery against a loopback-only model API with fixture keys."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import http.server
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import uuid


class FixtureAPI(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if "count_tokens" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"input_tokens":1}')
            return
        content, failed = self.server.reply(body)
        if failed:
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"type":"error","error":{"type":"overloaded_error","message":"temporary fixture failure"}}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        tool = content["type"] == "tool_use"
        events = [
            {"type": "message_start", "message": {"id": "msg_" + uuid.uuid4().hex, "type": "message",
                "role": "assistant", "model": "fixture", "content": [], "stop_reason": None,
                "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0, "content_block":
                {**content, "input": {}} if tool else {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta":
                {"type": "input_json_delta", "partial_json": json.dumps(content["input"])} if tool else
                {"type": "text_delta", "text": content["text"]}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use" if tool else "end_turn",
                "stop_sequence": None}, "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]
        for event in events:
            self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
            self.wfile.flush()


def check(plugin: Path, scenario: str) -> dict:
    requests, child_calls = [], []
    phase = 0
    state = None
    lock = threading.Lock()

    def reply(body):
        nonlocal phase
        with lock:
            requests.append(body)
            text = json.dumps(body.get("messages", []))
            content = {"type": "text", "text": "recovered"}
            if scenario == "main":
                return content, len(requests) <= 2
            if "Start fixture child." not in text:
                child_calls.append(body)
                return {"type": "text", "text": "child recovered"}, len(child_calls) == 1
            if phase == 0:
                phase = 1
                name = next(tool["name"] for tool in body["tools"] if tool["name"] in ("Agent", "Task"))
                return {"type": "tool_use", "id": "tool_fixture_agent", "name": name, "input": {
                    "description": "Fixture child", "prompt": "Native child fixture: reply child recovered.",
                    "subagent_type": "general-purpose", "run_in_background": True}}, False
            if phase == 1:
                phase = 2
            elif phase == 2 and "AUTOPILOT_TRANSPORT_RECOVERY" in text:
                stored = json.loads((state / "native-recovery.json").read_text(encoding="utf-8"))
                permit = next(value for value in stored["permits"].values() if not value["consumed"])
                phase = 3
                return {"type": "tool_use", "id": "tool_fixture_resume", "name": "SendMessage", "input": {
                    "to": permit["thread_id"], "message": permit["message"]}}, False
            return {"type": "text", "text": "waiting"}, False

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FixtureAPI)
    server.reply = reply
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="native-recovery-smoke-") as temporary:
            root = Path(temporary).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            (root / ".gitignore").write_text("/.agentrof/\n/config/\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c",
                            "user.email=fixture@example.invalid", "commit", "-qm", "Fixture"], check=True, capture_output=True)
            owner = str(uuid.uuid4())
            state = root / ".agentrof/agent-marketplace/.runtime/autopilot"
            state.mkdir(parents=True)
            now = datetime.now(timezone.utc)
            grant = {"schema_version": 1, "id": "fixture-grant", "host": "claude", "state": "active",
                     "granted_at": now.isoformat(), "expires_at": (now + timedelta(minutes=2)).isoformat(),
                     "classes": [], "goal": None, "transport_recovery": True,
                     "armed_by": {"guard": "user_prompt_hook", "session_id": owner}}
            (state / "grant.json").write_text(json.dumps(grant), encoding="utf-8")
            environment = dict(os.environ, ANTHROPIC_API_KEY="fixture-only-not-a-real-key",
                ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
                CLAUDE_CONFIG_DIR=str(root / "config"), CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                DISABLE_TELEMETRY="1", CLAUDE_CODE_MAX_RETRIES="0", PYTHONDONTWRITEBYTECODE="1")
            process = subprocess.Popen(["claude", "-p", "--input-format", "stream-json", "--output-format",
                "stream-json", "--verbose", "--session-id", owner, "--setting-sources", "", "--plugin-dir",
                str(plugin), "--model", "fixture", "--permission-mode", "default",
                "--allowedTools", "Agent,Task,SendMessage"], cwd=root, env=environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
            messages = queue.Queue()

            def read(stream):
                for line in stream:
                    messages.put(line)

            for stream in (process.stdout, process.stderr):
                threading.Thread(target=read, args=(stream,), daemon=True).start()
            recovered = observed_error = False
            child_id = resumed_id = None
            last_error = ""
            try:
                prompt = "Start fixture child." if scenario == "child" else "Reply recovered."
                process.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
                process.stdin.flush()
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        event = json.loads(messages.get(timeout=0.25))
                    except (queue.Empty, ValueError):
                        continue
                    observed_error |= event.get("is_api_error_message") is True or bool(event.get("is_error"))
                    if event.get("subtype") == "task_started":
                        child_id = event["task_id"]
                    result = event.get("tool_use_result")
                    if isinstance(result, str):
                        last_error = result[:1200]
                    if isinstance(result, dict) and result.get("resumedAgentId"):
                        resumed_id = result["resumedAgentId"]
                    if event.get("type") == "assistant" and "recovered" in json.dumps(event.get("message", {})):
                        if event.get("session_id") != owner:
                            raise RuntimeError("native recovery started a different session")
                        if scenario == "main" or event.get("parent_tool_use_id"):
                            recovered = True
                            break
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if not observed_error or not recovered:
                raise RuntimeError(f"native {scenario} recovery failed (requests={len(requests)}, recovered={recovered}): {last_error}")
            stored = json.loads((state / "native-recovery.json").read_text(encoding="utf-8"))
            session = owner if scenario == "main" else child_id
            attempts = stored["attempts"]["fixture-grant:" + session]["count"]
            if scenario == "main" and (attempts != 2 or len(requests) != 3):
                raise RuntimeError("main recovery did not record exactly two retries")
            if scenario == "child" and (attempts != 1 or child_id != resumed_id or len(child_calls) != 2):
                raise RuntimeError("child recovery did not preserve its original agent identity")
            return {"scenario": scenario, "requests": len(requests), "same_session": True, "retries": attempts}
    finally:
        server.shutdown()
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", required=True, type=Path)
    parser.add_argument("--scenario", choices=("all", "main", "child"), default="all")
    args = parser.parse_args()
    scenarios = ("main", "child") if args.scenario == "all" else (args.scenario,)
    print(json.dumps({"native_recovery": "passed", "results": [check(args.plugin_root.resolve(), item)
                                                               for item in scenarios]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
