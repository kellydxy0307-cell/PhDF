"""Fast local PDF-to-text extraction for the floating summary app.

Public contract:

    input_json_list = [
        {"file_name": "paper.pdf", "pdf_content": "..."},
    ]

The module keeps UI and LLM logic out. Its job is to convert local PDFs into
LLM-ready text as quickly as possible while staying entirely local.
"""

from __future__ import annotations

import concurrent.futures
from collections import Counter
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence


DEFAULT_MAX_PDF_FILES = 15
DEFAULT_MAX_CHARS_PER_PDF = 50000
DEFAULT_MAX_OCR_PAGES = 120
DEFAULT_OCR_RENDER_WIDTH = 1400
DEFAULT_OCR_TIMEOUT_SECONDS = 360
CPU_UTILIZATION_LIMIT = 0.8

TRAILING_SECTION_HEADERS = (
    "references",
    "bibliography",
    "acknowledgements",
    "acknowledgments",
    "appendix",
    "appendices",
    "supplementary material",
    "supplementary materials",
    "supplementary information",
    "supporting information",
)


class PdfExtractionError(RuntimeError):
    """Raised when a PDF cannot be converted to useful local text."""


@dataclass
class PreparedPdfBatch:
    accepted_paths: List[str]
    ignored_paths: List[str]

    @property
    def truncated(self) -> bool:
        return bool(self.ignored_paths)


@dataclass
class ExtractedPdfPayload:
    index: int
    path: str
    file_name: str
    pdf_content: str
    extraction_error: str = ""
    elapsed_seconds: float = 0.0

    def to_input_item(self) -> Dict[str, str]:
        return {
            "file_name": self.file_name,
            "pdf_content": self.pdf_content,
        }


@dataclass
class ExtractionResult:
    input_json_list: List[Dict[str, str]]
    accepted_paths: List[str]
    ignored_paths: List[str]
    errors: List[str]

    @property
    def truncated(self) -> bool:
        return bool(self.ignored_paths)


@dataclass
class EngineResult:
    engine: str
    text: str
    ok: bool
    message: str = ""


_MODULE_CACHE: Dict[str, bool] = {}


def prepare_pdf_batch(
    file_paths: Sequence[str],
    max_files: int = DEFAULT_MAX_PDF_FILES,
) -> PreparedPdfBatch:
    pdf_paths = _normalize_pdf_paths(file_paths)
    return PreparedPdfBatch(
        accepted_paths=pdf_paths[:max_files],
        ignored_paths=pdf_paths[max_files:],
    )


def build_input_json_list(
    file_paths: Sequence[str],
    max_files: int = DEFAULT_MAX_PDF_FILES,
    max_chars_per_pdf: int = DEFAULT_MAX_CHARS_PER_PDF,
) -> ExtractionResult:
    """Extract text from up to ``max_files`` PDFs and return LLM-ready JSON."""

    batch = prepare_pdf_batch(file_paths, max_files=max_files)
    accepted_paths = batch.accepted_paths
    ignored_paths = batch.ignored_paths

    if not accepted_paths:
        return ExtractionResult(
            input_json_list=[],
            accepted_paths=[],
            ignored_paths=ignored_paths,
            errors=[],
        )

    workers = _compute_file_worker_limit(len(accepted_paths))
    ordered_results = _collect_extracted_pdf_payloads(accepted_paths, max_chars_per_pdf, workers)

    input_json_list: List[Dict[str, str]] = []
    errors: List[str] = []

    for payload in ordered_results:
        if payload.extraction_error:
            errors.append(f"{payload.file_name}: {payload.extraction_error}")
            text = f"Unable to extract readable text from this PDF. Reason: {payload.extraction_error}"
        else:
            text = payload.pdf_content

        input_json_list.append(
            {
                "file_name": payload.file_name,
                "pdf_content": text if not payload.extraction_error else _limit_text(_normalize_text(text), max_chars_per_pdf),
            }
        )

    return ExtractionResult(
        input_json_list=input_json_list,
        accepted_paths=accepted_paths,
        ignored_paths=ignored_paths,
        errors=errors,
    )


