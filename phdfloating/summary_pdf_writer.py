"""Create the final Downloads/summary.pdf file."""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


A4_WIDTH = 595
A4_HEIGHT = 842
MARGIN_X = 54
MARGIN_TOP = 58
MARGIN_BOTTOM = 58
REPORTLAB_TITLE_UNITS = 72
REPORTLAB_BODY_UNITS = 86
FALLBACK_TITLE_UNITS = 34
FALLBACK_BODY_UNITS = 76

RichLine = List[Tuple[str, bool]]

BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def get_downloads_dir() -> Path:
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        downloads = Path(user_profile) / "Downloads"
        if downloads.exists():
            return downloads
    return Path.home() / "Downloads"


def write_summary_pdf(
    summaries: Sequence[Dict[str, Any]],
    output_path: Optional[Path] = None,
    summary_language: str = "zh-CN",
) -> Path:
    output = output_path or _next_available_summary_path(get_downloads_dir())
    output.parent.mkdir(parents=True, exist_ok=True)

    if _write_with_browser_html(summaries, output, summary_language):
        return output

    if _write_with_reportlab(summaries, output, summary_language):
        return output

    pages = _compose_pages(summaries, summary_language)
    _write_simple_cjk_pdf(pages, output)
    return output


def _next_available_summary_path(downloads_dir: Path) -> Path:
    downloads_dir.mkdir(parents=True, exist_ok=True)

    base_name = "summary"
    suffix = ".pdf"
    candidate = downloads_dir / f"{base_name}{suffix}"
    if not candidate.exists():
        return candidate

    index = 1
    while True:
        candidate = downloads_dir / f"{base_name}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _write_with_browser_html(
    summaries: Sequence[Dict[str, Any]],
    output: Path,
    summary_language: str,
) -> bool:
    browser = _find_browser_executable()
    if browser is None:
        return False

    try:
        with tempfile.TemporaryDirectory(prefix="phdf-summary-") as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            html_path = temp_dir / "summary.html"
            profile_dir = temp_dir / "browser-profile"
            html_path.write_text(_render_summary_html(summaries, summary_language), encoding="utf-8")

            command_variants = [
                [
                    str(browser),
                    "--headless=new",
                    f"--user-data-dir={profile_dir}",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--allow-file-access-from-files",
                    "--no-pdf-header-footer",
                    f"--print-to-pdf={output}",
                    html_path.as_uri(),
                ],
                [
                    str(browser),
                    "--headless",
                    f"--user-data-dir={profile_dir}",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--allow-file-access-from-files",
                    "--print-to-pdf-no-header",
                    f"--print-to-pdf={output}",
                    html_path.as_uri(),
                ],
            ]

            for command in command_variants:
                if output.exists():
                    output.unlink()
                result = subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                    check=False,
                )
                if output.exists() and output.stat().st_size > 0:
                    return True
                if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
                    return True
    except Exception:
        return False

    return output.exists() and output.stat().st_size > 0


def _find_browser_executable() -> Optional[Path]:
    env_candidates = [
        os.environ.get("PHDF_PDF_BROWSER"),
        os.environ.get("CHROME_PATH"),
        os.environ.get("EDGE_PATH"),
    ]
    for candidate in env_candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)

    for binary in ("chrome", "msedge", "chromium", "microsoft-edge"):
        resolved = shutil.which(binary)
        if resolved:
            return Path(resolved)

    for candidate in BROWSER_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path

    return None


