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
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence


DEFAULT_MAX_PDF_FILES = 5
DEFAULT_MAX_CHARS_PER_PDF = 60000
DEFAULT_MAX_OCR_PAGES = 120
DEFAULT_OCR_RENDER_WIDTH = 1400
DEFAULT_OCR_TIMEOUT_SECONDS = 360
CPU_UTILIZATION_LIMIT = 0.8


class PdfExtractionError(RuntimeError):
    """Raised when a PDF cannot be converted to useful local text."""


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


def build_input_json_list(
    file_paths: Sequence[str],
    max_files: int = DEFAULT_MAX_PDF_FILES,
    max_chars_per_pdf: int = DEFAULT_MAX_CHARS_PER_PDF,
) -> ExtractionResult:
    """Extract text from up to ``max_files`` PDFs and return LLM-ready JSON."""

    pdf_paths = _normalize_pdf_paths(file_paths)
    accepted_paths = pdf_paths[:max_files]
    ignored_paths = pdf_paths[max_files:]

    if not accepted_paths:
        return ExtractionResult(
            input_json_list=[],
            accepted_paths=[],
            ignored_paths=ignored_paths,
            errors=[],
        )

    workers = _compute_file_worker_limit(len(accepted_paths))
    ordered_results = _extract_files_parallel(accepted_paths, max_chars_per_pdf, workers)

    input_json_list: List[Dict[str, str]] = []
    errors: List[str] = []

    for result in ordered_results:
        file_name = Path(result["path"]).name
        if result["error"]:
            errors.append(f"{file_name}: {result['error']}")
            text = f"Unable to extract readable text from this PDF. Reason: {result['error']}"
        else:
            text = result["text"]

        input_json_list.append(
            {
                "file_name": file_name,
                "pdf_content": _limit_text(_normalize_text(text), max_chars_per_pdf),
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


def _extract_files_parallel(
    accepted_paths: Sequence[str],
    max_chars_per_pdf: int,
    workers: int,
) -> List[Dict[str, str]]:
    if len(accepted_paths) == 1 or workers <= 1:
        return [
            _extract_single_payload(index, path, max_chars_per_pdf)
            for index, path in enumerate(accepted_paths)
        ]

    ordered: List[Optional[Dict[str, str]]] = [None] * len(accepted_paths)
    executor_kinds = [
        concurrent.futures.ProcessPoolExecutor,
        concurrent.futures.ThreadPoolExecutor,
    ]

    for executor_kind in executor_kinds:
        try:
            with executor_kind(max_workers=workers) as executor:
                futures = [
                    executor.submit(_extract_single_payload, index, path, max_chars_per_pdf)
                    for index, path in enumerate(accepted_paths)
                ]
                for future in concurrent.futures.as_completed(futures):
                    payload = future.result()
                    ordered[payload["index"]] = payload
            break
        except (PermissionError, OSError):
            ordered = [None] * len(accepted_paths)
            continue

    return [item for item in ordered if item is not None]


def _extract_single_payload(index: int, path: str, max_chars: int) -> Dict[str, str]:
    try:
        text = extract_pdf_text(path, max_chars=max_chars)
        return {"index": index, "path": path, "text": text, "error": ""}
    except Exception as error:
        return {"index": index, "path": path, "text": "", "error": str(error)}


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
    return _normalize_text(value)


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
