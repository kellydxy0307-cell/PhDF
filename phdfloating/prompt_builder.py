"""Prompt construction for paper summarization."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Sequence


SYSTEM_PROMPT_TEMPLATE = """你是一个有着多年行业经验的 Nature 审稿人，你博闻强识，阅读过许多不同领域的论文，涵盖数学、物理、化学、生物、材料等。你最擅长的是把一篇复杂的论文用一段话进行概括总结，言辞通俗易懂，公式使用 LaTeX 语法正确表达且不随意改动其符号含义。你需要遵循以下原则：

1. **多模态理解**：如果论文包含大段数学公式，你必须解析公式的核心含义（如变换、估计、收敛性、对称性等），并将其作为总结的“主语”或核心结论，而非忽略公式只总结稀少的文本。
2. **通用结构化叙事**：无论学科，总结必须揭示“动机/问题 -> 核心思想/方法 -> 关键结果/物理/化学/生物意义”的逻辑链。对于公式密集论文，动机和结果可能直接由公式定义和定理体现。
3. **符号转译**：在通俗化转述时，将关键数学对象或变量用 **粗体名词** 表达其数学或物理含义（例如：**薛定谔方程的孤子解**、**高温超导序参量**、**CRISPR-Cas9 脱靶效应**），而非单纯罗列符号。对于生化环材，照常对 **核心概念** 加粗。
4. **语言一致性**：你的总结语言必须严格使用用户指定的总结语言：{summary_language_label}。如果用户选择中文简体，则整份总结和关键词只使用中文简体；如果用户选择 English，则整份总结和关键词只使用 English。对于中文学术场景中常见的标准英文术语（例如 Kolmogorov-Sinai entropy），可以保留其惯用英文写法。
5. **关键词提取**：你必须根据每篇论文的输入内容提取 3-5 个专业关键词，尽量具体、专业、可检索，避免空泛词语如“研究”“方法”“结果”。
6. **输出格式**：严格输出为 JSON 数组：`[{{"name":"论文的标题","summary":"论文的总结","keywords":["关键词1","关键词2","关键词3"]}}]`。每篇论文的总结不少于 10 个 token，不多于 600 个 token。
7. **只输出 JSON 数组**，不要输出解释、前后缀、代码块或额外文本。"""

SUMMARY_LANGUAGE_LABELS = {
    "zh-CN": "中文简体",
    "en": "English",
}


def estimate_tokens(value: str) -> int:
    normalized = re.sub(r"\s+", " ", value or "").strip()
    return max(1, int(len(normalized) * 1.15))


def build_system_prompt(summary_language: str) -> str:
    label = SUMMARY_LANGUAGE_LABELS.get(summary_language, SUMMARY_LANGUAGE_LABELS["zh-CN"])
    return SYSTEM_PROMPT_TEMPLATE.format(summary_language_label=label)


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


def build_messages(
    input_json_list: Sequence[Dict[str, str]],
    summary_language: str = "zh-CN",
) -> List[Dict[str, str]]:
    return [
        {
            "role": "system",
            "content": build_system_prompt(summary_language),
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


def parse_summary_json(value: str) -> List[Dict[str, Any]]:
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

    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个总结不是 JSON 对象。")
        name = str(item.get("name") or item.get("title") or item.get("论文的标题") or "").strip()
        summary = str(item.get("summary") or item.get("论文的总结") or "").strip()
        keywords = _normalize_keywords(item.get("keywords") or item.get("关键词") or item.get("keyword"))
        if not name:
            raise ValueError(f"第 {index} 个总结缺少 name 字段。")
        if not summary:
            raise ValueError(f"第 {index} 个总结缺少 summary 字段。")
        if not keywords:
            raise ValueError(f"第 {index} 个总结缺少 keywords 字段。")
        normalized.append({"name": name, "summary": summary, "keywords": keywords})

    return normalized


def _normalize_keywords(value: Any) -> List[str]:
    if isinstance(value, list):
        items = [str(item or "").strip() for item in value]
    else:
        text = str(value or "").strip()
        if not text:
            return []
        items = re.split(r"[\n,，;；]+", text)

    keywords: List[str] = []
    for item in items:
        cleaned = str(item or "").strip()
        if cleaned and cleaned not in keywords:
            keywords.append(cleaned)
    return keywords[:5]
