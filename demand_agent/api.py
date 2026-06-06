from __future__ import annotations

import argparse
import json
import mimetypes
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .service import DemandAnalysisService


SERVICE: DemandAnalysisService | None = None
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = PROJECT_ROOT / "demo"


class DemandAgentHandler(BaseHTTPRequestHandler):
    server_version = "DemandAnalystAgent/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

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
                task = get_service().create_task(user_input=user_input, context=payload.get("context") or {})
                response = task.to_summary()
                if task.report:
                    response["report_id"] = task.report.report_id
                    response["report_files"] = get_service().storage.report_files(task.report.report_id)
                self._json(response, status=201)
                return

            if len(parts) == 6 and parts[:3] == ["v1", "agents", "demand-analysis"] and parts[3] == "tasks":
                demand_task_id = parts[4]
                action = parts[5]
                if action == "answers":
                    result = get_service().submit_answers(demand_task_id, payload.get("answers") or {})
                    self._json(result)
                    return
                if action == "handoff":
                    result = get_service().handoff(demand_task_id, payload.get("target_agents") or [])
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
            if path == "/demo" or path.startswith("/demo/"):
                self._static(path)
                return

            parts = [part for part in path.split("/") if part]
            if len(parts) == 6 and parts[:3] == ["v1", "agents", "demand-analysis"] and parts[3] == "tasks":
                demand_task_id = parts[4]
                action = parts[5]
                if action == "questions":
                    self._json(get_service().get_questions(demand_task_id))
                    return
                if action == "report":
                    self._json(get_service().get_report(demand_task_id))
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
        self._cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        if path in {"/demo", "/demo/"}:
            file_path = DEMO_DIR / "index.html"
        else:
            relative = path.removeprefix("/demo/").strip("/")
            file_path = (DEMO_DIR / relative).resolve()

        try:
            file_path.relative_to(DEMO_DIR.resolve())
        except ValueError:
            self._json({"error": "not found"}, status=404)
            return

        if not file_path.exists() or not file_path.is_file():
            self._json({"error": "not found"}, status=404)
            return

        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if file_path.suffix == ".js":
            content_type = "application/javascript"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self._cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def get_service() -> DemandAnalysisService:
    global SERVICE
    if SERVICE is None:
        SERVICE = DemandAnalysisService()
    return SERVICE


def run(host: str = "127.0.0.1", port: int = 8000, storage_dir: str = "outputs") -> None:
    global SERVICE
    SERVICE = DemandAnalysisService(storage_dir=storage_dir)
    server = ThreadingHTTPServer((host, port), DemandAgentHandler)
    print(f"Demand Analyst Agent API listening on http://{host}:{port}")
    print(f"Persistent storage: {storage_dir}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Demand Analyst Agent MVP API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--storage-dir", default="outputs")
    args = parser.parse_args()
    run(args.host, args.port, args.storage_dir)


if __name__ == "__main__":
    main()
