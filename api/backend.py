from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from openai import OpenAI

from clipper_core import AutoClipperCore
from config.config_manager import ConfigManager
from utils.helpers import get_app_dir, get_bundle_dir, get_ffmpeg_path, get_ytdlp_path


class ClipperBackend:
    """Shared backend API used by GUI and HTTP integration layers."""

    def __init__(
        self,
        config_file: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        ffmpeg_path: Optional[str] = None,
        ytdlp_path: Optional[str] = None,
    ):
        app_dir = Path(get_app_dir())
        env_config_file = os.environ.get("YTSC_CONFIG_FILE")
        env_output_dir = os.environ.get("YTSC_OUTPUT_DIR")
        self.config_file = Path(config_file or env_config_file or (app_dir / "config.json"))
        self.output_dir = Path(output_dir or env_output_dir or (app_dir / "output"))
        self.ffmpeg_path = ffmpeg_path or get_ffmpeg_path()
        self.ytdlp_path = ytdlp_path or get_ytdlp_path()

    def get_config_manager(self) -> ConfigManager:
        return ConfigManager(self.config_file, self.output_dir)

    def get_config(self) -> dict[str, Any]:
        return copy.deepcopy(self.get_config_manager().get_all())

    def get_ai_settings(self) -> dict[str, Any]:
        return self.get_config().get("ai_providers", {})

    def get_provider_type(self) -> str:
        return self.get_config().get("provider_type", "ytclip")

    def get_icon_data(self) -> dict[str, str]:
        try:
            bundle_dir = Path(get_bundle_dir())
            icon_path = bundle_dir / "assets" / "icon.png"
            if not icon_path.exists():
                return {"data": ""}
            encoded = base64.b64encode(icon_path.read_bytes()).decode("utf-8")
            return {"data": f"data:image/png;base64,{encoded}"}
        except Exception:
            return {"data": ""}

    def save_ai_settings(self, settings: dict[str, Any]) -> dict[str, str]:
        if not isinstance(settings, dict):
            return {"status": "error", "message": "settings must be an object"}

        cfg_mgr = self.get_config_manager()
        payload = copy.deepcopy(settings)
        provider_type = payload.pop("_provider_type", None)

        cfg_mgr.config["ai_providers"] = payload
        if provider_type:
            cfg_mgr.config["provider_type"] = provider_type

        highlight_finder = payload.get("highlight_finder", {})
        cfg_mgr.config["api_key"] = highlight_finder.get("api_key", "")
        cfg_mgr.config["base_url"] = highlight_finder.get("base_url", "https://api.openai.com/v1")
        cfg_mgr.config["model"] = highlight_finder.get("model", "gpt-4.1")
        cfg_mgr.save()
        return {"status": "saved"}

    def create_provider_client(
        self,
        provider_key: str = "highlight_finder",
        allow_legacy: bool = False,
    ) -> Optional[OpenAI]:
        cfg = self.get_config()
        provider = cfg.get("ai_providers", {}).get(provider_key, {})
        api_key = (provider.get("api_key") or "").strip()
        base_url = (provider.get("base_url") or "https://api.openai.com/v1").strip()

        if not api_key and allow_legacy and provider_key == "highlight_finder":
            api_key = (cfg.get("api_key") or "").strip()
            base_url = (cfg.get("base_url") or "https://api.openai.com/v1").strip()

        if not api_key:
            return None

        kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
        if provider_key == "caption_maker":
            kwargs["timeout"] = 600.0
        return OpenAI(**kwargs)

    def create_core(
        self,
        subtitle_language: str = "id",
        client: Optional[OpenAI] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        progress_callback: Optional[Callable[[str, float], None]] = None,
        token_callback: Optional[Callable[[int, int, float, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> AutoClipperCore:
        cfg = self.get_config()
        ai_providers = cfg.get("ai_providers", {})
        highlight_finder = ai_providers.get("highlight_finder", {})
        system_prompt = highlight_finder.get("system_message") or cfg.get("system_prompt")
        mediapipe_settings = cfg.get(
            "mediapipe_settings",
            {
                "lip_activity_threshold": 0.15,
                "switch_threshold": 0.3,
                "min_shot_duration": 90,
                "center_weight": 0.3,
            },
        )

        core = AutoClipperCore(
            client=client or self.create_provider_client("highlight_finder", allow_legacy=True),
            ffmpeg_path=self.ffmpeg_path,
            ytdlp_path=self.ytdlp_path,
            output_dir=cfg.get("output_dir", str(self.output_dir)),
            model=cfg.get("model", "gpt-4.1"),
            tts_model=cfg.get("tts_model", "tts-1"),
            temperature=cfg.get("temperature", 1.0),
            system_prompt=system_prompt,
            watermark_settings=cfg.get("watermark", {"enabled": False}),
            credit_watermark_settings=cfg.get("credit_watermark", {"enabled": False}),
            face_tracking_mode=cfg.get("face_tracking_mode", "opencv"),
            mediapipe_settings=mediapipe_settings,
            ai_providers=ai_providers,
            subtitle_language=subtitle_language,
            log_callback=log_callback,
            progress_callback=progress_callback,
            token_callback=token_callback,
            cancel_check=cancel_check,
        )

        gpu_settings = cfg.get("gpu_acceleration", {})
        if gpu_settings.get("enabled", False):
            core.enable_gpu_acceleration(True)

        return core

    def validate_highlight_finder_configuration(self) -> dict[str, Any]:
        cfg = self.get_config()
        provider = cfg.get("ai_providers", {}).get("highlight_finder", {})
        api_key = (provider.get("api_key") or "").strip()
        base_url = (provider.get("base_url") or "https://api.openai.com/v1").strip()
        model = (provider.get("model") or "").strip()

        if not api_key or not model:
            return {
                "status": "error",
                "message": "Highlight Finder API is not configured.",
            }

        try:
            client = OpenAI(api_key=api_key, base_url=base_url)
            try:
                available = [item.id for item in client.models.list().data]
                if model not in available:
                    return {
                        "status": "error",
                        "message": f"Highlight Finder model '{model}' is not available.",
                    }
            except Exception:
                # Some providers do not expose models.list(); non-empty credentials are enough.
                pass
        except Exception as exc:
            return {
                "status": "error",
                "message": f"Highlight Finder API validation failed: {str(exc)[:100]}",
            }

        return {"status": "ok", "message": "Highlight Finder configuration is valid."}

    def validate_api_key(self, base_url: str, api_key: str) -> dict[str, str]:
        if not base_url:
            return {"status": "error", "message": "Missing base URL"}
        if not api_key:
            return {"status": "error", "message": "Missing API key"}

        try:
            response = requests.get(
                self._get_models_url(base_url),
                headers=self._auth_headers(api_key),
                timeout=10,
            )
            if response.status_code == 200:
                return {"status": "ok"}
            return {"status": "error", "message": f"HTTP {response.status_code}"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    def get_models(self, base_url: str, api_key: str) -> dict[str, list[str]]:
        if not base_url:
            return {"models": []}

        try:
            response = requests.get(
                self._get_models_url(base_url),
                headers=self._auth_headers(api_key),
                timeout=15,
            )
            if response.status_code != 200:
                return {"models": []}
            payload = response.json()
            return {
                "models": [
                    item.get("id")
                    for item in payload.get("data", [])
                    if item.get("id")
                ]
            }
        except Exception:
            return {"models": []}

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions_root = self.get_sessions_root()
        if not sessions_root.exists():
            return []

        sessions: list[dict[str, Any]] = []
        for session_file in sorted(sessions_root.glob("*/session_data.json"), reverse=True):
            try:
                with open(session_file, "r", encoding="utf-8") as handle:
                    sessions.append(self._normalize_session_payload(json.load(handle), session_file.parent))
            except Exception:
                continue
        return sessions

    def get_session(self, session_ref: str | Path) -> dict[str, Any]:
        session_path = Path(session_ref)
        if session_path.exists():
            session_dir = session_path if session_path.is_dir() else session_path.parent
        else:
            session_dir = self.get_sessions_root() / str(session_ref)

        session_file = session_dir / "session_data.json"
        if not session_file.exists():
            raise FileNotFoundError(f"Session not found: {session_ref}")

        with open(session_file, "r", encoding="utf-8") as handle:
            return self._normalize_session_payload(json.load(handle), session_dir)

    def get_sessions_root(self) -> Path:
        cfg = self.get_config()
        return Path(cfg.get("output_dir", str(self.output_dir))) / "sessions"

    def _normalize_session_payload(self, payload: dict[str, Any], session_dir: Path) -> dict[str, Any]:
        normalized = copy.deepcopy(payload)
        normalized["session_dir"] = str(session_dir)
        normalized["session_id"] = session_dir.name
        clips_dir = session_dir / "clips"
        normalized["clips_dir"] = str(clips_dir) if clips_dir.exists() else ""
        return normalized

    def _get_models_url(self, base_url: str) -> str:
        url = base_url.rstrip("/")
        if url.endswith("/v1"):
            return f"{url}/models"
        return f"{url}/v1/models"

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"}
