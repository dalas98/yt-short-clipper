from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from urllib.request import Request, urlopen


class _StubAutoClipperCore:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    @staticmethod
    def get_default_prompt():
        return "stub prompt"


class _StubOpenAI:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.models = types.SimpleNamespace(
            list=lambda: types.SimpleNamespace(data=[types.SimpleNamespace(id="gpt-4.1")])
        )


clipper_core_stub = types.ModuleType("clipper_core")
clipper_core_stub.AutoClipperCore = _StubAutoClipperCore
sys.modules.setdefault("clipper_core", clipper_core_stub)

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = _StubOpenAI
sys.modules.setdefault("openai", openai_stub)

requests_stub = types.ModuleType("requests")
requests_stub.get = lambda *args, **kwargs: types.SimpleNamespace(
    status_code=200,
    json=lambda: {"data": [{"id": "gpt-4.1"}]},
)
sys.modules.setdefault("requests", requests_stub)

from api.backend import ClipperBackend
from api.http_server import ClipperApiServer, ClipperJobManager


class ClipperBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="ytclip-tests-"))
        self.config_file = self.tmpdir / "config.json"
        self.output_dir = self.tmpdir / "output"
        self.backend = ClipperBackend(
            config_file=self.config_file,
            output_dir=self.output_dir,
            ffmpeg_path="ffmpeg",
            ytdlp_path="yt-dlp",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("YTSC_CONFIG_FILE", None)
        os.environ.pop("YTSC_OUTPUT_DIR", None)

    def test_save_ai_settings_updates_provider_and_legacy_fields(self):
        result = self.backend.save_ai_settings(
            {
                "_provider_type": "openai",
                "highlight_finder": {
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-test",
                    "model": "gpt-4.1",
                },
                "caption_maker": {
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-caption",
                    "model": "whisper-1",
                },
            }
        )

        self.assertEqual(result["status"], "saved")
        saved = json.loads(self.config_file.read_text())
        self.assertEqual(saved["provider_type"], "openai")
        self.assertEqual(saved["api_key"], "sk-test")
        self.assertEqual(saved["base_url"], "https://api.openai.com/v1")
        self.assertEqual(saved["model"], "gpt-4.1")
        self.assertEqual(saved["ai_providers"]["caption_maker"]["model"], "whisper-1")

    def test_list_sessions_and_get_session_add_session_id(self):
        self.backend.get_config_manager()  # create default config
        session_dir = self.output_dir / "sessions" / "20260406_120000"
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_dir": str(session_dir),
            "video_path": "video.mp4",
            "srt_path": "video.srt",
            "highlights": [{"title": "clip"}],
            "video_info": {"title": "demo"},
            "status": "highlights_found",
        }
        (session_dir / "session_data.json").write_text(json.dumps(payload), encoding="utf-8")

        sessions = self.backend.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], "20260406_120000")
        self.assertEqual(sessions[0]["session_dir"], str(session_dir))

        session = self.backend.get_session("20260406_120000")
        self.assertEqual(session["session_id"], "20260406_120000")
        self.assertEqual(session["video_info"]["title"], "demo")

    def test_env_overrides_are_respected_for_paths(self):
        env_config = self.tmpdir / "env-config.json"
        env_output = self.tmpdir / "env-output"
        os.environ["YTSC_CONFIG_FILE"] = str(env_config)
        os.environ["YTSC_OUTPUT_DIR"] = str(env_output)

        backend = ClipperBackend(ffmpeg_path="ffmpeg", ytdlp_path="yt-dlp")
        self.assertEqual(backend.config_file, env_config)
        self.assertEqual(backend.output_dir, env_output)


