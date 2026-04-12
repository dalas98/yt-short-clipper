from __future__ import annotations

import asyncio
import copy
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

from api.backend import ClipperBackend

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


TAGS_METADATA = [
    {"name": "system", "description": "Health and discovery endpoints"},
    {"name": "config", "description": "AI provider configuration"},
    {"name": "providers", "description": "Provider validation and model loading"},
    {"name": "jobs", "description": "Background processing jobs"},
    {"name": "sessions", "description": "Saved highlight sessions"},
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    system_message: Optional[str] = None


class SaveAiConfigRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider_type: Optional[str] = Field(default=None, alias="_provider_type")
    highlight_finder: Optional[ProviderConfig] = None
    caption_maker: Optional[ProviderConfig] = None
    hook_maker: Optional[ProviderConfig] = None
    youtube_title_maker: Optional[ProviderConfig] = None


class ProviderRequest(BaseModel):
    base_url: str
    api_key: str


class FullProcessRequest(BaseModel):
    url: Optional[str] = None
    num_clips: int = 5
    add_captions: bool = True
    add_hook: bool = False
    subtitle_language: str = "id"


class FindHighlightsRequest(BaseModel):
    url: Optional[str] = None
    num_clips: int = 5
    subtitle_language: str = "id"


class ProcessSelectedRequest(BaseModel):
    session_id: Optional[str] = None
    session_dir: Optional[str] = None
    selected_indexes: Optional[list[int]] = None
    selected_highlights: Optional[list[dict[str, Any]]] = None
    add_captions: bool = False
    add_hook: bool = False


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
                logs = job.setdefault("logs", [])
                logs.append(f"[ERROR] {exc}")
                if len(logs) > 100:
                    del logs[:-100]
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


def create_app(backend: Optional[ClipperBackend] = None) -> FastAPI:
    backend = backend or ClipperBackend()
    job_manager = ClipperJobManager(backend)

    app = FastAPI(
        title="YT Short Clipper API",
        version="1.0.0",
        description=(
            "HTTP integration API for the GUI-first YT Short Clipper app. "
            "The API shares the same backend service layer as the desktop/webview GUI."
        ),
        openapi_url="/api/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        openapi_tags=TAGS_METADATA,
    )
    app.state.backend = backend
    app.state.job_manager = job_manager

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config/ai", tags=["config"])
    async def get_ai_config(request: Request) -> dict[str, Any]:
        backend = request.app.state.backend
        return {
            "status": "ok",
            "provider_type": backend.get_provider_type(),
            "data": backend.get_ai_settings(),
        }

    @app.post("/api/config/ai", tags=["config"])
    async def save_ai_config(request: Request, payload: SaveAiConfigRequest = Body(...)) -> dict[str, Any]:
        return request.app.state.backend.save_ai_settings(payload.model_dump(by_alias=True, exclude_none=True))

    @app.post("/api/providers/validate", tags=["providers"])
    async def validate_provider(request: Request, payload: ProviderRequest) -> dict[str, Any]:
        return request.app.state.backend.validate_api_key(payload.base_url, payload.api_key)

    @app.post("/api/providers/models", tags=["providers"])
    async def get_provider_models(request: Request, payload: ProviderRequest) -> dict[str, Any]:
        return request.app.state.backend.get_models(payload.base_url, payload.api_key)

    @app.get("/api/jobs", tags=["jobs"])
    async def list_jobs(request: Request) -> dict[str, Any]:
        return {"jobs": request.app.state.job_manager.list_jobs()}

    @app.get("/api/jobs/{job_id}", tags=["jobs"])
    async def get_job(request: Request, job_id: str) -> dict[str, Any]:
        job = request.app.state.job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        return job

    @app.get("/api/jobs/{job_id}/stream", tags=["jobs"])
    async def stream_job(request: Request, job_id: str):
        import logging
        logger = logging.getLogger(__name__)
        
        job = request.app.state.job_manager.get_job(job_id)
        if not job:
            logger.error(f"[SSE] Job not found: {job_id}")
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        
        logger.info(f"[SSE] Starting stream for job: {job_id}")

        async def event_generator():
            TERMINAL = frozenset(TERMINAL_STATUSES)
            iteration = 0

            while True:
                iteration += 1
                current_job = request.app.state.job_manager.get_job(job_id)
                if not current_job:
                    logger.error(f"[SSE] Job disappeared: {job_id}")
                    yield "event: error\ndata: Job not found\n\n"
                    break

                data = json.dumps(current_job)
                logger.info(f"[SSE] Sending job data for {job_id}, status: {current_job.get('status')}, logs: {len(current_job.get('logs', []))}")
                yield f"event: job\ndata: {data}\n\n"

                if current_job["status"] in TERMINAL:
                    logger.info(f"[SSE] Job {job_id} reached terminal status: {current_job['status']}")
                    break

                await asyncio.sleep(0.5)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Transfer-Encoding": "chunked",
            },
        )

    @app.post("/api/jobs/{job_id}/cancel", tags=["jobs"])
    async def cancel_job(request: Request, job_id: str) -> dict[str, Any]:
        try:
            return request.app.state.job_manager.cancel_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/jobs/full-process", status_code=202, tags=["jobs"])
    async def start_full_process(request: Request, payload: FullProcessRequest) -> dict[str, Any]:
        if not payload.url:
            raise HTTPException(status_code=400, detail="Missing field: url")
        return request.app.state.job_manager.start_full_process(
            url=payload.url,
            num_clips=int(payload.num_clips),
            add_captions=payload.add_captions,
            add_hook=payload.add_hook,
            subtitle_language=payload.subtitle_language,
        )

    @app.post("/api/jobs/find-highlights", status_code=202, tags=["jobs"])
    async def start_find_highlights(request: Request, payload: FindHighlightsRequest) -> dict[str, Any]:
        if not payload.url:
            raise HTTPException(status_code=400, detail="Missing field: url")
        return request.app.state.job_manager.start_find_highlights(
            url=payload.url,
            num_clips=int(payload.num_clips),
            subtitle_language=payload.subtitle_language,
        )

    @app.post("/api/jobs/process-selected", status_code=202, tags=["jobs"])
    async def start_process_selected(request: Request, payload: ProcessSelectedRequest) -> dict[str, Any]:
        session_ref = payload.session_id or payload.session_dir
        if not session_ref:
            raise HTTPException(status_code=400, detail="Missing field: session_id or session_dir")
        try:
            return request.app.state.job_manager.start_process_selected(
                session_ref=session_ref,
                selected_indexes=payload.selected_indexes,
                selected_highlights=payload.selected_highlights,
                add_captions=payload.add_captions,
                add_hook=payload.add_hook,
            )
        except (IndexError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/sessions", tags=["sessions"])
    async def list_sessions(request: Request) -> dict[str, Any]:
        return {"sessions": request.app.state.backend.list_sessions()}

    @app.get("/api/sessions/{session_id}", tags=["sessions"])
    async def get_session(request: Request, session_id: str) -> dict[str, Any]:
        try:
            return request.app.state.backend.get_session(session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


app = create_app()


def serve_api(host: str = "127.0.0.1", port: int = 8787, backend: Optional[ClipperBackend] = None) -> None:
    uvicorn.run(create_app(backend=backend), host=host, port=port)
