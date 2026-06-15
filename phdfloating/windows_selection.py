"""Read the PDF files currently selected in Windows File Explorer."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Sequence


def get_selected_pdf_paths() -> List[str]:
    """Return selected PDF paths from the foreground Explorer window.

    The floating window is configured not to steal focus, so the foreground
    window normally remains File Explorer when the user clicks the ball.  If the
    foreground match is unavailable, we fall back to selected PDFs from all open
    Explorer windows.
    """

    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
        import win32gui  # type: ignore
    except Exception:
        return []

    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        foreground_hwnd = 0
        try:
            foreground_hwnd = int(win32gui.GetForegroundWindow())
        except Exception:
            foreground_hwnd = 0

        explorer_windows = _iter_explorer_windows(shell)
        foreground_matches: List[str] = []
        fallback_matches: List[str] = []

        for window in explorer_windows:
            selected = _selected_paths_from_window(window)
            if not selected:
                continue

            pdfs = _filter_pdf_paths(selected)
            if not pdfs:
                continue

            fallback_matches.extend(pdfs)
            try:
                if int(window.HWND) == foreground_hwnd:
                    foreground_matches.extend(pdfs)
            except Exception:
                continue

        return _dedupe_paths(foreground_matches or fallback_matches)
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _iter_explorer_windows(shell: object) -> Iterable[object]:
    try:
        windows = shell.Windows()
    except Exception:
        return []

    items = []
    try:
        count = int(windows.Count)
    except Exception:
        count = 0

    for index in range(count):
        try:
            window = windows.Item(index)
            full_name = str(getattr(window, "FullName", "") or "").lower()
            if "explorer.exe" in full_name:
                items.append(window)
        except Exception:
            continue

    return items


def _selected_paths_from_window(window: object) -> List[str]:
    paths: List[str] = []
    try:
        selected_items = window.Document.SelectedItems()
        count = int(selected_items.Count)
    except Exception:
        return paths

    for index in range(count):
        try:
            item = selected_items.Item(index)
            path = str(item.Path or "")
        except Exception:
            continue
        if path:
            paths.append(path)

    return paths


def _filter_pdf_paths(paths: Sequence[str]) -> List[str]:
    pdfs: List[str] = []
    for raw_path in paths:
        try:
            path = Path(raw_path)
            if path.suffix.lower() == ".pdf" and path.exists():
                pdfs.append(str(path.resolve()))
        except OSError:
            continue
    return pdfs


def _dedupe_paths(paths: Sequence[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for path in paths:
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result
