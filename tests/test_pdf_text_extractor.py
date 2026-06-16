from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from phdfloating import pdf_text_extractor as extractor


class StreamExtractedPdfPayloadsTests(unittest.TestCase):
    def test_stream_yields_completion_order_and_preserves_original_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = [
                root / "slow.pdf",
                root / "fast.pdf",
                root / "mid.pdf",
            ]
            for path in paths:
                path.write_bytes(b"%PDF-1.4\n")

            sleep_by_name = {
                "slow.pdf": 0.18,
                "fast.pdf": 0.01,
                "mid.pdf": 0.08,
            }
            events = []

            def fake_extract(path: str, max_chars: int = 0) -> str:
                del max_chars
                time.sleep(sleep_by_name[Path(path).name])
                return f"text for {Path(path).name}"

            with mock.patch.object(
                extractor.concurrent.futures,
                "ProcessPoolExecutor",
                side_effect=OSError("process pool unavailable"),
            ), mock.patch.object(extractor, "extract_pdf_text", side_effect=fake_extract):
                payloads = list(
                    extractor.stream_extracted_pdf_payloads(
                        [str(path) for path in paths],
                        max_chars_per_pdf=500,
                        workers=3,
                        event_callback=lambda event, payload: events.append((event, payload["file_name"])),
                    )
                )

        self.assertEqual([payload.file_name for payload in payloads], ["fast.pdf", "mid.pdf", "slow.pdf"])
        self.assertEqual([payload.index for payload in payloads], [1, 2, 0])
        self.assertEqual(
            [event for event in events if event[0] == "extract_start"],
            [
                ("extract_start", "slow.pdf"),
                ("extract_start", "fast.pdf"),
                ("extract_start", "mid.pdf"),
            ],
        )
        self.assertEqual(
            [event for event in events if event[0] == "extract_success"],
            [
                ("extract_success", "fast.pdf"),
                ("extract_success", "mid.pdf"),
                ("extract_success", "slow.pdf"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
