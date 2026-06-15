"""Local settings storage compatible with OpenAI-style chat completion APIs."""

from __future__ import annotations

import copy
import base64
import ctypes
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
        "summaryLanguage": "zh-CN",
    },
    "limits": {
        "maxPdfFiles": 5,
        "maxCharsPerPdf": 60000,
    },
}

SUMMARY_LANGUAGE_OPTIONS = {"zh-CN", "en"}

API_KEY_ENCRYPTED_FIELD = "apiKeyEncrypted"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def get_config_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def get_settings_path() -> Path:
    return get_config_dir() / SETTINGS_FILE_NAME


def _create_blob(data: bytes) -> _DataBlob:
    if not data:
        return _DataBlob(0, ctypes.POINTER(ctypes.c_ubyte)())
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    blob._buffer = buffer  # type: ignore[attr-defined]
    return blob


def _read_blob(blob: _DataBlob) -> bytes:
    if not blob.cbData or not blob.pbData:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


def _crypt_protect_data(raw: bytes) -> bytes:
    if os.name != "nt" or not raw:
        return raw

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob = _create_blob(raw)
    output_blob = _DataBlob()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        flags,
        ctypes.byref(output_blob),
    ):
        raise OSError("Failed to encrypt API key with Windows DPAPI.")

    try:
        return _read_blob(output_blob)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(output_blob.pbData)


def _crypt_unprotect_data(encrypted: bytes) -> bytes:
    if os.name != "nt" or not encrypted:
        return encrypted

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob = _create_blob(encrypted)
    output_blob = _DataBlob()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        flags,
        ctypes.byref(output_blob),
    ):
        raise OSError("Failed to decrypt API key from Windows DPAPI.")

    try:
        return _read_blob(output_blob)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(output_blob.pbData)


def _encrypt_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    protected = _crypt_protect_data(api_key.encode("utf-8"))
    return base64.b64encode(protected).decode("ascii")


def _decrypt_api_key(encoded_value: str) -> str:
    if not encoded_value:
        return ""
    raw = base64.b64decode(encoded_value.encode("ascii"))
    return _crypt_unprotect_data(raw).decode("utf-8")


def _prepare_settings_for_disk(settings: Dict[str, Any]) -> Dict[str, Any]:
    disk_settings = copy.deepcopy(settings)
    llm = disk_settings.setdefault("llm", {})
    api_key = str(llm.get("apiKey") or "").strip()
    llm["apiKey"] = ""
    llm[API_KEY_ENCRYPTED_FIELD] = _encrypt_api_key(api_key)
    return disk_settings


def _inflate_settings_from_disk(raw_settings: Dict[str, Any]) -> Dict[str, Any]:
    inflated = copy.deepcopy(raw_settings or {})
    llm = inflated.setdefault("llm", {})
    encrypted_value = str(llm.get(API_KEY_ENCRYPTED_FIELD) or "").strip()
    plaintext_value = str(llm.get("apiKey") or "").strip()
    if encrypted_value:
        try:
            llm["apiKey"] = _decrypt_api_key(encrypted_value)
        except Exception:
            llm["apiKey"] = plaintext_value
    else:
        llm["apiKey"] = plaintext_value
    llm.pop(API_KEY_ENCRYPTED_FIELD, None)
    return inflated


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
    merged = deep_merge(DEFAULT_SETTINGS, _inflate_settings_from_disk(raw_settings or {}))
    llm = merged.get("llm") or {}
    limits = merged.get("limits") or {}
    summary_language = str(llm.get("summaryLanguage") or DEFAULT_SETTINGS["llm"]["summaryLanguage"]).strip()
    if summary_language not in SUMMARY_LANGUAGE_OPTIONS:
        summary_language = DEFAULT_SETTINGS["llm"]["summaryLanguage"]

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
            "summaryLanguage": summary_language,
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
            raw_settings = json.load(file)
            sanitized = sanitize_settings(raw_settings)
            llm = (raw_settings or {}).get("llm") or {}
            has_plaintext_key = bool(str(llm.get("apiKey") or "").strip())
            has_encrypted_key = bool(str(llm.get(API_KEY_ENCRYPTED_FIELD) or "").strip())
            if has_plaintext_key or (has_encrypted_key and llm.get("apiKey")):
                try:
                    save_settings(sanitized)
                except OSError:
                    pass
            return sanitized
    except (OSError, json.JSONDecodeError):
        return sanitize_settings({})


def save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = sanitize_settings(settings)
    path = get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(_prepare_settings_for_disk(sanitized), file, ensure_ascii=False, indent=2)

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
