"""Local settings storage compatible with OpenAI-style chat completion APIs."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict


APP_NAME = "PhDFloatingSummary"
SETTINGS_FILE_NAME = "settings.json"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "llm": {
        "mode": "api",
        "apiUrl": "",
        "apiKey": "",
        "model": "",
        "temperature": 0.2,
        "requestTimeoutMs": 60000,
    },
    "limits": {
        "maxPdfFiles": 5,
        "maxCharsPerPdf": 60000,
    },
}


def get_config_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def get_settings_path() -> Path:
    return get_config_dir() / SETTINGS_FILE_NAME


def clamp_number(value: Any, minimum: float, maximum: float, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback

    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def sanitize_settings(raw_settings: Dict[str, Any]) -> Dict[str, Any]:
    merged = deep_merge(DEFAULT_SETTINGS, raw_settings or {})
    llm = merged.get("llm") or {}
    limits = merged.get("limits") or {}

    return {
        "llm": {
            "mode": "api",
            "apiUrl": str(llm.get("apiUrl") or "").strip(),
            "apiKey": str(llm.get("apiKey") or "").strip(),
            "model": str(llm.get("model") or "").strip(),
            "temperature": clamp_number(
                llm.get("temperature"),
                0,
                2,
                DEFAULT_SETTINGS["llm"]["temperature"],
            ),
            "requestTimeoutMs": int(
                clamp_number(
                    llm.get("requestTimeoutMs"),
                    5000,
                    180000,
                    DEFAULT_SETTINGS["llm"]["requestTimeoutMs"],
                )
            ),
        },
        "limits": {
            "maxPdfFiles": int(
                clamp_number(
                    limits.get("maxPdfFiles"),
                    1,
                    5,
                    DEFAULT_SETTINGS["limits"]["maxPdfFiles"],
                )
            ),
            "maxCharsPerPdf": int(
                clamp_number(
                    limits.get("maxCharsPerPdf"),
                    5000,
                    200000,
                    DEFAULT_SETTINGS["limits"]["maxCharsPerPdf"],
                )
            ),
        },
    }


def load_settings() -> Dict[str, Any]:
    path = get_settings_path()
    if not path.exists():
        return sanitize_settings({})

    try:
        with path.open("r", encoding="utf-8") as file:
            return sanitize_settings(json.load(file))
    except (OSError, json.JSONDecodeError):
        return sanitize_settings({})


def save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = sanitize_settings(settings)
    path = get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(sanitized, file, ensure_ascii=False, indent=2)

    return sanitized


def reset_settings() -> Dict[str, Any]:
    path = get_settings_path()
    if path.exists():
        path.unlink()
    return sanitize_settings({})


def ensure_api_settings(settings: Dict[str, Any]) -> None:
    llm = sanitize_settings(settings)["llm"]
    missing = []

    if not llm["apiUrl"]:
        missing.append("API URL")
    if not llm["apiKey"]:
        missing.append("API Key")
    if not llm["model"]:
        missing.append("模型名")

    if missing:
        raise ValueError(f"请先在设置中填写：{', '.join(missing)}。")


def normalize_api_url(url: str) -> str:
    trimmed = str(url or "").strip().rstrip("/")
    if not trimmed:
        return ""
    if trimmed.lower().endswith("/chat/completions"):
        return trimmed
    if trimmed.lower().endswith("/v1"):
        return f"{trimmed}/chat/completions"
    if trimmed.startswith("http://") or trimmed.startswith("https://"):
        slash_count = trimmed.count("/")
        if slash_count <= 2:
            return f"{trimmed}/v1/chat/completions"
    return f"{trimmed}/chat/completions"
