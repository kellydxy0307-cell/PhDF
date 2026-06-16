"""Producer-consumer pipeline for overlapping extraction and summarization."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

from .llm_client import OpenAICompatibleLLMClient, build_summary_failure_placeholder
from .pdf_text_extractor import ExtractedPdfPayload, stream_extracted_pdf_payloads
from .pipeline_logger import get_pipeline_logger
from .summary_pdf_writer import write_summary_pdf


PIPELINE_QUEUE_SIZE = 3
_SENTINEL = object()


@dataclass
class SummaryPipelineResult:
    output_path: Path
    warnings: List[str]
    summaries: List[Dict[str, Any]]


def run_summary_pipeline(
    settings: Dict[str, Any],
    accepted_paths: Sequence[str],
    max_chars_per_pdf: int,
    queue_size: int = PIPELINE_QUEUE_SIZE,
    stream_extractor: Callable[..., Iterator[ExtractedPdfPayload]] = stream_extracted_pdf_payloads,
    client_factory: Callable[[Dict[str, Any]], OpenAICompatibleLLMClient] = OpenAICompatibleLLMClient,
    writer: Callable[..., Path] = write_summary_pdf,
    logger: Optional[logging.Logger] = None,
) -> SummaryPipelineResult:
    paths = list(accepted_paths or [])
    if not paths:
        raise ValueError("No accepted PDFs to process.")

    pipeline_logger = logger or get_pipeline_logger()
    summary_language = str((settings.get("llm") or {}).get("summaryLanguage") or "zh-CN")
    warnings: List[str] = []
    summaries: List[Optional[Dict[str, Any]]] = [None] * len(paths)
    payload_queue: "queue.Queue[object]" = queue.Queue(maxsize=max(1, queue_size))
    producer_errors: List[BaseException] = []
    batch_started_at = time.monotonic()
    client = client_factory(settings)

    pipeline_logger.info(
        "batch start total=%s max_chars_per_pdf=%s queue_size=%s summary_concurrency=1",
        len(paths),
        max_chars_per_pdf,
        queue_size,
    )

    def on_extract_event(event: str, payload: Dict[str, Any]) -> None:
        file_name = str(payload.get("file_name") or Path(str(payload.get("path") or "")).name)
        index = int(payload.get("index") or 0)
        elapsed_seconds = float(payload.get("elapsed_seconds") or 0.0)
        if event == "extract_start":
            pipeline_logger.info("extract start index=%s file=%s", index, file_name)
        elif event == "extract_success":
            pipeline_logger.info(
                "extract success index=%s file=%s elapsed=%.3fs chars=%s",
                index,
                file_name,
                elapsed_seconds,
                len(str(payload.get("pdf_content") or "")),
            )
        elif event == "extract_fail":
            pipeline_logger.warning(
                "extract fail index=%s file=%s elapsed=%.3fs error=%s",
                index,
                file_name,
                elapsed_seconds,
                str(payload.get("extraction_error") or ""),
            )

    def producer() -> None:
        try:
            for payload in stream_extractor(
                paths,
                max_chars_per_pdf=max_chars_per_pdf,
                event_callback=on_extract_event,
            ):
                payload_queue.put(payload)
        except Exception as error:
            producer_errors.append(error)
            pipeline_logger.exception("batch extract fatal error: %s", error)
        finally:
            payload_queue.put(_SENTINEL)

    producer_thread = threading.Thread(
        target=producer,
        daemon=True,
        name="phdf-extract-producer",
    )
    producer_thread.start()
    success_count = 0
    placeholder_count = 0

    while True:
        item = payload_queue.get()
        if item is _SENTINEL:
            break

        payload = item
        assert isinstance(payload, ExtractedPdfPayload)

        if payload.extraction_error:
            warnings.append(f"{payload.file_name}: {payload.extraction_error}")
            summaries[payload.index] = build_summary_failure_placeholder(payload.file_name)
            placeholder_count += 1
            continue

        pipeline_logger.info("summarize start index=%s file=%s", payload.index, payload.file_name)
        summarize_started_at = time.monotonic()
        try:
            summaries[payload.index] = client.summarize_single_input(payload.to_input_item())
            success_count += 1
            pipeline_logger.info(
                "summarize success index=%s file=%s elapsed=%.3fs",
                payload.index,
                payload.file_name,
                time.monotonic() - summarize_started_at,
            )
        except Exception as error:
            warnings.append(f"{payload.file_name}: 无法总结")
            summaries[payload.index] = build_summary_failure_placeholder(payload.file_name)
            placeholder_count += 1
            pipeline_logger.exception(
                "summarize fail index=%s file=%s elapsed=%.3fs error=%s",
                payload.index,
                payload.file_name,
                time.monotonic() - summarize_started_at,
                error,
            )

    producer_thread.join()

    if producer_errors:
        raise RuntimeError(str(producer_errors[0]))

    if any(item is None for item in summaries):
        raise RuntimeError("Pipeline finished before all PDFs produced summary results.")

    final_summaries = [item for item in summaries if item is not None]
    output_path = writer(final_summaries, summary_language=summary_language)

    pipeline_logger.info(
        "batch end total=%s success=%s placeholders=%s elapsed=%.3fs output=%s",
        len(paths),
        success_count,
        placeholder_count,
        time.monotonic() - batch_started_at,
        output_path,
    )

    return SummaryPipelineResult(
        output_path=output_path,
        warnings=warnings,
        summaries=final_summaries,
    )