def _render_summary_html(summaries: Sequence[Dict[str, Any]], summary_language: str) -> str:
    month_label = datetime.now().strftime("%B %Y")
    entries_html = "\n".join(
        _render_entry_html(index, item, summary_language)
        for index, item in enumerate(summaries, start=1)
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Summary</title>
  <style>
    @page {{
      size: A4;
      margin: 22mm 18mm 18mm 18mm;
    }}

    html, body {{
      margin: 0;
      padding: 0;
      background: #ffffff;
      color: #121212;
      font-family: "Georgia", "Times New Roman", "SimSun", serif;
    }}

    body {{
      font-size: 11.2pt;
      line-height: 1.65;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }}

    .page {{
      max-width: 168mm;
      margin: 0 auto;
      padding-top: 24mm;
    }}

    .cover {{
      text-align: center;
      margin-bottom: 24mm;
    }}

    .cover h1 {{
      margin: 0;
      font-size: 22pt;
      font-weight: 500;
      letter-spacing: 0;
    }}

    .cover .month {{
      margin-top: 14mm;
      font-size: 11pt;
      color: #222;
    }}

    .entry {{
      break-inside: avoid;
      page-break-inside: avoid;
      margin-bottom: 14mm;
    }}

    .entry-header {{
      display: grid;
      grid-template-columns: 14mm 1fr;
      column-gap: 4mm;
      align-items: start;
      margin-bottom: 3mm;
    }}

    .entry-index {{
      font-size: 14pt;
      font-weight: 700;
      line-height: 1.35;
    }}

    .entry-title {{
      margin: 0;
      font-size: 14pt;
      font-weight: 700;
      line-height: 1.35;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .entry-content {{
      margin: 0 0 0 18mm;
      font-size: 11.2pt;
      line-height: 1.7;
      text-align: justify;
      text-justify: inter-word;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .entry-content p {{
      margin: 0;
    }}

    .entry-keywords {{
      margin-top: 3mm;
    }}

    strong {{
      font-weight: 700;
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="cover">
      <h1>Summary</h1>
      <div class="month">{html.escape(month_label)}</div>
    </section>
    {entries_html}
  </main>
</body>
</html>
"""


def _render_entry_html(index: int, item: Dict[str, Any], summary_language: str) -> str:
    title = _summary_text_to_html(str(item.get("name") or f"PDF {index}"))
    summary = _summary_text_to_html(str(item.get("summary") or "").strip())
    summary_paragraphs = summary.split("\n")
    summary_html = "".join(f"<p>{paragraph or '&nbsp;'}</p>" for paragraph in summary_paragraphs)
    keywords_html = ""
    keywords_markdown = _build_keywords_markdown(item, summary_language)
    if keywords_markdown:
        keywords_html = f'<p class="entry-keywords">{_summary_text_to_html(keywords_markdown)}</p>'

    return f"""
    <article class="entry">
      <header class="entry-header">
        <div class="entry-index">{index}</div>
        <h2 class="entry-title">{title}</h2>
      </header>
      <section class="entry-content">
        {summary_html}
        {keywords_html}
      </section>
    </article>
    """


def _build_keywords_markdown(item: Dict[str, Any], summary_language: str) -> str:
    keywords = _extract_keywords(item)
    if not keywords:
        return ""
    label = "关键词：" if summary_language != "en" else "Keywords: "
    separator = "，" if summary_language != "en" else ", "
    return f"**{label}**{separator.join(keywords)}"


def _extract_keywords(item: Dict[str, Any]) -> List[str]:
    raw_keywords = item.get("keywords") or item.get("关键词") or []
    if isinstance(raw_keywords, list):
        values = raw_keywords
    else:
        values = re.split(r"[\n,，;；]+", str(raw_keywords or ""))

    keywords: List[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in keywords:
            keywords.append(cleaned)
    return keywords[:5]


def _summary_text_to_html(value: str) -> str:
    escaped = html.escape(value or "")
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return escaped.replace("\r\n", "\n").replace("\r", "\n")


def _write_with_reportlab(
    summaries: Sequence[Dict[str, Any]],
    output: Path,
    summary_language: str,
) -> bool:
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
        _draw_reportlab_document(doc, summaries, summary_language)
        doc.save()
        return True
    except Exception:
        return False


def _draw_reportlab_document(
    doc: object,
    summaries: Sequence[Dict[str, Any]],
    summary_language: str,
) -> None:
    height = A4_HEIGHT
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
        keywords_markdown = _build_keywords_markdown(item, summary_language)

        for title_line in _wrap_title_text(f"{index}. {title}", REPORTLAB_TITLE_UNITS):
            ensure_space(22)
            doc.setFont("STSong-Light", 13)
            doc.drawString(MARGIN_X, y, title_line)
            y -= 22

        for body_line in _wrap_rich_text(summary, REPORTLAB_BODY_UNITS):
            ensure_space(16)
            _draw_reportlab_rich_line(doc, body_line, MARGIN_X, y, 10)
            y -= 16

        if keywords_markdown:
            for keyword_line in _wrap_rich_text(keywords_markdown, REPORTLAB_BODY_UNITS):
                ensure_space(16)
                _draw_reportlab_rich_line(doc, keyword_line, MARGIN_X, y, 10)
                y -= 16

        y -= 14


def _compose_pages(
    summaries: Sequence[Dict[str, Any]],
    summary_language: str,
) -> List[List[Tuple[RichLine, int, int]]]:
    pages: List[List[Tuple[RichLine, int, int]]] = [[]]
    y = A4_HEIGHT - MARGIN_TOP

    def add_line(text: str, font_size: int = 10, leading: int = 16, bold: bool = False) -> None:
        add_rich_line([(text, bold)], font_size, leading)

    def add_rich_line(line: RichLine, font_size: int = 10, leading: int = 16) -> None:
        nonlocal y
        if y - leading < MARGIN_BOTTOM and pages[-1]:
            pages.append([])
            y = A4_HEIGHT - MARGIN_TOP
        pages[-1].append((line, font_size, leading))
        y -= leading

    add_line("PDF 批量总结", 18, 28)
    add_line(datetime.now().strftime("%Y-%m-%d %H:%M"), 9, 24)
    add_line("", 10, 14)

    for index, item in enumerate(summaries, start=1):
        title = _clean_markdown(str(item.get("name") or f"PDF {index}"))
        summary = str(item.get("summary") or "").strip()
        keywords_markdown = _build_keywords_markdown(item, summary_language)

        for title_line in _wrap_title_text(f"{index}. {title}", FALLBACK_TITLE_UNITS):
            add_line(title_line, 13, 22)

        for body_line in _wrap_rich_text(summary, FALLBACK_BODY_UNITS):
            add_rich_line(body_line, 10, 16)

        if keywords_markdown:
            for keyword_line in _wrap_rich_text(keywords_markdown, FALLBACK_BODY_UNITS):
                add_rich_line(keyword_line, 10, 16)

        add_line("", 10, 14)

    return pages


def _write_simple_cjk_pdf(pages: Sequence[Sequence[Tuple[RichLine, int, int]]], output: Path) -> None:
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


def _build_page_stream(lines: Sequence[Tuple[RichLine, int, int]]) -> bytes:
    parts: List[bytes] = [b"BT\n", f"{MARGIN_X} {A4_HEIGHT - MARGIN_TOP} Td\n".encode("ascii")]
    first = True

    for rich_line, font_size, leading in lines:
        if not first:
            parts.append(f"0 -{leading} Td\n".encode("ascii"))
        first = False
        parts.append(f"/F1 {font_size} Tf\n".encode("ascii"))

        if not rich_line:
            parts.append(b"<" + _encode_text_hex(" ") + b"> Tj\n")
            continue

        for text, bold in rich_line:
            safe_text = text if text else " "
            if bold:
                parts.append(b"0.25 w 2 Tr\n")
                parts.append(b"<" + _encode_text_hex(safe_text) + b"> Tj\n")
                parts.append(b"0 Tr\n")
            else:
                parts.append(b"<" + _encode_text_hex(safe_text) + b"> Tj\n")

    parts.append(b"ET\n")
    return b"".join(parts)


def _encode_text_hex(value: str) -> bytes:
    return value.encode("utf-16-be", errors="replace").hex().upper().encode("ascii")


def _wrap_rich_text(value: str, max_units: int) -> List[RichLine]:
    segments = _parse_markdown_bold(value)
    if not segments:
        return [[("无总结内容。", False)]]

    lines: List[RichLine] = []
    current: RichLine = []
    current_width = 0

    for text, bold in segments:
        for token in _split_for_wrapping(text):
            token_width = _display_width(token)
            if current and current_width + token_width > max_units:
                lines.append(_trim_rich_line(current))
                current = []
                current_width = 0
                token = token.lstrip()
                token_width = _display_width(token)
            if token:
                _append_rich_segment(current, token, bold)
                current_width += token_width

    if current:
        lines.append(_trim_rich_line(current))

    return [line for line in lines if _rich_line_text(line).strip()] or [[("无总结内容。", False)]]


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
            if char in ",.;:!?)]}/-":
                yield buffer
                buffer = ""
    if buffer:
        yield buffer


def _wrap_title_text(value: str, max_units: int) -> List[str]:
    text = _clean_markdown(re.sub(r"\s+", " ", value or "").strip())
    if not text:
        return ["Untitled"]

    lines: List[str] = []
    current = ""
    current_width = 0

    for token in _split_title_for_wrapping(text):
        token_width = _title_display_width(token)
        if current and current_width + token_width > max_units:
            lines.append(current.rstrip())
            current = token.lstrip()
            current_width = _title_display_width(current)
        else:
            current += token
            current_width += token_width

        while current and current_width > max_units:
            head, tail = _split_oversized_title_token(current, max_units)
            if head:
                lines.append(head.rstrip())
            current = tail.lstrip()
            current_width = _title_display_width(current)

    if current.strip():
        lines.append(current.rstrip())
    return lines or ["Untitled"]


def _split_title_for_wrapping(text: str) -> Iterable[str]:
    buffer = ""
    break_chars = set(" -/:;,.()[]{}")
    for char in text:
        buffer += char
        if char in break_chars or "\u4e00" <= char <= "\u9fff":
            yield buffer
            buffer = ""
    if buffer:
        yield buffer


def _display_width(value: str) -> int:
    width = 0
    for char in value:
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def _title_display_width(value: str) -> int:
    width = 0
    for char in value:
        if char.isspace():
            width += 1
        elif char.isascii():
            width += 2
        elif unicodedata.east_asian_width(char) in {"W", "F"}:
            width += 2
        else:
            width += 2
    return width


def _split_oversized_title_token(value: str, max_units: int) -> Tuple[str, str]:
    width = 0
    split_at = 0
    for index, char in enumerate(value):
        char_width = _title_display_width(char)
        if index > 0 and width + char_width > max_units:
            break
        width += char_width
        split_at = index + 1
    if split_at <= 0:
        split_at = 1
    return value[:split_at], value[split_at:]


def _clean_markdown(value: str) -> str:
    return re.sub(r"\*\*(.*?)\*\*", r"\1", value or "").strip()


def _parse_markdown_bold(value: str) -> RichLine:
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        return []

    segments: RichLine = []
    cursor = 0
    for match in re.finditer(r"\*\*(.+?)\*\*", text):
        if match.start() > cursor:
            segments.append((text[cursor : match.start()], False))
        if match.group(1):
            segments.append((match.group(1), True))
        cursor = match.end()
    if cursor < len(text):
        segments.append((text[cursor:], False))
    return segments


def _append_rich_segment(line: RichLine, text: str, bold: bool) -> None:
    if not text:
        return
    if line and line[-1][1] == bold:
        line[-1] = (line[-1][0] + text, bold)
    else:
        line.append((text, bold))


def _trim_rich_line(line: RichLine) -> RichLine:
    trimmed = [(text, bold) for text, bold in line]
    while trimmed and not trimmed[0][0].strip():
        trimmed.pop(0)
    while trimmed and not trimmed[-1][0].strip():
        trimmed.pop()
    if trimmed:
        first_text, first_bold = trimmed[0]
        last_text, last_bold = trimmed[-1]
        trimmed[0] = (first_text.lstrip(), first_bold)
        trimmed[-1] = (last_text.rstrip(), last_bold)
    return trimmed


def _rich_line_text(line: RichLine) -> str:
    return "".join(text for text, _bold in line)


def _draw_reportlab_rich_line(doc: object, line: RichLine, x: float, y: float, font_size: int) -> None:
    cursor_x = x
    for text, bold in line:
        if not text:
            continue
        doc.setFont("STSong-Light", font_size)
        doc.drawString(cursor_x, y, text)
        if bold:
            doc.drawString(cursor_x + 0.35, y, text)
        try:
            cursor_x += doc.stringWidth(text, "STSong-Light", font_size)
        except Exception:
            cursor_x += _pdf_text_width_units(text, font_size)


def _pdf_text_width_units(value: str, font_size: int) -> float:
    return _display_width(value) * font_size * 0.5