class _FakeCore:
    def __init__(self, log_callback=None, progress_callback=None, token_callback=None, cancel_check=None, **kwargs):
        self.log_callback = log_callback or (lambda message: None)
        self.progress_callback = progress_callback or (lambda status, progress=None: None)
        self.token_callback = token_callback or (lambda *args: None)
        self.cancel_check = cancel_check or (lambda: False)

    def process(self, url, num_clips=5, add_captions=True, add_hook=False):
        self.progress_callback("Downloading video...", 0.2)
        self.token_callback(10, 20, 30.0, 40)
        self.progress_callback("Complete!", 1.0)

    def find_highlights_only(self, url, num_clips=5):
        self.progress_callback("Finding highlights...", 0.5)
        self.token_callback(1, 2, 3.0, 4)
        return {"session_dir": "/tmp/session-001"}

    def process_selected_highlights(self, video_path, selected_highlights, session_dir, add_captions=True, add_hook=True):
        self.progress_callback("Clip 1/1: Processing", 0.6)


class _FakeBackend:
    def __init__(self):
        self.output_dir = Path("/tmp/output")

    def create_core(self, **kwargs):
        return _FakeCore(**kwargs)

    def get_config(self):
        return {"output_dir": str(self.output_dir)}

    def get_session(self, session_ref):
        return {
            "session_id": "session-001",
            "session_dir": "/tmp/session-001",
            "video_path": "/tmp/source.mp4",
            "highlights": [{"title": "clip-a"}, {"title": "clip-b"}],
            "clips_dir": "/tmp/session-001/clips",
        }


class _CancelableCore(_FakeCore):
    def process(self, url, num_clips=5, add_captions=True, add_hook=False):
        while not self.cancel_check():
            self.progress_callback("Working...", 0.3)
            time.sleep(0.01)


class _CancelableBackend(_FakeBackend):
    def create_core(self, **kwargs):
        return _CancelableCore(**kwargs)


class ClipperJobManagerTests(unittest.TestCase):
    def _wait_for_terminal(self, manager: ClipperJobManager, job_id: str, timeout: float = 2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = manager.get_job(job_id)
            if job and job["status"] in {"completed", "failed", "cancelled"}:
                return job
            time.sleep(0.02)
        self.fail(f"Job {job_id} did not reach terminal state")

    def test_find_highlights_job_returns_session_payload(self):
        manager = ClipperJobManager(_FakeBackend())
        job = manager.start_find_highlights("https://youtube.com/watch?v=demo", num_clips=2)
        finished = self._wait_for_terminal(manager, job["job_id"])

        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["result"]["session_id"], "session-001")
        self.assertEqual(finished["token_usage"]["gpt_input"], 1)
        self.assertEqual(finished["token_usage"]["tts_chars"], 4)

    def test_cancel_job_marks_background_process_cancelled(self):
        manager = ClipperJobManager(_CancelableBackend())
        job = manager.start_full_process("https://youtube.com/watch?v=demo")
        time.sleep(0.05)
        manager.cancel_job(job["job_id"])
        finished = self._wait_for_terminal(manager, job["job_id"])
        self.assertEqual(finished["status"], "cancelled")


class ClipperHttpServerTests(unittest.TestCase):
    def test_health_endpoint_returns_ok(self):
        server = ClipperApiServer(("127.0.0.1", 0), backend=_FakeBackend())
        thread = types.SimpleNamespace()
        try:
            import threading

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            with urlopen(Request(f"http://{host}:{port}/health")) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["status"], "ok")
        finally:
            server.shutdown()
            server.server_close()
            if hasattr(thread, "join"):
                thread.join(timeout=1)

    def test_openapi_and_docs_endpoints_are_served(self):
        server = ClipperApiServer(("127.0.0.1", 0), backend=_FakeBackend())
        thread = types.SimpleNamespace()
        try:
            import threading

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address

            with urlopen(Request(f"http://{host}:{port}/api/openapi.json")) as response:
                spec = json.loads(response.read().decode("utf-8"))
            self.assertEqual(spec["openapi"], "3.1.0")
            self.assertIn("/api/jobs/find-highlights", spec["paths"])

            with urlopen(Request(f"http://{host}:{port}/docs")) as response:
                html = response.read().decode("utf-8")
            self.assertIn("SwaggerUIBundle", html)
            self.assertIn("/api/openapi.json", html)
        finally:
            server.shutdown()
            server.server_close()
            if hasattr(thread, "join"):
                thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
