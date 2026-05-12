"""Read selected PDF files and emit the input_json_list consumed by the LLM layer.

The public contract of this module is intentionally small:

    input_json_list = [
        {"file_name": "paper.pdf", "pdf_content": "..."},
    ]

UI code should pass PDF paths into ``build_input_json_list`` and then hand the
returned list to the summarizer.  This file contains no API or UI logic.
"""

from __future__ import annotations

import json
import re
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_MAX_PDF_FILES = 5
DEFAULT_MAX_CHARS_PER_PDF = 60000


class PdfExtractionError(RuntimeError):
    """Raised when a file cannot be parsed as a text-bearing PDF."""


@dataclass
class ExtractionResult:
    input_json_list: List[Dict[str, str]]
    accepted_paths: List[str]
    ignored_paths: List[str]
    errors: List[str]

    @property
    def truncated(self) -> bool:
        return bool(self.ignored_paths)


def build_input_json_list(
    file_paths: Sequence[str],
    max_files: int = DEFAULT_MAX_PDF_FILES,
    max_chars_per_pdf: int = DEFAULT_MAX_CHARS_PER_PDF,
) -> ExtractionResult:
    """Extract text from the first ``max_files`` PDFs in ``file_paths``.

    Non-PDF paths are ignored.  The returned ``input_json_list`` is safe to pass
    directly into the prompt builder.
    """

    pdf_paths = _normalize_pdf_paths(file_paths)
    accepted_paths = pdf_paths[:max_files]
    ignored_paths = pdf_paths[max_files:]
    input_json_list: List[Dict[str, str]] = []
    errors: List[str] = []

    for path in accepted_paths:
        file_name = Path(path).name
        try:
            text = extract_pdf_text(path)
        except Exception as error:  # Keep batch processing alive for other PDFs.
            message = f"{file_name}: {error}"
            errors.append(message)
            text = f"未能从此 PDF 提取到可读文本。原因：{error}"

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


def extract_pdf_text(file_path: str) -> str:
    """Return plain text for a PDF file.

    If the optional ``pypdf`` package is present, it is used first.  A pure
    Python fallback handles common text PDFs without requiring installation.
    """

    path = Path(file_path)
    if not path.exists():
        raise PdfExtractionError("文件不存在。")
    if path.suffix.lower() != ".pdf":
        raise PdfExtractionError("不是 PDF 文件。")

    pypdf_text = _extract_with_pypdf(path)
    if _has_meaningful_text(pypdf_text):
        return pypdf_text

    fallback_text = _extract_with_fallback_parser(path)
    if _has_meaningful_text(fallback_text):
        return fallback_text

    raise PdfExtractionError("没有提取到可读文本，可能是扫描版、加密文件或特殊编码 PDF。")


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


