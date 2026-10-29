"""A scripted OpenAI-compatible model for end-to-end tests (no network, no weights).

  python3 fake-model.py PORT SCRIPT_JSON

SCRIPT_JSON is a list of turns. Each request to /v1/chat/completions consumes the next turn:
  {"tool_call": {"name": "mcp_files_list", "arguments": {...}}}   -> one tool call
  {"text": "Here are your files."}                                   -> a final answer
When the script runs out, the model answers "done". Both streaming (SSE) and plain JSON
responses are supported, which is all Hermes's OpenAI-compatible client needs. Every request is
appended to requests.jsonl next to the script, so a test can assert what reached the model.
"""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(sys.argv[1])
SCRIPT_PATH = Path(sys.argv[2])
LOG = SCRIPT_PATH.with_name("requests.jsonl")
_lock = threading.Lock()
_turn = 0


def next_turn():
    global _turn
    with _lock:
        script = json.loads(SCRIPT_PATH.read_text())
        turn = script[_turn] if _turn < len(script) else {"text": "done"}
        _turn += 1
        return turn


def completion(turn, model):
    message = {"role": "assistant", "content": None}
    finish = "stop"
    if "tool_call" in turn:
        call = turn["tool_call"]
        message["tool_calls"] = [{
            "id": f"call_{int(time.time() * 1000)}", "type": "function",
            "function": {"name": call["name"], "arguments": json.dumps(call.get("arguments", {}))},
        }]
        finish = "tool_calls"
    else:
        message["content"] = turn.get("text", "")
    return {
        "id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("fake-model: " + fmt % args + "\n")

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": "fake", "object": "model", "owned_by": "test"}]})
        if self.path.startswith("/api/tags"):  # Ollama probe
            return self._json(200, {"models": [{"name": "fake", "model": "fake"}]})
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        with _lock:
            with LOG.open("a") as fh:
                fh.write(json.dumps({"path": self.path, "body": body}) + "\n")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": {"message": "not found"}})
        # Auxiliary calls (session titles, compression) carry no tools: answer them without
        # consuming the script, so the script describes only the agent's own turns.
        turn = next_turn() if body.get("tools") else {"text": "Test session"}
        result = completion(turn, body.get("model", "fake"))
        if not body.get("stream"):
            return self._json(200, result)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        msg = result["choices"][0]["message"]
        delta = {"role": "assistant"}
        if msg.get("tool_calls"):
            delta["tool_calls"] = [{**msg["tool_calls"][0], "index": 0}]
        else:
            delta["content"] = msg["content"]
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": result["choices"][0]["finish_reason"]}],
             "usage": result["usage"]},
        ]
        for chunk in chunks:
            chunk.update(id="chatcmpl-fake", object="chat.completion.chunk", created=result["created"], model=result["model"])
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
