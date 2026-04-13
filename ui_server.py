from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from run_session import AxiomRunManager

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "ui" / "dist"
EXCLUDED_TREE_NAMES = {
    ".axiom",
    ".axiom_snapshots",
    "__pycache__",
    "node_modules",
    "dist",
}
STATE = AxiomRunManager()


def tree_for_directory(project_root: Path, current: Path | None = None) -> list[dict]:
    current = current or project_root
    nodes: list[dict] = []
    for child in sorted(
        current.iterdir(), key=lambda item: (item.is_file(), item.name.lower())
    ):
        if child.name in EXCLUDED_TREE_NAMES:
            continue
        relative_path = (
            "."
            if child == project_root
            else str(child.relative_to(project_root)).replace("\\", "/")
        )
        if child.is_dir():
            nodes.append(
                {
                    "name": child.name,
                    "path": relative_path,
                    "type": "directory",
                    "children": tree_for_directory(project_root, child),
                }
            )
        else:
            nodes.append(
                {
                    "name": child.name,
                    "path": relative_path,
                    "type": "file",
                }
            )
    return nodes


class AxiomUIRequestHandler(BaseHTTPRequestHandler):
    server_version = "AxiomUI/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self._send_json({"status": "ok"})
                return
            if parsed.path == "/api/projects":
                self._send_json(STATE.list_projects())
                return
            if (
                parsed.path.startswith("/api/projects/")
                and parsed.path != "/api/projects/active"
            ):
                project_id = parsed.path.split("/")[-1]
                self._send_json(STATE.get_project(project_id))
                return
            if parsed.path == "/api/runs":
                self._send_json({"runs": STATE.list_runs()})
                return
            if parsed.path == "/api/llm/settings":
                self._send_json(STATE.get_llm_settings())
                return
            if parsed.path.startswith("/api/runs/"):
                session_id = parsed.path.split("/")[-1]
                self._send_json(STATE.get_run_state(session_id))
                return
            if parsed.path == "/api/artifact":
                self._handle_artifact_request(parsed)
                return
            self._serve_static(parsed.path)
        except Exception as error:
            self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/project/tree":
                if payload.get("projectId"):
                    project_root = Path(
                        STATE.project_manager.get_project(
                            payload["projectId"]
                        ).root_path
                    ).resolve()
                else:
                    project_root = Path(payload["projectPath"]).resolve()
                self._send_json(
                    {
                        "projectPath": str(project_root),
                        "tree": tree_for_directory(project_root),
                    }
                )
                return
            if parsed.path == "/api/projects":
                self._send_json(
                    STATE.create_project(payload["path"], payload.get("name"))
                )
                return
            if parsed.path == "/api/projects/active":
                self._send_json(STATE.set_active_project(payload["projectId"]))
                return
            if parsed.path == "/api/runs/prepare":
                self._send_json(STATE.prepare_run(payload))
                return
            if parsed.path == "/api/llm/settings":
                self._send_json(STATE.update_llm_settings(payload))
                return
            if parsed.path.endswith("/approve-plan"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(STATE.approve_plan(session_id))
                return
            if parsed.path.endswith("/approve-decomposition"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(STATE.approve_decomposition(session_id))
                return
            if parsed.path.endswith("/approve-implementation"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(STATE.approve_implementation(session_id))
                return
            if parsed.path.endswith("/approve-phase"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(STATE.approve_phase(session_id))
                return
            if parsed.path.endswith("/decline-plan"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(STATE.decline_plan(session_id, payload.get("reason")))
                return
            if parsed.path.endswith("/decline-decomposition"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(
                    STATE.decline_decomposition(session_id, payload.get("reason"))
                )
                return
            if parsed.path.endswith("/decline-implementation"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(
                    STATE.decline_implementation(session_id, payload.get("reason"))
                )
                return
            if parsed.path.endswith("/decline-phase"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(STATE.decline_phase(session_id, payload.get("reason")))
                return
            if parsed.path.endswith("/cancel"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(STATE.cancel_run(session_id, payload.get("reason")))
                return
            if parsed.path.endswith("/archive"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(STATE.archive_run(session_id))
                return
            if parsed.path.endswith("/unarchive"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(STATE.unarchive_run(session_id))
                return
            if parsed.path.endswith("/steer"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(STATE.steer_run(session_id, payload["prompt"]))
                return
            if parsed.path.endswith("/queue"):
                session_id = parsed.path.split("/")[-2]
                self._send_json(STATE.queue_task(session_id, payload["task"]))
                return
            self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)
        except Exception as error:
            self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _handle_artifact_request(self, parsed) -> None:
        query = parse_qs(parsed.query)
        raw_path = query.get("path", [""])[0]
        if not raw_path:
            self._send_json(
                {"error": "Artifact path is required."}, status=HTTPStatus.BAD_REQUEST
            )
            return
        artifact_path = Path(raw_path).resolve()
        if not artifact_path.exists():
            self._send_json(
                {"error": "Artifact does not exist."}, status=HTTPStatus.NOT_FOUND
            )
            return
        text = artifact_path.read_text(encoding="utf-8")
        try:
            parsed_payload = json.loads(text)
        except json.JSONDecodeError:
            parsed_payload = None
        self._send_json(
            {
                "path": str(artifact_path),
                "isJson": parsed_payload is not None,
                "content": parsed_payload if parsed_payload is not None else text,
            }
        )

    def _serve_static(self, raw_path: str) -> None:
        if not DIST_DIR.exists():
            self._send_json(
                {
                    "error": "UI build output was not found. Start the Vite dev server or build the frontend first."
                },
                status=HTTPStatus.NOT_FOUND,
            )
            return
        requested = DIST_DIR / raw_path.lstrip("/")
        if raw_path in {"/", ""} or not requested.exists() or requested.is_dir():
            requested = DIST_DIR / "index.html"
        if not requested.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = requested.read_bytes()
        content_type = (
            mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Axiom UI bridge server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AxiomUIRequestHandler)
    print(f"Axiom UI server listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
