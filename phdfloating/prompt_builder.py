"""Prompt construction for paper summarization."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Sequence


SYSTEM_PROMPT = """你是一个有着多年行业经验的 Nature 审稿人，你博闻强识，阅读过许多不同领域的论文。你最擅长的是把一篇复杂的论文用一段话进行概括总结，言辞通俗易懂，公式正确不乱修改。你需要注意：
- 你的总结语言和论文的语言应当保持一致。
- 你的总结必须严格输出为 JSON 数组：[{"name":"论文的标题","summary":"论文的总结"}]。
- 每篇论文的总结不少于 10 个 token，也不多于 300 个 token。
- 请在重点名词处使用 Markdown 加粗标记，例如 **核心概念**。
- 请给 input_json_list 内每一篇论文都写一个总结。
- 只输出 JSON 数组，不要输出解释、前后缀、代码块或额外文本。"""


def estimate_tokens(value: str) -> int:
    normalized = re.sub(r"\s+", " ", value or "").strip()
    return max(1, int(len(normalized) * 1.15))


def build_user_message(input_json_list: Sequence[Dict[str, str]]) -> str:
    payload = {
        "input_json_list": [
            {
                "file_name": str(item.get("file_name") or ""),
                "pdf_content": str(item.get("pdf_content") or ""),
            }
            for item in input_json_list
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


def build_messages(input_json_list: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": build_user_message(input_json_list),
        },
    ]


def strip_code_fence(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_summary_json(value: str) -> List[Dict[str, str]]:
    text = strip_code_fence(value)
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("模型返回内容不是合法 JSON 数组。")
        payload = json.loads(text[start : end + 1])

    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("模型返回内容不是 JSON 数组。")

    normalized: List[Dict[str, str]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个总结不是 JSON 对象。")
        name = str(item.get("name") or item.get("title") or item.get("论文的标题") or "").strip()
        summary = str(item.get("summary") or item.get("论文的总结") or "").strip()
        if not name:
            raise ValueError(f"第 {index} 个总结缺少 name 字段。")
        if not summary:
            raise ValueError(f"第 {index} 个总结缺少 summary 字段。")
        normalized.append({"name": name, "summary": summary})

    return normalized
