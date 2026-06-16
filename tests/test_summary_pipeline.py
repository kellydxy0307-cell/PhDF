from __future__ import annotations

import logging
import time
import unittest
from pathlib import Path
from typing import Dict, Iterator, List

from phdfloating.pdf_text_extractor import ExtractedPdfPayload
from phdfloating.summary_pipeline import SummaryPipelineResult, run_summary_pipeline


def _test_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


class SummaryPipelineTests(unittest.TestCase):
    def test_pipeline_overlaps_extraction_with_summarization_and_preserves_output_order(self) -> None:
        events: List[str] = []
        written: Dict[str, List[Dict[str, object]]] = {}

        def fake_stream(
            accepted_paths: List[str],
            max_chars_per_pdf: int,
            event_callback=None,
            **_: object,
        ) -> Iterator[ExtractedPdfPayload]:
            del max_chars_per_pdf
            first = ExtractedPdfPayload(0, accepted_paths[0], "paper1.pdf", "content 1")
            if event_callback:
                event_callback("extract_start", {"index": 0, "path": accepted_paths[0], "file_name": "paper1.pdf"})
                event_callback(
                    "extract_success",
                    {
                        "index": 0,
                        "path": accepted_paths[0],
                        "file_name": "paper1.pdf",
                        "pdf_content": "content 1",
                        "extraction_error": "",
                        "elapsed_seconds": 0.01,
                    },
                )
            events.append("extract_done_paper1")
            yield first

            time.sleep(0.05)

            second = ExtractedPdfPayload(1, accepted_paths[1], "paper2.pdf", "content 2")
            if event_callback:
                event_callback("extract_start", {"index": 1, "path": accepted_paths[1], "file_name": "paper2.pdf"})
                event_callback(
                    "extract_success",
                    {
                        "index": 1,
                        "path": accepted_paths[1],
                        "file_name": "paper2.pdf",
                        "pdf_content": "content 2",
                        "extraction_error": "",
                        "elapsed_seconds": 0.02,
                    },
                )
            events.append("extract_done_paper2")
            yield second

        class FakeClient:
            def summarize_single_input(self, item: Dict[str, str]) -> Dict[str, object]:
                file_name = item["file_name"]
                events.append(f"summarize_start_{file_name}")
                time.sleep(0.15)
                events.append(f"summarize_end_{file_name}")
                return {"name": file_name, "summary": "ok", "keywords": ["k"]}

        def fake_writer(summaries, summary_language="zh-CN") -> Path:
            del summary_language
            written["summaries"] = list(summaries)
            return Path("summary.pdf")

        result = run_summary_pipeline(
            {"llm": {"summaryLanguage": "zh-CN"}},
            ["paper1.pdf", "paper2.pdf"],
            max_chars_per_pdf=500,
            stream_extractor=fake_stream,
            client_factory=lambda settings: FakeClient(),
            writer=fake_writer,
            logger=_test_logger("summary_pipeline_overlap"),
        )

        self.assertIsInstance(result, SummaryPipelineResult)
        self.assertLess(
            events.index("summarize_start_paper1.pdf"),
            events.index("extract_done_paper2"),
        )
        self.assertLess(
            events.index("extract_done_paper2"),
            events.index("summarize_end_paper1.pdf"),
        )
        self.assertEqual(
            [item["name"] for item in written["summaries"]],
            ["paper1.pdf", "paper2.pdf"],
        )

    def test_extraction_failure_creates_placeholder_and_skips_llm_call(self) -> None:
        written: Dict[str, List[Dict[str, object]]] = {}
        summarized_files: List[str] = []

        def fake_stream(
            accepted_paths: List[str],
            max_chars_per_pdf: int,
            **_: object,
        ) -> Iterator[ExtractedPdfPayload]:
            del accepted_paths, max_chars_per_pdf
            yield ExtractedPdfPayload(0, "bad.pdf", "bad.pdf", "", extraction_error="ocr failed")
            yield ExtractedPdfPayload(1, "good.pdf", "good.pdf", "good content")

        class FakeClient:
            def summarize_single_input(self, item: Dict[str, str]) -> Dict[str, object]:
                summarized_files.append(item["file_name"])
                return {"name": item["file_name"], "summary": "ok", "keywords": ["k"]}

        def fake_writer(summaries, summary_language="zh-CN") -> Path:
            del summary_language
            written["summaries"] = list(summaries)
            return Path("summary.pdf")

        result = run_summary_pipeline(
            {"llm": {"summaryLanguage": "zh-CN"}},
            ["bad.pdf", "good.pdf"],
            max_chars_per_pdf=500,
            stream_extractor=fake_stream,
            client_factory=lambda settings: FakeClient(),
            writer=fake_writer,
            logger=_test_logger("summary_pipeline_extract_failure"),
        )

        self.assertEqual(summarized_files, ["good.pdf"])
        self.assertEqual(written["summaries"][0]["name"], "bad.pdf")
        self.assertEqual(written["summaries"][0]["summary"], "无法总结")
        self.assertIn("bad.pdf: ocr failed", result.warnings)

    def test_summary_failure_creates_placeholder_and_keeps_batch_running(self) -> None:
        written: Dict[str, List[Dict[str, object]]] = {}

        def fake_stream(
            accepted_paths: List[str],
            max_chars_per_pdf: int,
            **_: object,
        ) -> Iterator[ExtractedPdfPayload]:
            del accepted_paths, max_chars_per_pdf
            yield ExtractedPdfPayload(0, "bad.pdf", "bad.pdf", "bad content")
            yield ExtractedPdfPayload(1, "good.pdf", "good.pdf", "good content")

        class FakeClient:
            def summarize_single_input(self, item: Dict[str, str]) -> Dict[str, object]:
                if item["file_name"] == "bad.pdf":
                    raise RuntimeError("llm failed")
                return {"name": item["file_name"], "summary": "ok", "keywords": ["k"]}

        def fake_writer(summaries, summary_language="zh-CN") -> Path:
            del summary_language
            written["summaries"] = list(summaries)
            return Path("summary.pdf")

        result = run_summary_pipeline(
            {"llm": {"summaryLanguage": "zh-CN"}},
            ["bad.pdf", "good.pdf"],
            max_chars_per_pdf=500,
            stream_extractor=fake_stream,
            client_factory=lambda settings: FakeClient(),
            writer=fake_writer,
            logger=_test_logger("summary_pipeline_summary_failure"),
        )

        self.assertEqual(written["summaries"][0]["summary"], "无法总结")
        self.assertEqual(written["summaries"][1]["name"], "good.pdf")
        self.assertIn("bad.pdf: 无法总结", result.warnings)


if __name__ == "__main__":
    unittest.main()
