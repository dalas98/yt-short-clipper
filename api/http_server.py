from __future__ import annotations

import copy
import json
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse

from api.backend import ClipperBackend
from api.openapi import build_openapi_spec, build_swagger_ui_html

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "cancelling"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClipperJobManager:
    """Background job runner for HTTP and embedded GUI integrations."""

    def __init__(self, backend: Optional[ClipperBackend] = None):
        self.backend = backend or ClipperBackend()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(job) for job in self._jobs.values()]

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._require_job(job_id)
            if job["status"] not in TERMINAL_STATUSES:
                job["cancel_requested"] = True
                if job["status"] == "queued":
                    job["status"] = "cancelled"
                    job["message"] = "Cancelled before start"
                    job["finished_at"] = _utcnow()
                else:
                    job["status"] = "cancelling"
                    job["message"] = "Cancellation requested"
                job["updated_at"] = _utcnow()
            return copy.deepcopy(job)

    def start_full_process(
        self,
        url: str,
        num_clips: int = 5,
        add_captions: bool = True,
        add_hook: bool = False,
        subtitle_language: str = "id",
    ) -> dict[str, Any]:
        job = self._create_job(
            "full_process",
            {
                "url": url,
                "num_clips": num_clips,
                "add_captions": add_captions,
                "add_hook": add_hook,
                "subtitle_language": subtitle_language,
            },
        )
        threading.Thread(
            target=self._run_full_process,
            args=(job["job_id"], url, num_clips, add_captions, add_hook, subtitle_language),
            daemon=True,
        ).start()
        return job

    def start_find_highlights(
        self,
        url: str,
        num_clips: int = 5,
        subtitle_language: str = "id",
    ) -> dict[str, Any]:
        job = self._create_job(
            "find_highlights",
            {
                "url": url,
                "num_clips": num_clips,
                "subtitle_language": subtitle_language,
            },
        )
        threading.Thread(
            target=self._run_find_highlights,
            args=(job["job_id"], url, num_clips, subtitle_language),
            daemon=True,
        ).start()
        return job

    def start_process_selected(
        self,
        session_ref: str,
        selected_indexes: Optional[list[int]] = None,
        selected_highlights: Optional[list[dict[str, Any]]] = None,
        add_captions: bool = False,
        add_hook: bool = False,
    ) -> dict[str, Any]:
        job = self._create_job(
            "process_selected",
            {
                "session_ref": session_ref,
                "selected_indexes": selected_indexes or [],
                "selected_highlights": selected_highlights or [],
                "add_captions": add_captions,
                "add_hook": add_hook,
            },
        )
        threading.Thread(
            target=self._run_process_selected,
            args=(job["job_id"], session_ref, selected_indexes, selected_highlights, add_captions, add_hook),
            daemon=True,
        ).start()
        return job

    def _run_full_process(
        self,
        job_id: str,
        url: str,
        num_clips: int,
        add_captions: bool,
        add_hook: bool,
        subtitle_language: str,
    ) -> None:
        self._mark_running(job_id, "Starting processing...", 0.0)
        try:
            core = self.backend.create_core(
                subtitle_language=subtitle_language,
                log_callback=self._make_log_callback(job_id),
                progress_callback=self._make_progress_callback(job_id),
                token_callback=self._make_token_callback(job_id),
                cancel_check=self._make_cancel_check(job_id),
            )
            core.process(url, num_clips=num_clips, add_captions=add_captions, add_hook=add_hook)
            if self._is_cancel_requested(job_id):
                self._mark_cancelled(job_id, "Cancelled")
            else:
                output_dir = self.backend.get_config().get("output_dir", str(self.backend.output_dir))
                self._mark_completed(job_id, {"output_dir": output_dir})
        except Exception as exc:
            self._mark_failure(job_id, exc)

    def _run_find_highlights(self, job_id: str, url: str, num_clips: int, subtitle_language: str) -> None:
        self._mark_running(job_id, "Starting highlight search...", 0.0)
        try:
            core = self.backend.create_core(
                subtitle_language=subtitle_language,
                log_callback=self._make_log_callback(job_id),
                progress_callback=self._make_progress_callback(job_id),
                token_callback=self._make_token_callback(job_id),
                cancel_check=self._make_cancel_check(job_id),
            )
            result = core.find_highlights_only(url, num_clips=num_clips)
            if self._is_cancel_requested(job_id) or result is None:
                self._mark_cancelled(job_id, "Cancelled")
            else:
                session_ref = result.get("session_dir") or ""
                session = self.backend.get_session(session_ref) if session_ref else result
                self._mark_completed(job_id, session)
        except Exception as exc:
            self._mark_failure(job_id, exc)

    def _run_process_selected(
        self,
        job_id: str,
        session_ref: str,
        selected_indexes: Optional[list[int]],
        selected_highlights: Optional[list[dict[str, Any]]],
        add_captions: bool,
        add_hook: bool,
    ) -> None:
        self._mark_running(job_id, "Starting selected clip processing...", 0.0)
        try:
            session = self.backend.get_session(session_ref)
            highlights = self._resolve_selected_highlights(session, selected_indexes, selected_highlights)
            core = self.backend.create_core(
                subtitle_language="id",
                log_callback=self._make_log_callback(job_id),
                progress_callback=self._make_progress_callback(job_id),
                token_callback=self._make_token_callback(job_id),
                cancel_check=self._make_cancel_check(job_id),
            )
            core.process_selected_highlights(
                session["video_path"],
                highlights,
                session["session_dir"],
                add_captions=add_captions,
                add_hook=add_hook,
            )
            if self._is_cancel_requested(job_id):
                self._mark_cancelled(job_id, "Cancelled")
            else:
                refreshed = self.backend.get_session(session["session_dir"])
                self._mark_completed(
                    job_id,
                    {
                        "session_id": refreshed["session_id"],
                        "session_dir": refreshed["session_dir"],
                        "clips_dir": refreshed.get("clips_dir", ""),
                        "clips_processed": len(highlights),
                    },
                )
        except Exception as exc:
            self._mark_failure(job_id, exc)

    def _resolve_selected_highlights(
        self,
        session: dict[str, Any],
        selected_indexes: Optional[list[int]],
        selected_highlights: Optional[list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        if selected_highlights:
            return selected_highlights

        available = session.get("highlights", [])
        if not selected_indexes:
            return available

        resolved: list[dict[str, Any]] = []
        for index in selected_indexes:
            if index < 0 or index >= len(available):
                raise IndexError(f"selected_indexes contains invalid index: {index}")
            resolved.append(available[index])
        return resolved

    def _create_job(self, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = _utcnow()
        job = {
            "job_id": job_id,
            "type": job_type,
            "status": "queued",
            "progress": 0.0,
            "message": "Queued",
            "created_at": now,
            "updated_at": now,
            "finished_at": None,
            "input": copy.deepcopy(payload),
            "result": None,
            "error": None,
            "cancel_requested": False,
            "token_usage": {
                "gpt_input": 0,
                "gpt_output": 0,
                "whisper_seconds": 0,
                "tts_chars": 0,
            },
            "logs": [],
        }
        with self._lock:
            self._jobs[job_id] = job
            return copy.deepcopy(job)

    def _mark_running(self, job_id: str, message: str, progress: float) -> None:
        with self._lock:
            job = self._require_job(job_id)
            if job["status"] == "cancelled":
                return
            job["status"] = "running"
            job["message"] = message
            job["progress"] = progress
            job["updated_at"] = _utcnow()

    def _mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            job = self._require_job(job_id)
            job["status"] = "completed"
            job["progress"] = 1.0
            job["message"] = "Completed"
            job["result"] = copy.deepcopy(result)
            job["finished_at"] = _utcnow()
            job["updated_at"] = job["finished_at"]

    def _mark_cancelled(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._require_job(job_id)
            job["status"] = "cancelled"
            job["message"] = message
            job["finished_at"] = _utcnow()
            job["updated_at"] = job["finished_at"]

    def _mark_failure(self, job_id: str, exc: Exception) -> None:
        with self._lock:
            job = self._require_job(job_id)
            if job["cancel_requested"] or "cancel" in str(exc).lower():
                job["status"] = "cancelled"
                job["message"] = "Cancelled"
            else:
                job["status"] = "failed"
                job["message"] = str(exc)
                job["error"] = str(exc)
            job["finished_at"] = _utcnow()
            job["updated_at"] = job["finished_at"]

    def _make_log_callback(self, job_id: str):
        def callback(message: str) -> None:
            with self._lock:
                job = self._require_job(job_id)
                job["message"] = str(message)
                logs = job.setdefault("logs", [])
                logs.append(str(message))
                if len(logs) > 100:
                    del logs[:-100]
                job["updated_at"] = _utcnow()
        return callback

    def _make_progress_callback(self, job_id: str):
        def callback(status: str, progress: Optional[float] = None) -> None:
            with self._lock:
                job = self._require_job(job_id)
                if progress is not None:
                    try:
                        job["progress"] = max(0.0, min(1.0, float(progress)))
                    except (TypeError, ValueError):
                        pass
                job["message"] = str(status)
                job["updated_at"] = _utcnow()
        return callback

    def _make_token_callback(self, job_id: str):
        def callback(gpt_in: int, gpt_out: int, whisper: float, tts_chars: int) -> None:
            with self._lock:
                job = self._require_job(job_id)
                usage = job.setdefault("token_usage", {})
                usage["gpt_input"] = usage.get("gpt_input", 0) + int(gpt_in)
                usage["gpt_output"] = usage.get("gpt_output", 0) + int(gpt_out)
                usage["whisper_seconds"] = usage.get("whisper_seconds", 0) + float(whisper)
                usage["tts_chars"] = usage.get("tts_chars", 0) + int(tts_chars)
                job["updated_at"] = _utcnow()
        return callback

    def _make_cancel_check(self, job_id: str):
        return lambda: self._is_cancel_requested(job_id)

    def _is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            job = self._require_job(job_id)
            return bool(job.get("cancel_requested"))

    def _require_job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"Unknown job: {job_id}")
        return job


class ClipperApiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        backend: Optional[ClipperBackend] = None,
    ):
        self.backend = backend or ClipperBackend()
        self.job_manager = ClipperJobManager(self.backend)
        super().__init__(server_address, ClipperApiHandler)


class ClipperApiHandler(BaseHTTPRequestHandler):
    server: ClipperApiServer

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        parts = [part for part in path.split("/") if part]
        server_url = f"http://{self.headers.get('Host') or f'{self.server.server_address[0]}:{self.server.server_address[1]}'}"

        if path == "/health":
            self._respond_json(200, {"status": "ok"})
            return

        if path == "/api/openapi.json":
            self._respond_json(200, build_openapi_spec(server_url))
            return

        if path == "/docs":
            self._respond_html(200, build_swagger_ui_html(f"{server_url}/api/openapi.json"))
            return

        if parts == ["api", "config", "ai"]:
            self._respond_json(
                200,
                {
                    "status": "ok",
                    "provider_type": self.server.backend.get_provider_type(),
                    "data": self.server.backend.get_ai_settings(),
                },
            )
            return

        if parts == ["api", "sessions"]:
            self._respond_json(200, {"sessions": self.server.backend.list_sessions()})
            return

        if len(parts) == 3 and parts[:2] == ["api", "sessions"]:
            try:
                self._respond_json(200, self.server.backend.get_session(parts[2]))
            except FileNotFoundError as exc:
                self._respond_json(404, {"error": str(exc)})
            return

        if parts == ["api", "jobs"]:
            self._respond_json(200, {"jobs": self.server.job_manager.list_jobs()})
            return

        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            job = self.server.job_manager.get_job(parts[2])
            if not job:
                self._respond_json(404, {"error": f"Unknown job: {parts[2]}"})
            else:
                self._respond_json(200, job)
            return

        self._respond_json(404, {"error": f"Unknown route: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        parts = [part for part in path.split("/") if part]
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._respond_json(400, {"error": str(exc)})
            return

        if parts == ["api", "config", "ai"]:
            self._respond_json(200, self.server.backend.save_ai_settings(payload))
            return

        if parts == ["api", "providers", "validate"]:
            self._respond_json(
                200,
                self.server.backend.validate_api_key(
                    payload.get("base_url", ""),
                    payload.get("api_key", ""),
                ),
            )
            return

        if parts == ["api", "providers", "models"]:
            self._respond_json(
                200,
                self.server.backend.get_models(
                    payload.get("base_url", ""),
                    payload.get("api_key", ""),
                ),
            )
            return

        if parts == ["api", "jobs", "full-process"]:
            try:
                job = self.server.job_manager.start_full_process(
                    url=payload["url"],
                    num_clips=int(payload.get("num_clips", 5)),
                    add_captions=bool(payload.get("add_captions", True)),
                    add_hook=bool(payload.get("add_hook", False)),
                    subtitle_language=payload.get("subtitle_language", "id"),
                )
                self._respond_json(202, job)
            except KeyError as exc:
                self._respond_json(400, {"error": f"Missing field: {exc.args[0]}"})
            return

        if parts == ["api", "jobs", "find-highlights"]:
            try:
                job = self.server.job_manager.start_find_highlights(
                    url=payload["url"],
                    num_clips=int(payload.get("num_clips", 5)),
                    subtitle_language=payload.get("subtitle_language", "id"),
                )
                self._respond_json(202, job)
            except KeyError as exc:
                self._respond_json(400, {"error": f"Missing field: {exc.args[0]}"})
            return

        if parts == ["api", "jobs", "process-selected"]:
            session_ref = payload.get("session_id") or payload.get("session_dir")
            if not session_ref:
                self._respond_json(400, {"error": "Missing field: session_id or session_dir"})
                return
            try:
                job = self.server.job_manager.start_process_selected(
                    session_ref=session_ref,
                    selected_indexes=payload.get("selected_indexes"),
                    selected_highlights=payload.get("selected_highlights"),
                    add_captions=bool(payload.get("add_captions", False)),
                    add_hook=bool(payload.get("add_hook", False)),
                )
                self._respond_json(202, job)
            except (IndexError, FileNotFoundError) as exc:
                self._respond_json(400, {"error": str(exc)})
            return

        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
            job_id = parts[2]
            try:
                self._respond_json(200, self.server.job_manager.cancel_job(job_id))
            except KeyError as exc:
                self._respond_json(404, {"error": str(exc)})
            return

        self._respond_json(404, {"error": f"Unknown route: {path}"})

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc

    def _respond_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_html(self, status_code: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def log_message(self, format: str, *args: Any) -> None:
        # Keep stdout clean for GUI/API embedding.
        return


def serve_api(host: str = "127.0.0.1", port: int = 8787, backend: Optional[ClipperBackend] = None) -> ClipperApiServer:
    server = ClipperApiServer((host, port), backend=backend)
    print(f"YT Short Clipper API listening on http://{host}:{port}")
    server.serve_forever()
    return server