def _extract_with_pypdf(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return ""

    try:
        reader = PdfReader(str(path))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                return ""

        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return "\n\n".join(pages)
    except Exception:
        return ""


def _extract_with_fallback_parser(path: Path) -> str:
    data = path.read_bytes()
    objects = _parse_pdf_objects(data)
    decoded_streams = {
        obj_id: _decode_stream(raw_object)
        for obj_id, raw_object in objects.items()
        if b"stream" in raw_object and b"endstream" in raw_object
    }

    cmaps_by_object = {
        obj_id: _parse_tounicode_cmap(stream)
        for obj_id, stream in decoded_streams.items()
        if b"beginbf" in stream or b"begincmap" in stream
    }
    cmaps_by_object = {key: value for key, value in cmaps_by_object.items() if value}
    font_to_cmaps = _build_font_cmap_index(objects, cmaps_by_object)
    all_cmaps = list(cmaps_by_object.values())

    chunks: List[str] = []
    for obj_id in sorted(decoded_streams):
        stream = decoded_streams[obj_id]
        if _is_probable_text_content_stream(stream):
            chunks.append(_extract_text_from_content_stream(stream, font_to_cmaps, all_cmaps))

    return "\n".join(chunk for chunk in chunks if chunk.strip())


def _parse_pdf_objects(data: bytes) -> Dict[int, bytes]:
    objects: Dict[int, bytes] = {}
    object_re = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj\b", re.DOTALL)

    for match in object_re.finditer(data):
        obj_id = int(match.group(1))
        objects[obj_id] = match.group(3)

    return objects


def _decode_stream(raw_object: bytes) -> bytes:
    stream_match = re.search(rb"stream\r?\n?(.*?)\r?\n?endstream", raw_object, re.DOTALL)
    if not stream_match:
        return b""

    stream = stream_match.group(1)
    header = raw_object[: stream_match.start()]

    if b"/FlateDecode" in header or b"/Fl" in header:
        for candidate in (stream, stream.strip(), stream.rstrip(b"\r\n")):
            try:
                return zlib.decompress(candidate)
            except zlib.error:
                continue

    if b"/ASCIIHexDecode" in header:
        return _decode_ascii_hex(stream)

    return stream


def _decode_ascii_hex(value: bytes) -> bytes:
    hex_text = re.sub(rb"[^0-9A-Fa-f]", b"", value.split(b">", 1)[0])
    if len(hex_text) % 2:
        hex_text += b"0"
    try:
        return bytes.fromhex(hex_text.decode("ascii"))
    except ValueError:
        return b""


def _parse_tounicode_cmap(stream: bytes) -> Dict[str, str]:
    text = stream.decode("latin-1", errors="ignore")
    mapping: Dict[str, str] = {}

    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, flags=re.DOTALL):
        for source, target in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            decoded = _decode_utf16_hex(target)
            if decoded:
                mapping[source.upper()] = decoded

    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, flags=re.DOTALL):
        array_ranges = re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]",
            block,
            flags=re.DOTALL,
        )
        for start_hex, end_hex, array_body in array_ranges:
            values = re.findall(r"<([0-9A-Fa-f]+)>", array_body)
            _add_bfrange_array(mapping, start_hex, end_hex, values)

        simple_ranges = re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
            block,
        )
        for start_hex, end_hex, target_hex in simple_ranges:
            _add_bfrange_simple(mapping, start_hex, end_hex, target_hex)

    return mapping


def _add_bfrange_array(
    mapping: Dict[str, str],
    start_hex: str,
    end_hex: str,
    values: Sequence[str],
) -> None:
    start = int(start_hex, 16)
    end = int(end_hex, 16)
    width = len(start_hex)

    for offset, source in enumerate(range(start, end + 1)):
        if offset >= len(values):
            break
        decoded = _decode_utf16_hex(values[offset])
        if decoded:
            mapping[f"{source:0{width}X}"] = decoded


def _add_bfrange_simple(
    mapping: Dict[str, str],
    start_hex: str,
    end_hex: str,
    target_hex: str,
) -> None:
    start = int(start_hex, 16)
    end = int(end_hex, 16)
    width = len(start_hex)
    target = _decode_utf16_hex(target_hex)

    if len(target) == 1:
        base = ord(target)
        for offset, source in enumerate(range(start, end + 1)):
            mapping[f"{source:0{width}X}"] = chr(base + offset)
        return

    if target:
        for source in range(start, end + 1):
            mapping[f"{source:0{width}X}"] = target


def _decode_utf16_hex(value: str) -> str:
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        return ""

    if not raw:
        return ""

    if len(raw) % 2:
        try:
            return raw.decode("utf-8", errors="ignore")
        except UnicodeDecodeError:
            return ""

    return raw.decode("utf-16-be", errors="ignore")


def _build_font_cmap_index(
    objects: Dict[int, bytes],
    cmaps_by_object: Dict[int, Dict[str, str]],
) -> Dict[str, List[Dict[str, str]]]:
    font_object_to_cmap: Dict[int, Dict[str, str]] = {}

    for obj_id, raw_object in objects.items():
        match = re.search(rb"/ToUnicode\s+(\d+)\s+(\d+)\s+R", raw_object)
        if not match:
            continue
        cmap = cmaps_by_object.get(int(match.group(1)))
        if cmap:
            font_object_to_cmap[obj_id] = cmap

    font_to_cmaps: Dict[str, List[Dict[str, str]]] = {}
    for raw_object in objects.values():
        for font_block in re.findall(rb"/Font\s*<<(.*?)>>", raw_object, flags=re.DOTALL):
            for font_name, font_obj_id in re.findall(
                rb"/([A-Za-z0-9_.+\-]+)\s+(\d+)\s+\d+\s+R",
                font_block,
            ):
                cmap = font_object_to_cmap.get(int(font_obj_id))
                if cmap:
                    font_to_cmaps.setdefault(font_name.decode("latin-1"), []).append(cmap)

    return font_to_cmaps


