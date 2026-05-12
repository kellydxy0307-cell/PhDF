"""Create the final Downloads/summary.pdf file."""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


A4_WIDTH = 595
A4_HEIGHT = 842
MARGIN_X = 54
MARGIN_TOP = 58
MARGIN_BOTTOM = 58


def get_downloads_dir() -> Path:
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        downloads = Path(user_profile) / "Downloads"
        if downloads.exists():
            return downloads
    return Path.home() / "Downloads"


def write_summary_pdf(
    summaries: Sequence[Dict[str, str]],
    output_path: Optional[Path] = None,
) -> Path:
    output = output_path or (get_downloads_dir() / "summary.pdf")
    output.parent.mkdir(parents=True, exist_ok=True)

    if _write_with_reportlab(summaries, output):
        return output

    pages = _compose_pages(summaries)
    _write_simple_cjk_pdf(pages, output)
    return output


def _write_with_reportlab(summaries: Sequence[Dict[str, str]], output: Path) -> bool:
    try:
        from reportlab.lib.pagesizes import A4  # type: ignore
        from reportlab.pdfbase import pdfmetrics  # type: ignore
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont  # type: ignore
        from reportlab.pdfgen import canvas  # type: ignore
    except Exception:
        return False

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        doc = canvas.Canvas(str(output), pagesize=A4)
        width, height = A4
        y = height - MARGIN_TOP

        def ensure_space(line_height: int) -> None:
            nonlocal y
            if y - line_height < MARGIN_BOTTOM:
                doc.showPage()
                y = height - MARGIN_TOP

        doc.setTitle("summary")
        doc.setFont("STSong-Light", 18)
        doc.drawString(MARGIN_X, y, "PDF 批量总结")
        y -= 28
        doc.setFont("STSong-Light", 9)
        doc.drawString(MARGIN_X, y, datetime.now().strftime("%Y-%m-%d %H:%M"))
        y -= 30

        for index, item in enumerate(summaries, start=1):
            title = _clean_markdown(str(item.get("name") or f"PDF {index}"))
            summary = str(item.get("summary") or "").strip()

            ensure_space(30)
            doc.setFont("STSong-Light", 13)
            doc.drawString(MARGIN_X, y, f"{index}. {title}")
            y -= 22

            doc.setFont("STSong-Light", 10)
            for line in _wrap_text(summary, 86):
                ensure_space(16)
                doc.drawString(MARGIN_X, y, line)
                y -= 16
            y -= 14

        doc.save()
        return True
    except Exception:
        return False


def _compose_pages(summaries: Sequence[Dict[str, str]]) -> List[List[Tuple[str, int, int]]]:
    pages: List[List[Tuple[str, int, int]]] = [[]]
    y = A4_HEIGHT - MARGIN_TOP

    def add_line(text: str, font_size: int = 10, leading: int = 16) -> None:
        nonlocal y
        if y - leading < MARGIN_BOTTOM and pages[-1]:
            pages.append([])
            y = A4_HEIGHT - MARGIN_TOP
        pages[-1].append((text, font_size, leading))
        y -= leading

    add_line("PDF 批量总结", 18, 28)
    add_line(datetime.now().strftime("%Y-%m-%d %H:%M"), 9, 24)
    add_line("", 10, 14)

    for index, item in enumerate(summaries, start=1):
        title = _clean_markdown(str(item.get("name") or f"PDF {index}"))
        summary = str(item.get("summary") or "").strip()

        add_line(f"{index}. {title}", 13, 22)
        for line in _wrap_text(summary, 76):
            add_line(line, 10, 16)
        add_line("", 10, 14)

    return pages


def _write_simple_cjk_pdf(pages: Sequence[Sequence[Tuple[str, int, int]]], output: Path) -> None:
    objects: List[bytes] = []
    page_object_ids: List[int] = []
    content_object_ids: List[int] = []

    catalog_id = 1
    pages_id = 2
    font_id = 3
    next_id = 4

    for _page in pages:
        page_object_ids.append(next_id)
        next_id += 1
    for _page in pages:
        content_object_ids.append(next_id)
        next_id += 1

    kids = " ".join(f"{obj_id} 0 R" for obj_id in page_object_ids)
    objects.append(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii"))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_object_ids)} >>".encode("ascii"))
    objects.append(
        b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
        b"/Encoding /UniGB-UCS2-H /DescendantFonts ["
        b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> "
        b"/DW 1000 >>] >>"
    )

    for page_id, content_id in zip(page_object_ids, content_object_ids):
        page_object = (
            f"<< /Type /Page /Parent {pages_id} 0 R "
            f"/MediaBox [0 0 {A4_WIDTH} {A4_HEIGHT}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        )
        objects.append(page_object.encode("ascii"))

    for page in pages:
        stream = _build_page_stream(page)
        objects.append(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))

    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    output.write_bytes(bytes(pdf))


def _build_page_stream(lines: Sequence[Tuple[str, int, int]]) -> bytes:
    parts: List[bytes] = [b"BT\n", f"{MARGIN_X} {A4_HEIGHT - MARGIN_TOP} Td\n".encode("ascii")]
    first = True

    for text, font_size, leading in lines:
        if not first:
            parts.append(f"0 -{leading} Td\n".encode("ascii"))
        first = False
        safe_text = text if text else " "
        parts.append(f"/F1 {font_size} Tf\n".encode("ascii"))
        parts.append(b"<" + _encode_text_hex(safe_text) + b"> Tj\n")

    parts.append(b"ET\n")
    return b"".join(parts)


def _encode_text_hex(value: str) -> bytes:
    return value.encode("utf-16-be", errors="replace").hex().upper().encode("ascii")


def _wrap_text(value: str, max_units: int) -> List[str]:
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        return ["无总结内容。"]

    lines: List[str] = []
    current = ""
    current_width = 0

    for token in _split_for_wrapping(text):
        token_width = _display_width(token)
        if current and current_width + token_width > max_units:
            lines.append(current.rstrip())
            current = token.lstrip()
            current_width = _display_width(current)
        else:
            current += token
            current_width += token_width

    if current.strip():
        lines.append(current.rstrip())
    return lines or ["无总结内容。"]


def _split_for_wrapping(text: str) -> Iterable[str]:
    buffer = ""
    for char in text:
        if char.isspace():
            if buffer:
                yield buffer
                buffer = ""
            yield " "
        elif _display_width(char) >= 2:
            if buffer:
                yield buffer
                buffer = ""
            yield char
        else:
            buffer += char
            if char in ",.;:!?)]}":
                yield buffer
                buffer = ""
    if buffer:
        yield buffer


def _display_width(value: str) -> int:
    width = 0
    for char in value:
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def _clean_markdown(value: str) -> str:
    return re.sub(r"\*\*(.*?)\*\*", r"\1", value or "").strip()
