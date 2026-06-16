"""OpenAI-compatible chat completion client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Sequence

from .prompt_builder import build_messages, parse_summary_json
from .settings_store import ensure_api_settings, normalize_api_url, sanitize_settings


SUMMARY_FAILURE_TEXT = "无法总结"


def build_summary_failure_placeholder(file_name: str) -> Dict[str, Any]:
    name = str(file_name or "PDF").strip() or "PDF"
    return {
        "name": name,
        "summary": SUMMARY_FAILURE_TEXT,
        "keywords": [],
    }


class OpenAICompatibleLLMClient:
    def __init__(self, settings: Dict[str, Any]):
        sanitized = sanitize_settings(settings)
        ensure_api_settings(sanitized)
        llm = sanitized["llm"]

        self.api_url = normalize_api_url(llm["apiUrl"])
        self.api_key = llm["apiKey"]
        self.model = llm["model"]
        self.temperature = float(llm["temperature"])
        self.timeout_seconds = max(5, int(llm["requestTimeoutMs"]) / 1000)
        self.summary_language = llm["summaryLanguage"]

    def summarize_input_json_list(self, input_json_list: Sequence[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Summarize PDFs one by one and return a merged JSON list."""

        results: List[Dict[str, Any]] = []
        for item in input_json_list:
            results.append(self.summarize_single_input(item))
        return results

    def summarize_single_input(self, item: Dict[str, str]) -> Dict[str, Any]:
        file_name = str(item.get("file_name") or "未命名 PDF")
        completion = self.request_chat_completion(
            build_messages([item], summary_language=self.summary_language),
            max_tokens=900,
        )
        parsed = parse_summary_json(completion)
        if not parsed:
            raise RuntimeError("模型没有返回可读总结。")

        result = parsed[0]
        if not result.get("name"):
            result["name"] = file_name
        return result

    def request_chat_completion(
        self,
        messages: Sequence[Dict[str, str]],
        max_tokens: int = 1200,
    ) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "messages": list(messages),
        }

        response_payload = self._post_json(payload)
        content = _extract_message_content(response_payload)
        if not content:
            raise RuntimeError("模型没有返回可读取的内容。")
        return content

    def _post_json(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import requests  # type: ignore

            try:
                response = requests.post(
                    self.api_url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            except requests.Timeout:
                raise RuntimeError("模型请求超时，请增大超时时间或减少本次 PDF 数量。")
            except requests.RequestException as error:
                raise RuntimeError(f"模型请求失败：{error}")

            try:
                response_payload = response.json()
            except ValueError:
                response_payload = {}

            if not response.ok:
                raise RuntimeError(_extract_api_error(response.status_code, response_payload, self.api_url))
            return response_payload
        except ImportError:
            return self._post_json_with_urllib(payload)

    def _post_json_with_urllib(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                response_payload = json.loads(error.read().decode("utf-8"))
            except Exception:
                response_payload = {}
            raise RuntimeError(_extract_api_error(error.code, response_payload, self.api_url))
        except urllib.error.URLError as error:
            raise RuntimeError(f"模型请求失败：{error.reason}")


def _extract_message_content(payload: Dict[str, Any]) -> str:
    message = (payload.get("choices") or [{}])[0].get("message") or {}
    content = message.get("content")

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text") or ""))
        return "".join(chunks)
    return ""


def _extract_api_error(status_code: int, payload: Dict[str, Any], api_url: str) -> str:
    message = (
        ((payload.get("error") or {}).get("message") if isinstance(payload.get("error"), dict) else "")
        or payload.get("message")
        or f"HTTP {status_code}"
    )
    if status_code == 404:
        return f"模型请求失败 404。当前请求地址是：{api_url}"
    return f"模型请求失败：{message}"