def _is_probable_text_content_stream(stream: bytes) -> bool:
    if b"beginbf" in stream or b"begincmap" in stream:
        return False
    return bool(re.search(rb"\bBT\b.*?\bET\b", stream, flags=re.DOTALL))


PDF_OPERATORS = {
    "BT",
    "ET",
    "Tf",
    "Tj",
    "TJ",
    "'",
    '"',
    "Td",
    "TD",
    "T*",
    "Tm",
}


def _extract_text_from_content_stream(
    stream: bytes,
    font_to_cmaps: Dict[str, List[Dict[str, str]]],
    all_cmaps: Sequence[Dict[str, str]],
) -> str:
    output: List[str] = []
    operands: List[Any] = []
    array_stack: List[List[Any]] = []
    current_font = ""

    for token_type, token_value in _tokenize_content_stream(stream):
        if token_type == "ARRAY_START":
            new_array: List[Any] = []
            if array_stack:
                array_stack[-1].append(new_array)
            else:
                operands.append(new_array)
            array_stack.append(new_array)
            continue

        if token_type == "ARRAY_END":
            if array_stack:
                array_stack.pop()
            continue

        if token_type != "OPERATOR":
            item = (token_type, token_value)
            if array_stack:
                array_stack[-1].append(item)
            else:
                operands.append(item)
            continue

        operator = str(token_value)
        if operator == "Tf":
            font_name = _find_last_name_operand(operands)
            if font_name:
                current_font = font_name
        elif operator in {"Td", "TD", "T*", "'", '"'}:
            _append_line_break(output)

        if operator == "Tj":
            item = _find_last_text_operand(operands)
            _append_text_item(output, item, current_font, font_to_cmaps, all_cmaps)
        elif operator in {"'", '"'}:
            item = _find_last_text_operand(operands)
            _append_text_item(output, item, current_font, font_to_cmaps, all_cmaps)
        elif operator == "TJ":
            array = _find_last_array_operand(operands)
            if array is not None:
                for item in _flatten_text_items(array):
                    _append_text_item(output, item, current_font, font_to_cmaps, all_cmaps)

        operands = []

    return _clean_extracted_text("".join(output))


def _tokenize_content_stream(stream: bytes) -> Iterable[Tuple[str, Any]]:
    length = len(stream)
    index = 0

    while index < length:
        byte = stream[index]

        if byte in b"\x00\t\n\f\r ":
            index += 1
            continue

        if byte == ord("%"):
            while index < length and stream[index] not in b"\r\n":
                index += 1
            continue

        if byte == ord("["):
            index += 1
            yield ("ARRAY_START", None)
            continue

        if byte == ord("]"):
            index += 1
            yield ("ARRAY_END", None)
            continue

        if byte == ord("("):
            value, index = _read_literal_string(stream, index)
            yield ("STRING", value)
            continue

        if byte == ord("<"):
            if index + 1 < length and stream[index + 1] == ord("<"):
                index += 2
                continue
            value, index = _read_hex_string(stream, index)
            yield ("HEX", value)
            continue

        if byte == ord(">"):
            index += 1
            continue

        word, index = _read_word(stream, index)
        if not word:
            continue
        if word in PDF_OPERATORS:
            yield ("OPERATOR", word)
        else:
            yield ("ATOM", word)