def extract_pdf_text(file_path: str, max_chars: int = DEFAULT_MAX_CHARS_PER_PDF) -> str:
    """Return plain text/Markdown for one PDF using local resources only."""

    path = Path(file_path)
    if not path.exists():
        raise PdfExtractionError(f"File does not exist: {path}")
    if path.suffix.lower() != ".pdf":
        raise PdfExtractionError(f"Not a PDF file: {path}")

    attempts: List[EngineResult] = []

    fast_engines: List[Callable[[Path, int], str]] = [
        _extract_with_pdf_oxide,
        _extract_with_pymupdf,
        _extract_with_pypdf,
        _extract_with_pdftotext_cli,
    ]

    slow_engines: List[Callable[[Path, int], str]] = [
        _extract_with_docling,
        _extract_with_windows_ocr,
    ]

    engine_sequence = fast_engines + slow_engines

    for engine in engine_sequence:
        result = _run_engine(engine, path, max_chars)
        attempts.append(result)
        if result.ok:
            return _finalize_text(result.text)

    detail = "; ".join(
        f"{result.engine}: {result.message}" for result in attempts if result.message
    )
    raise PdfExtractionError(
        "No local extractor returned usable text."
        + (f" Diagnostics: {detail}" if detail else "")
    )


def stream_extracted_pdf_payloads(
    accepted_paths: Sequence[str],
    max_chars_per_pdf: int = DEFAULT_MAX_CHARS_PER_PDF,
    workers: Optional[int] = None,
    event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Iterator[ExtractedPdfPayload]:
    paths = list(accepted_paths or [])
    if not paths:
        return

    worker_limit = workers if workers is not None else _compute_file_worker_limit(len(paths))
    yield from _stream_extracted_pdf_payloads_parallel(
        paths,
        max_chars_per_pdf,
        worker_limit,
        event_callback=event_callback,
    )


def _collect_extracted_pdf_payloads(
    accepted_paths: Sequence[str],
    max_chars_per_pdf: int,
    workers: int,
) -> List[ExtractedPdfPayload]:
    ordered = list(
        stream_extracted_pdf_payloads(
            accepted_paths,
            max_chars_per_pdf=max_chars_per_pdf,
            workers=workers,
        )
    )
    ordered.sort(key=lambda item: item.index)
    return ordered


def _stream_extracted_pdf_payloads_parallel(
    accepted_paths: Sequence[str],
    max_chars_per_pdf: int,
    workers: int,
    event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Iterator[ExtractedPdfPayload]:
    if len(accepted_paths) == 1 or workers <= 1:
        for index, path in enumerate(accepted_paths):
            _emit_extraction_event(
                event_callback,
                "extract_start",
                index=index,
                path=path,
                file_name=Path(path).name,
            )
            payload = _extract_single_payload(index, path, max_chars_per_pdf)
            _emit_extraction_payload_event(event_callback, payload)
            yield payload
        return

    executor_kinds = [
        concurrent.futures.ProcessPoolExecutor,
        concurrent.futures.ThreadPoolExecutor,
    ]
    last_error: Optional[BaseException] = None

    for executor_kind in executor_kinds:
        try:
            with executor_kind(max_workers=workers) as executor:
                futures = []
                for index, path in enumerate(accepted_paths):
                    _emit_extraction_event(
                        event_callback,
                        "extract_start",
                        index=index,
                        path=path,
                        file_name=Path(path).name,
                    )
                    futures.append(
                        executor.submit(_extract_single_payload, index, path, max_chars_per_pdf)
                    )
                for future in concurrent.futures.as_completed(futures):
                    payload = future.result()
                    _emit_extraction_payload_event(event_callback, payload)
                    yield payload
            return
        except (PermissionError, OSError) as error:
            last_error = error
            continue

    if last_error is not None:
        for index, path in enumerate(accepted_paths):
            _emit_extraction_event(
                event_callback,
                "extract_start",
                index=index,
                path=path,
                file_name=Path(path).name,
            )
            payload = _extract_single_payload(index, path, max_chars_per_pdf)
            _emit_extraction_payload_event(event_callback, payload)
            yield payload


def _extract_single_payload(index: int, path: str, max_chars: int) -> ExtractedPdfPayload:
    started_at = time.monotonic()
    file_name = Path(path).name
    try:
        text = extract_pdf_text(path, max_chars=max_chars)
        pdf_content = _limit_text(_normalize_text(text), max_chars)
        return ExtractedPdfPayload(
            index=index,
            path=path,
            file_name=file_name,
            pdf_content=pdf_content,
            elapsed_seconds=time.monotonic() - started_at,
        )
    except Exception as error:
        return ExtractedPdfPayload(
            index=index,
            path=path,
            file_name=file_name,
            pdf_content="",
            extraction_error=str(error),
            elapsed_seconds=time.monotonic() - started_at,
        )


def _emit_extraction_event(
    callback: Optional[Callable[[str, Dict[str, Any]], None]],
    event: str,
    **payload: Any,
) -> None:
    if callback is None:
        return
    callback(event, payload)


def _emit_extraction_payload_event(
    callback: Optional[Callable[[str, Dict[str, Any]], None]],
    payload: ExtractedPdfPayload,
) -> None:
    event = "extract_fail" if payload.extraction_error else "extract_success"
    _emit_extraction_event(
        callback,
        event,
        index=payload.index,
        path=payload.path,
        file_name=payload.file_name,
        pdf_content=payload.pdf_content,
        extraction_error=payload.extraction_error,
        elapsed_seconds=payload.elapsed_seconds,
    )


def _compute_file_worker_limit(file_count: int) -> int:
    cpu_count = os.cpu_count() or 1
    reserved_limit = max(1, int(math.floor(cpu_count * CPU_UTILIZATION_LIMIT)))
    return max(1, min(file_count, reserved_limit))


def _run_engine(
    engine: Callable[[Path, int], str],
    path: Path,
    max_chars: int,
) -> EngineResult:
    engine_name = engine.__name__.replace("_extract_with_", "")
    try:
        text = _normalize_text(engine(path, max_chars))
        if _has_meaningful_text(text):
            return EngineResult(engine=engine_name, text=text, ok=True)
        return EngineResult(
            engine=engine_name,
            text=text,
            ok=False,
            message="extractor returned too little usable text",
        )
    except Exception as error:
        return EngineResult(engine=engine_name, text="", ok=False, message=str(error))


def _extract_with_pdf_oxide(path: Path, max_chars: int) -> str:
    if not _module_available("pdf_oxide"):
        raise RuntimeError("pdf_oxide not available")

    from pdf_oxide import PdfDocument  # type: ignore

    doc = PdfDocument(str(path))
    page_count = doc.page_count() if callable(getattr(doc, "page_count", None)) else doc.page_count
    parts: List[str] = []
    char_count = 0

    for index in range(int(page_count)):
        page_text = str(doc.extract_text(index) or "")
        parts.append(page_text)
        char_count += len(page_text)
        if char_count >= max_chars:
            break

    return "\n\n".join(parts)


def _extract_with_pymupdf(path: Path, max_chars: int) -> str:
    if not _module_available("fitz"):
        raise RuntimeError("PyMuPDF not available")

    import fitz  # type: ignore

    parts: List[str] = []
    char_count = 0
    with fitz.open(str(path)) as doc:
        for page in doc:
            page_text = page.get_text("text") or ""
            parts.append(page_text)
            char_count += len(page_text)
            if char_count >= max_chars:
                break
    return "\n\n".join(parts)


def _extract_with_pypdf(path: Path, max_chars: int) -> str:
    if not _module_available("pypdf"):
        raise RuntimeError("pypdf not available")

    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(str(path))
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as error:
            raise RuntimeError(f"encrypted PDF could not be decrypted: {error}")

    parts: List[str] = []
    char_count = 0
    for page in reader.pages:
        page_text = page.extract_text() or ""
        parts.append(page_text)
        char_count += len(page_text)
        if char_count >= max_chars:
            break
    return "\n\n".join(parts)


def _extract_with_pdftotext_cli(path: Path, max_chars: int) -> str:
    executable = shutil.which("pdftotext")
    if not executable:
        raise RuntimeError("pdftotext command not available")

    completed = subprocess.run(
        [executable, "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "pdftotext failed")
    return completed.stdout[: max_chars * 2]


def _extract_with_docling(path: Path, max_chars: int) -> str:
    if not _module_available("docling"):
        raise RuntimeError("Docling not available")

    from docling.document_converter import DocumentConverter  # type: ignore

    converter = DocumentConverter()
    result = converter.convert(str(path))
    document = result.document

    if hasattr(document, "export_to_markdown"):
        return str(document.export_to_markdown())[: max_chars * 2]
    if hasattr(document, "export_to_text"):
        return str(document.export_to_text())[: max_chars * 2]
    return str(document)[: max_chars * 2]


def _extract_with_windows_ocr(path: Path, max_chars: int) -> str:
    if sys.platform != "win32":
        raise RuntimeError("Windows OCR is available only on Windows")

    script_path = Path(__file__).resolve().with_name("windows_pdf_ocr.ps1")
    if not script_path.exists():
        raise RuntimeError("windows_pdf_ocr.ps1 is missing")

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise RuntimeError("PowerShell not found")

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-PdfPath",
            str(path),
            "-MaxPages",
            str(DEFAULT_MAX_OCR_PAGES),
            "-RenderWidth",
            str(DEFAULT_OCR_RENDER_WIDTH),
            "-MaxChars",
            str(max(max_chars, 20000)),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=DEFAULT_OCR_TIMEOUT_SECONDS,
    )

    if completed.returncode != 0:
        error_text = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(error_text or "Windows OCR failed")

    try:
        payload = json.loads(completed.stdout.lstrip("\ufeff").strip())
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Windows OCR returned invalid JSON: {error}")

    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "Windows OCR failed"))

    return str(payload.get("text") or "")


def _normalize_pdf_paths(file_paths: Sequence[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()

    for raw_path in file_paths or []:
        try:
            path = Path(str(raw_path)).expanduser()
            if path.suffix.lower() != ".pdf" or not path.exists():
                continue
            resolved = str(path.resolve())
        except (OSError, RuntimeError):
            continue

        key = resolved.lower()
        if key not in seen:
            seen.add(key)
            normalized.append(resolved)

    return normalized


def _finalize_text(value: str) -> str:
    text = _normalize_text(value)
    text = _strip_repetitive_noise_lines(text)
    text = _strip_inline_noise_lines(text)
    text = _trim_trailing_reference_like_sections(text)
    text = _normalize_text(text)
    return text


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", text)
    text = re.sub(r"([A-Za-z])-\n([A-Za-z])", r"\1\2", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _strip_repetitive_noise_lines(value: str) -> str:
    lines = value.splitlines()
    normalized_counts: Counter[str] = Counter()

    for line in lines:
        key = _noise_count_key(line)
        if key:
            normalized_counts[key] += 1

    cleaned_lines: List[str] = []
    for line in lines:
        key = _noise_count_key(line)
        if key and normalized_counts[key] >= 3:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _noise_count_key(line: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(line or "")).strip()
    if not cleaned:
        return ""
    if len(cleaned) > 140:
        return ""
    lowered = cleaned.lower()
    if _is_probable_page_number(cleaned):
        return "__page_number__"
    if any(token in lowered for token in ("doi", "copyright", "all rights reserved", "downloaded from")):
        return lowered
    if re.search(r"\b(vol\.?|volume|issue|journal|issn|www\.)\b", lowered):
        return lowered
    return ""


def _strip_inline_noise_lines(value: str) -> str:
    cleaned_lines: List[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not stripped:
            cleaned_lines.append("")
            continue
        if _is_probable_page_number(stripped):
            continue
        if lowered.startswith("doi:") or lowered.startswith("https://doi.org/") or lowered.startswith("http://doi.org/"):
            continue
        if "all rights reserved" in lowered:
            continue
        if "downloaded from" in lowered and len(stripped) < 180:
            continue
        if re.match(r"^copyright\b", lowered):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _trim_trailing_reference_like_sections(value: str) -> str:
    lines = value.splitlines()
    cutoff_index: Optional[int] = None
    min_index = _minimum_trailing_cut_index(lines)

    for index, line in enumerate(lines):
        if index < min_index:
            continue
        if _is_trailing_section_header(line):
            cutoff_index = index
            break

    if cutoff_index is None:
        return value
    return "\n".join(lines[:cutoff_index]).rstrip()


def _minimum_trailing_cut_index(lines: Sequence[str]) -> int:
    line_count = len(lines)
    if line_count <= 8:
        return max(3, line_count - 4)
    if line_count <= 20:
        return max(5, int(line_count * 0.5))
    return max(12, int(line_count * 0.35))


def _is_trailing_section_header(line: str) -> bool:
    normalized = re.sub(r"^[\s\d.IVXivx\-:()]+", "", str(line or "")).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized or len(normalized) > 80:
        return False

    lowered = normalized.lower().rstrip(":.")
    if lowered in TRAILING_SECTION_HEADERS:
        return True

    for header in TRAILING_SECTION_HEADERS:
        if lowered == f"{header}s":
            return True
        if lowered.startswith(f"{header} "):
            return True
    return False


def _is_probable_page_number(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 24:
        return False
    if re.fullmatch(r"(page\s+)?\d+(\s*(/|of)\s*\d+)?", text, flags=re.IGNORECASE):
        return True
    if len(text) >= 2 and re.fullmatch(r"[ivxlcdm]+", text, flags=re.IGNORECASE):
        return True
    return False


def _limit_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n\n[Text truncated locally due to length.]"


def _has_meaningful_text(value: str) -> bool:
    cleaned = _normalize_text(value)
    if len(cleaned) < 80:
        return False

    visible = sum(1 for char in cleaned if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    if visible < 40:
        return False

    replacement_count = cleaned.count("\ufffd")
    if replacement_count > max(5, int(len(cleaned) * 0.03)):
        return False

    return True


def _module_available(name: str) -> bool:
    if name not in _MODULE_CACHE:
        _MODULE_CACHE[name] = importlib.util.find_spec(name) is not None
    return _MODULE_CACHE[name]


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = list(argv if argv is not None else sys.argv[1:])
    result = build_input_json_list(args)
    json.dump(result.input_json_list, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")

    if result.ignored_paths:
        sys.stderr.write(
            f"Only the first {DEFAULT_MAX_PDF_FILES} PDFs were processed; the rest were ignored.\n"
        )
    for error in result.errors:
        sys.stderr.write(f"{error}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
