from __future__ import annotations

import webview
from pathlib import Path

from api import ClipperBackend, ClipperJobManager
from utils.helpers import get_app_dir, get_bundle_dir

ACTIVE_JOB_STATES = {"queued", "running", "cancelling"}


class WebAPI:
    def __init__(self):
        app_dir = get_app_dir()
        self.config_file = str(app_dir / "config.json")
        self.output_dir = str(app_dir / "output")
        self.backend = ClipperBackend()
        self.job_manager = ClipperJobManager(self.backend)
        self.current_job_id: str | None = None

    def get_progress(self):
        job = self._get_current_job()
        if not job:
            return {"state": "idle", "status": "idle", "progress": 0.0}

        return {
            "job_id": job["job_id"],
            "state": job["status"],
            "status": job["message"],
            "progress": job["progress"],
            "error": job.get("error"),
            "result": job.get("result"),
        }

    def get_asset_paths(self):
        bundle_dir = get_bundle_dir()
        icon_path = Path(bundle_dir) / "assets" / "icon.png"
        return {"icon": str(icon_path)}

    def get_icon_data(self):
        return self.backend.get_icon_data()

    def get_ai_settings(self):
        return self.backend.get_ai_settings()

    def get_provider_type(self):
        return {"provider_type": self.backend.get_provider_type()}

    def validate_api_key(self, base_url, api_key):
        return self.backend.validate_api_key(base_url, api_key)

    def get_models(self, base_url, api_key):
        return self.backend.get_models(base_url, api_key)

    def save_ai_settings(self, settings):
        return self.backend.save_ai_settings(settings)

    def start_processing(self, url, num_clips=5, add_captions=True, add_hook=False, subtitle_lang="id"):
        current = self._get_current_job()
        if current and current["status"] in ACTIVE_JOB_STATES:
            return {"status": "busy", "job_id": current["job_id"]}

        job = self.job_manager.start_full_process(
            url=url,
            num_clips=int(num_clips),
            add_captions=bool(add_captions),
            add_hook=bool(add_hook),
            subtitle_language=subtitle_lang,
        )
        self.current_job_id = job["job_id"]
        return {"status": "started", "job_id": job["job_id"]}

    def cancel_processing(self):
        current = self._get_current_job()
        if not current:
            return {"status": "idle"}
        return self.job_manager.cancel_job(current["job_id"])

    def _get_current_job(self):
        if not self.current_job_id:
            return None
        return self.job_manager.get_job(self.current_job_id)


def main():
    api = WebAPI()
    bundle_dir = get_bundle_dir()
    html_path = Path(bundle_dir) / "web" / "index.html"
    webview.create_window("YT Short Clipper", str(html_path), js_api=api)
    webview.start()


if __name__ == "__main__":
    main()