def _read_literal_string(stream: bytes, start: int) -> Tuple[bytes, int]:
    index = start + 1
    depth = 1
    output = bytearray()

    while index < len(stream):
        byte = stream[index]

        if byte == ord("\\"):
            if index + 1 >= len(stream):
                break
            next_byte = stream[index + 1]
            escaped = {
                ord("n"): b"\n",
                ord("r"): b"\r",
                ord("t"): b"\t",
                ord("b"): b"\b",
                ord("f"): b"\f",
                ord("("): b"(",
                ord(")"): b")",
                ord("\\"): b"\\",
            }.get(next_byte)
            if escaped is not None:
                output.extend(escaped)
                index += 2
                continue
            if 48 <= next_byte <= 55:
                octal = bytes([next_byte])
                cursor = index + 2
                while cursor < min(index + 4, len(stream)) and 48 <= stream[cursor] <= 55:
                    octal += bytes([stream[cursor]])
                    cursor += 1
                output.append(int(octal, 8))
                index = cursor
                continue
            if next_byte in b"\r\n":
                index += 2
                if next_byte == ord("\r") and index < len(stream) and stream[index] == ord("\n"):
                    index += 1
                continue
            output.append(next_byte)
            index += 2
            continue

        if byte == ord("("):
            depth += 1
            output.append(byte)
            index += 1
            continue

        if byte == ord(")"):
            depth -= 1
            if depth == 0:
                return bytes(output), index + 1
            output.append(byte)
            index += 1
            continue

        output.append(byte)
        index += 1

    return bytes(output), index


def _read_hex_string(stream: bytes, start: int) -> Tuple[str, int]:
    index = start + 1
    output = bytearray()
    while index < len(stream) and stream[index] != ord(">"):
        if stream[index] not in b"\x00\t\n\f\r ":
            output.append(stream[index])
        index += 1
    if len(output) % 2:
        output.append(ord("0"))
    return output.decode("ascii", errors="ignore"), min(index + 1, len(stream))


def _read_word(stream: bytes, start: int) -> Tuple[str, int]:
    index = start
    delimiters = b"\x00\t\n\f\r ()<>[]{}%/"

    if stream[index] == ord("/"):
        index += 1
        word_start = index
        while index < len(stream) and stream[index] not in delimiters:
            index += 1
        return "/" + stream[word_start:index].decode("latin-1", errors="ignore"), index

    word_start = index
    while index < len(stream) and stream[index] not in delimiters:
        index += 1
    return stream[word_start:index].decode("latin-1", errors="ignore"), index


def _find_last_name_operand(operands: Sequence[Any]) -> str:
    for item in reversed(operands):
        if isinstance(item, tuple) and item[0] == "ATOM" and str(item[1]).startswith("/"):
            return str(item[1])[1:]
    return ""


def _find_last_text_operand(operands: Sequence[Any]) -> Optional[Tuple[str, Any]]:
    for item in reversed(operands):
        if isinstance(item, tuple) and item[0] in {"STRING", "HEX"}:
            return item
    return None


def _find_last_array_operand(operands: Sequence[Any]) -> Optional[List[Any]]:
    for item in reversed(operands):
        if isinstance(item, list):
            return item
    return None


def _flatten_text_items(items: Sequence[Any]) -> Iterable[Tuple[str, Any]]:
    for item in items:
        if isinstance(item, list):
            yield from _flatten_text_items(item)
        elif isinstance(item, tuple) and item[0] in {"STRING", "HEX"}:
            yield item


def _append_line_break(output: List[str]) -> None:
    if output and not output[-1].endswith("\n"):
        output.append("\n")


def _append_text_item(
    output: List[str],
    item: Optional[Tuple[str, Any]],
    current_font: str,
    font_to_cmaps: Dict[str, List[Dict[str, str]]],
    all_cmaps: Sequence[Dict[str, str]],
) -> None:
    if item is None:
        return

    token_type, token_value = item
    preferred_cmaps = font_to_cmaps.get(current_font, [])
    if token_type == "HEX":
        text = _decode_hex_text(str(token_value), preferred_cmaps, all_cmaps)
    else:
        text = _decode_literal_text(bytes(token_value), preferred_cmaps, all_cmaps)

    if text:
        output.append(text)


