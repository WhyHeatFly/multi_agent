from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .service import DemandAnalysisService


SERVICE = DemandAnalysisService()


class DemandAgentHandler(BaseHTTPRequestHandler):
    server_version = "DemandAnalystAgent/0.1"

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            path = urlparse(self.path).path
            parts = [part for part in path.split("/") if part]

            if path == "/v1/agents/demand-analysis/tasks":
                user_input = payload.get("user_input")
                if not user_input:
                    self._json({"error": "user_input is required"}, status=400)
                    return
                task = SERVICE.create_task(user_input=user_input, context=payload.get("context") or {})
                self._json(task.to_summary(), status=201)
                return

            if len(parts) == 6 and parts[:3] == ["v1", "agents", "demand-analysis"] and parts[3] == "tasks":
                demand_task_id = parts[4]
                action = parts[5]
                if action == "answers":
                    result = SERVICE.submit_answers(demand_task_id, payload.get("answers") or {})
                    self._json(result)
                    return
                if action == "handoff":
                    result = SERVICE.handoff(demand_task_id, payload.get("target_agents") or [])
                    self._json(result)
                    return

            self._json({"error": "not found"}, status=404)
        except KeyError as exc:
            self._json({"error": str(exc)}, status=404)
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, status=400)

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            parts = [part for part in path.split("/") if part]
            if len(parts) == 6 and parts[:3] == ["v1", "agents", "demand-analysis"] and parts[3] == "tasks":
                demand_task_id = parts[4]
                action = parts[5]
                if action == "questions":
                    self._json(SERVICE.get_questions(demand_task_id))
                    return
                if action == "report":
                    self._json(SERVICE.get_report(demand_task_id))
                    return
            self._json({"error": "not found"}, status=404)
        except KeyError as exc:
            self._json({"error": str(exc)}, status=404)

    def log_message(self, format: str, *args) -> None:
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), DemandAgentHandler)
    print(f"Demand Analyst Agent API listening on http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Demand Analyst Agent MVP API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    run(args.host, args.port)


if __name__ == "__main__":
    main()