def _decode_literal_text(
    raw: bytes,
    preferred_cmaps: Sequence[Dict[str, str]],
    all_cmaps: Sequence[Dict[str, str]],
) -> str:
    hex_text = raw.hex().upper()
    cmap_text = _decode_with_best_cmap(hex_text, preferred_cmaps, all_cmaps)
    plain_candidates = [
        _decode_raw_bytes(raw, "utf-16-be") if raw.startswith(b"\xfe\xff") else "",
        _decode_raw_bytes(raw, "utf-16-be") if b"\x00" in raw[: min(len(raw), 20)] else "",
        _decode_raw_bytes(raw, "utf-8"),
        _decode_raw_bytes(raw, "latin-1"),
    ]
    candidates = [cmap_text] + plain_candidates
    return max(candidates, key=_text_score).strip("\x00")


def _decode_hex_text(
    hex_text: str,
    preferred_cmaps: Sequence[Dict[str, str]],
    all_cmaps: Sequence[Dict[str, str]],
) -> str:
    normalized_hex = re.sub(r"[^0-9A-Fa-f]", "", hex_text).upper()
    if not normalized_hex:
        return ""

    cmap_text = _decode_with_best_cmap(normalized_hex, preferred_cmaps, all_cmaps)
    raw = bytes.fromhex(normalized_hex) if len(normalized_hex) % 2 == 0 else b""
    plain_candidates = [
        _decode_raw_bytes(raw[2:], "utf-16-be") if raw.startswith(b"\xfe\xff") else "",
        _decode_raw_bytes(raw, "utf-16-be"),
        _decode_raw_bytes(raw, "utf-8"),
        _decode_raw_bytes(raw, "latin-1"),
    ]
    candidates = [cmap_text] + plain_candidates
    return max(candidates, key=_text_score).strip("\x00")


def _decode_with_best_cmap(
    hex_text: str,
    preferred_cmaps: Sequence[Dict[str, str]],
    all_cmaps: Sequence[Dict[str, str]],
) -> str:
    candidates: List[str] = []
    seen = set()

    for cmap in list(preferred_cmaps) + list(all_cmaps):
        marker = id(cmap)
        if marker in seen:
            continue
        seen.add(marker)
        decoded = _decode_hex_with_cmap(hex_text, cmap)
        if decoded:
            candidates.append(decoded)

    return max(candidates, key=_text_score) if candidates else ""


def _decode_hex_with_cmap(hex_text: str, cmap: Dict[str, str]) -> str:
    if not cmap:
        return ""

    key_lengths = sorted({len(key) for key in cmap}, reverse=True)
    index = 0
    output: List[str] = []
    hits = 0

    while index < len(hex_text):
        matched = False
        for length in key_lengths:
            key = hex_text[index : index + length]
            if key in cmap:
                output.append(cmap[key])
                index += length
                hits += 1
                matched = True
                break
        if not matched:
            index += 2

    if hits == 0:
        return ""
    return "".join(output)


def _decode_raw_bytes(raw: bytes, encoding: str) -> str:
    if not raw:
        return ""
    try:
        return raw.decode(encoding, errors="ignore")
    except LookupError:
        return ""


def _text_score(value: str) -> int:
    if not value:
        return -1000

    score = 0
    for char in value:
        code = ord(char)
        if char in "\r\n\t":
            score += 1
        elif char.isprintable():
            score += 2
        if "\u4e00" <= char <= "\u9fff":
            score += 4
        if code < 32 and char not in "\r\n\t":
            score -= 12
        if char == "\ufffd":
            score -= 20
    return score


def _clean_extracted_text(value: str) -> str:
    value = value.replace("\x00", "")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\u00a0]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _limit_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n\n[文本过长，已按本地限制截断。]"


def _has_meaningful_text(value: str) -> bool:
    cleaned = _normalize_text(value or "")
    if len(cleaned) < 20:
        return False
    visible = sum(1 for char in cleaned if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    return visible >= 10


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    result = build_input_json_list(args)
    json.dump(result.input_json_list, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")

    if result.ignored_paths:
        sys.stderr.write(f"仅处理前 {DEFAULT_MAX_PDF_FILES} 份 PDF，其余已忽略。\n")
    for error in result.errors:
        sys.stderr.write(f"{error}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
