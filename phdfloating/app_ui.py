"""Tkinter desktop floating ball UI."""

from __future__ import annotations

import ctypes
import math
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Any, Dict, List, Optional, Tuple

from .llm_client import OpenAICompatibleLLMClient
from .pdf_text_extractor import build_input_json_list
from .settings_store import (
    DEFAULT_SETTINGS,
    ensure_api_settings,
    load_settings,
    reset_settings,
    save_settings,
    sanitize_settings,
)
from .summary_pdf_writer import write_summary_pdf
from .windows_selection import get_selected_pdf_paths


COLOR_PRIMARY = "#3BAFA9"
COLOR_HIGHLIGHT = "#8EDAD5"
COLOR_SHADOW = "#2C8F89"
COLOR_TEXT = "#0F766E"
COLOR_BORDER = "#D6EEEC"
COLOR_BG = "#F6FBFB"
TRANSPARENT = "#123456"


class FloatingSummaryApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.geometry("+120+160")

        self.expanded = False
        self.running = False
        self.dragging = False
        self.moved = False
        self.drag_start = (0, 0)
        self.window_start = (80, 120)
        self.single_click_job: Optional[str] = None
        self.pressed_button: Optional[int] = None
        self.settings_window: Optional[tk.Toplevel] = None
        self.show_api_key = False
        self.result_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()

        self.canvas = tk.Canvas(
            self.root,
            width=60,
            height=60,
            highlightthickness=0,
            bg=TRANSPARENT,
        )
        self.canvas.pack()

        self._set_window_size()
        self.root.geometry("60x60+120+160")
        self._bind_events()
        self._apply_no_activate_style()
        self._redraw()
        self.root.after(150, self._poll_result_queue)

    def run(self) -> None:
        self.root.mainloop()

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.root.bind("<Escape>", lambda _event: self._collapse())

    def _apply_no_activate_style(self) -> None:
        try:
            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            user32 = ctypes.windll.user32
            gwl_exstyle = -20
            ws_ex_toolwindow = 0x00000080
            ws_ex_noactivate = 0x08000000
            swp_nomove = 0x0002
            swp_nosize = 0x0001
            swp_noactivate = 0x0010
            swp_framechanged = 0x0020
            hwnd_topmost = -1
            style = user32.GetWindowLongW(hwnd, gwl_exstyle)
            style |= ws_ex_toolwindow | ws_ex_noactivate
            user32.SetWindowLongW(hwnd, gwl_exstyle, style)
            user32.SetWindowPos(
                hwnd,
                hwnd_topmost,
                0,
                0,
                0,
                0,
                swp_nomove | swp_nosize | swp_noactivate | swp_framechanged,
            )
        except Exception:
            pass

    def _set_window_size(self) -> None:
        width, height = (158, 60) if self.expanded else (60, 60)
        self.canvas.configure(width=width, height=height)
        self.root.geometry(f"{width}x{height}+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def _redraw(self) -> None:
        self.canvas.delete("all")
        if self.expanded:
            self._draw_expanded()
        else:
            self._draw_ball()

    def _draw_ball(self) -> None:
        ball = self._load_ui_image("悬浮球_60")
        self.canvas.create_image(30, 30, image=ball)
        if self.running:
            self.canvas.create_arc(7, 7, 53, 53, start=30, extent=270, outline="#FFFFFF", width=2)

    def _draw_expanded(self) -> None:
        expanded = self._load_ui_image("expanded_combined_1_2x")
        self.canvas.create_image(79, 30, image=expanded)
        if self.running:
            self.canvas.create_text(
                79,
                55,
                text="总结中...",
                fill=COLOR_TEXT,
                font=("Microsoft YaHei UI", 7, "bold"),
            )

    def _round_rect(self, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs: Any) -> None:
        points = [
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
        ]
        self.canvas.create_polygon(points, smooth=True, **kwargs)

    def _draw_document_icon(self, cx: int, cy: int, scale: float = 1.0) -> None:
        size = 26 * scale
        left = cx - size * 0.42
        top = cy - size * 0.50
        right = cx + size * 0.38
        bottom = cy + size * 0.50
        fold = size * 0.28
        self.canvas.create_line(left, top, right - fold, top, right, top + fold, right, bottom, left, bottom, left, top, fill="white", width=2.3)
        self.canvas.create_line(right - fold, top, right - fold, top + fold, right, top + fold, fill="white", width=2.0)
        for offset in (0.08, 0.27, 0.46):
            y = top + size * offset + fold
            self.canvas.create_line(left + size * 0.18, y, right - size * 0.16, y, fill="white", width=2.0)

    def _draw_gear_icon(self, cx: int, cy: int, scale: float = 1.0) -> None:
        r_outer = 16 * scale
        r_inner = 7 * scale
        for angle in range(0, 360, 45):
            radians = math.radians(angle)
            self.canvas.create_line(
                cx + math.cos(radians) * (r_outer - 3),
                cy + math.sin(radians) * (r_outer - 3),
                cx + math.cos(radians) * (r_outer + 4),
                cy + math.sin(radians) * (r_outer + 4),
                fill="white",
                width=3,
                capstyle=tk.ROUND,
            )
        self.canvas.create_oval(cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer, outline="white", width=3)
        self.canvas.create_oval(cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner, outline="white", width=3)

    def _draw_close_icon(self, cx: int, cy: int, scale: float = 1.0) -> None:
        span = 12 * scale
        self.canvas.create_line(cx - span, cy - span, cx + span, cy + span, fill="white", width=3, capstyle=tk.ROUND)
        self.canvas.create_line(cx + span, cy - span, cx - span, cy + span, fill="white", width=3, capstyle=tk.ROUND)

    def _button_centers(self) -> List[Tuple[int, int]]:
        return [(35, 30), (79, 30), (123, 30)]

    def _hit_button(self, x: int, y: int) -> Optional[int]:
        if not self.expanded:
            return None
        for index, (cx, cy) in enumerate(self._button_centers()):
            if (x - cx) ** 2 + (y - cy) ** 2 <= 20 ** 2:
                return index
        return None

    def _on_press(self, event: tk.Event) -> None:
        self.drag_start = (event.x_root, event.y_root)
        self.window_start = (self.root.winfo_x(), self.root.winfo_y())
        self.dragging = True
        self.moved = False
        self.pressed_button = self._hit_button(event.x, event.y)

    def _on_motion(self, event: tk.Event) -> None:
        if not self.dragging:
            return
        dx = event.x_root - self.drag_start[0]
        dy = event.y_root - self.drag_start[1]
        if abs(dx) + abs(dy) < 5:
            return
        self.moved = True
        x = self.window_start[0] + dx
        y = self.window_start[1] + dy
        self.root.geometry(f"+{x}+{y}")

    def _on_release(self, event: tk.Event) -> None:
        if not self.dragging:
            return
        self.dragging = False
        if self.moved:
            return

        released_button = self._hit_button(event.x, event.y)
        if self.expanded and released_button is not None and released_button == self.pressed_button:
            self._run_expanded_action(released_button)
            return

        if self.single_click_job:
            self.root.after_cancel(self.single_click_job)
        self.single_click_job = self.root.after(230, self._single_click)

    def _on_double_click(self, event: tk.Event) -> None:
        if self.single_click_job:
            self.root.after_cancel(self.single_click_job)
            self.single_click_job = None
        if self._hit_button(event.x, event.y) is not None:
            return
        self.expanded = not self.expanded
        self._set_window_size()
        self._redraw()

    def _single_click(self) -> None:
        self.single_click_job = None
        if self.expanded:
            return
        self.start_summary()

    def _run_expanded_action(self, index: int) -> None:
        if index == 0:
            self.start_summary()
        elif index == 1:
            self.show_settings()
        elif index == 2:
            self.root.destroy()

    def _collapse(self) -> None:
        if self.expanded:
            self.expanded = False
            self._set_window_size()
            self._redraw()

    def start_summary(self) -> None:
        if self.running:
            messagebox.showinfo("正在总结", "上一批 PDF 还在处理中，请稍等。")
            return

        settings = sanitize_settings(load_settings())
        try:
            ensure_api_settings(settings)
        except ValueError as error:
            self.show_settings()
            messagebox.showwarning("缺少 API 配置", str(error))
            return

        selected_pdfs = get_selected_pdf_paths()
        if not selected_pdfs:
            messagebox.showwarning(
                "未找到选中的 PDF",
                "请先在 Windows 文件资源管理器中选中 PDF 文件，再单击悬浮球。",
            )
            return

        extraction = build_input_json_list(
            selected_pdfs,
            max_files=settings["limits"]["maxPdfFiles"],
            max_chars_per_pdf=settings["limits"]["maxCharsPerPdf"],
        )
        if not extraction.input_json_list:
            messagebox.showwarning("未找到 PDF", "当前选择中没有可处理的 PDF 文件。")
            return

        if extraction.truncated:
            messagebox.showwarning(
                "PDF 数量超过限制",
                f"检测到 {len(selected_pdfs)} 份 PDF，本次只处理前 {len(extraction.accepted_paths)} 份。",
            )

        self.running = True
        self._redraw()
        worker = threading.Thread(
            target=self._summary_worker,
            args=(settings, extraction.input_json_list, extraction.errors),
            daemon=True,
        )
        worker.start()

    def _summary_worker(
        self,
        settings: Dict[str, Any],
        input_json_list: List[Dict[str, str]],
        extraction_errors: List[str],
    ) -> None:
        try:
            client = OpenAICompatibleLLMClient(settings)
            summaries = client.summarize_input_json_list(input_json_list)
            output_path = write_summary_pdf(summaries)
            self.result_queue.put(("success", (output_path, extraction_errors)))
        except Exception as error:
            self.result_queue.put(("error", str(error)))

    def _poll_result_queue(self) -> None:
        try:
            while True:
                status, payload = self.result_queue.get_nowait()
                self.running = False
                self._redraw()
                if status == "success":
                    output_path, extraction_errors = payload
                    message = f"总结完成，已输出：\n{output_path}"
                    if extraction_errors:
                        message += "\n\n部分 PDF 文本提取有警告：\n" + "\n".join(extraction_errors[:3])
                    messagebox.showinfo("完成", message)
                else:
                    messagebox.showerror("总结失败", str(payload))
        except queue.Empty:
            pass
        self.root.after(150, self._poll_result_queue)

    def show_settings(self) -> None:
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return

        settings = sanitize_settings(load_settings())
        llm = settings["llm"]
        win = tk.Toplevel(self.root)
        self.settings_window = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=COLOR_BG)
        win.geometry(self._settings_geometry())

        container = tk.Frame(win, bg="#FFFFFF", highlightbackground=COLOR_BORDER, highlightthickness=1)
        container.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        header = tk.Frame(container, bg="#FFFFFF")
        header.pack(fill=tk.X, padx=28, pady=(24, 8))
        title = tk.Label(
            header,
            text="模型设置",
            fg=COLOR_TEXT,
            bg="#FFFFFF",
            font=("Microsoft YaHei UI", 20, "bold"),
        )
        title.pack(side=tk.LEFT)
        close = tk.Button(
            header,
            text="×",
            command=win.destroy,
            width=3,
            bg="#E6F7F5",
            fg=COLOR_TEXT,
            relief=tk.FLAT,
            font=("Microsoft YaHei UI", 16, "bold"),
        )
        close.pack(side=tk.RIGHT)

        subtitle = tk.Label(
            container,
            text="配置方式与 OpenAI-compatible 参数结构一致。API Key 只保存在当前设备。",
            fg="#687785",
            bg="#FFFFFF",
            anchor="w",
            justify=tk.LEFT,
            font=("Microsoft YaHei UI", 10),
        )
        subtitle.pack(fill=tk.X, padx=30, pady=(0, 22))

        form = tk.Frame(container, bg="#FFFFFF")
        form.pack(fill=tk.BOTH, padx=30)

        entries: Dict[str, tk.Entry] = {}
        entries["apiUrl"] = self._add_field(form, "API URL", llm["apiUrl"], row=0)
        api_key_entry = self._add_field(form, "API Key", llm["apiKey"], row=1, show="*")
        entries["apiKey"] = api_key_entry
        entries["model"] = self._add_field(form, "模型名", llm["model"], row=2)
        entries["temperature"] = self._add_field(form, "Temperature", str(llm["temperature"]), row=3)
        entries["requestTimeoutMs"] = self._add_field(
            form,
            "请求超时(ms)",
            str(llm["requestTimeoutMs"]),
            row=4,
        )

        eye_button = tk.Button(
            form,
            text="显示",
            command=lambda: self._toggle_api_key(api_key_entry, eye_button),
            bg="#E6F7F5",
            fg=COLOR_TEXT,
            relief=tk.FLAT,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        eye_button.grid(row=1, column=2, sticky="w", padx=(10, 0))

        status_label = tk.Label(
            container,
            text="",
            fg=COLOR_TEXT,
            bg="#FFFFFF",
            anchor="w",
            font=("Microsoft YaHei UI", 10),
        )
        status_label.pack(fill=tk.X, padx=30, pady=(12, 0))

        buttons = tk.Frame(container, bg="#FFFFFF")
        buttons.pack(fill=tk.X, padx=30, pady=(18, 26))

        save_button = tk.Button(
            buttons,
            text="保存配置",
            command=lambda: self._save_settings_from_form(entries, status_label),
            bg=COLOR_PRIMARY,
            fg="#FFFFFF",
            relief=tk.FLAT,
            padx=28,
            pady=10,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        save_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

        clear_button = tk.Button(
            buttons,
            text="清空配置",
            command=lambda: self._clear_settings_form(entries, status_label),
            bg="#EEF7F6",
            fg=COLOR_TEXT,
            relief=tk.FLAT,
            padx=28,
            pady=10,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        clear_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(16, 0))

        self._enable_window_drag(win, header)
        self._enable_window_drag(win, title)

    def _settings_geometry(self) -> str:
        x = max(20, self.root.winfo_x() + 20)
        y = max(20, self.root.winfo_y() + 90)
        return f"660x620+{x}+{y}"

    def _add_field(
        self,
        parent: tk.Frame,
        label_text: str,
        value: str,
        row: int,
        show: Optional[str] = None,
    ) -> tk.Entry:
        label = tk.Label(
            parent,
            text=label_text,
            width=16,
            anchor="w",
            fg=COLOR_TEXT,
            bg="#FFFFFF",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        label.grid(row=row, column=0, sticky="w", pady=10)

        entry = tk.Entry(
            parent,
            show=show or "",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_PRIMARY,
            fg="#1F2A37",
            bg="#FFFFFF",
            insertbackground=COLOR_TEXT,
            font=("Microsoft YaHei UI", 11),
        )
        entry.grid(row=row, column=1, sticky="ew", ipady=10, pady=10)
        entry.insert(0, str(value or ""))
        parent.grid_columnconfigure(1, weight=1)
        return entry

    def _toggle_api_key(self, entry: tk.Entry, button: tk.Button) -> None:
        self.show_api_key = not self.show_api_key
        entry.configure(show="" if self.show_api_key else "*")
        button.configure(text="隐藏" if self.show_api_key else "显示")

    def _save_settings_from_form(self, entries: Dict[str, tk.Entry], status_label: tk.Label) -> None:
        try:
            current = sanitize_settings(load_settings())
            next_settings = {
                **current,
                "llm": {
                    **current["llm"],
                    "apiUrl": entries["apiUrl"].get().strip(),
                    "apiKey": entries["apiKey"].get().strip(),
                    "model": entries["model"].get().strip(),
                    "temperature": entries["temperature"].get().strip(),
                    "requestTimeoutMs": entries["requestTimeoutMs"].get().strip(),
                },
            }
            save_settings(next_settings)
            status_label.configure(text="配置已保存。")
        except Exception as error:
            status_label.configure(text=f"保存失败：{error}")

    def _clear_settings_form(self, entries: Dict[str, tk.Entry], status_label: tk.Label) -> None:
        reset_settings()
        defaults = sanitize_settings(DEFAULT_SETTINGS)["llm"]
        for key, entry in entries.items():
            entry.delete(0, tk.END)
            entry.insert(0, str(defaults.get(key) or ""))
        status_label.configure(text="配置已清空。")

    def _enable_window_drag(self, win: tk.Toplevel, widget: tk.Widget) -> None:
        state = {"x": 0, "y": 0, "win_x": 0, "win_y": 0}

        def on_press(event: tk.Event) -> None:
            state["x"] = event.x_root
            state["y"] = event.y_root
            state["win_x"] = win.winfo_x()
            state["win_y"] = win.winfo_y()

        def on_motion(event: tk.Event) -> None:
            dx = event.x_root - state["x"]
            dy = event.y_root - state["y"]
            win.geometry(f"+{state['win_x'] + dx}+{state['win_y'] + dy}")

        widget.bind("<ButtonPress-1>", on_press)
        widget.bind("<B1-Motion>", on_motion)

    def show_settings(self) -> None:
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return

        settings = sanitize_settings(load_settings())
        llm = settings["llm"]
        win = tk.Toplevel(self.root)
        self.settings_window = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=TRANSPARENT)
        win.attributes("-transparentcolor", TRANSPARENT)
        win.geometry(self._settings_geometry())

        panel = tk.Canvas(win, width=430, height=540, bg=TRANSPARENT, highlightthickness=0)
        panel.pack(fill=tk.BOTH, expand=True)
        panel.create_oval(18, 18, 418, 536, fill="#E7F4F3", outline="")
        self._settings_round_rect(panel, 8, 8, 422, 528, 16, fill="#FBFEFE", outline=COLOR_BORDER, width=1)

        container = tk.Frame(panel, bg="#FBFEFE")
        panel.create_window(24, 24, anchor="nw", width=382, height=486, window=container)

        header = tk.Frame(container, bg="#FBFEFE")
        header.pack(fill=tk.X)
        title = tk.Label(
            header,
            text="模型设置",
            fg=COLOR_TEXT,
            bg="#FBFEFE",
            font=("Microsoft YaHei UI", 17, "bold"),
        )
        title.pack(side=tk.LEFT)
        close = tk.Button(
            header,
            text="×",
            command=win.destroy,
            width=2,
            bg="#E6F7F5",
            fg=COLOR_TEXT,
            relief=tk.FLAT,
            activebackground="#D8F1EF",
            activeforeground=COLOR_TEXT,
            cursor="hand2",
            font=("Microsoft YaHei UI", 15, "bold"),
        )
        close.pack(side=tk.RIGHT)

        subtitle = tk.Label(
            container,
            text="配置方式与参考扩展一致，采用本地保存的 OpenAI-compatible\n参数结构。API Key 只保存在当前设备。",
            fg="#687785",
            bg="#FBFEFE",
            anchor="w",
            justify=tk.LEFT,
            font=("Microsoft YaHei UI", 9),
        )
        subtitle.pack(fill=tk.X, pady=(14, 22))

        form = tk.Frame(container, bg="#FBFEFE")
        form.pack(fill=tk.X)

        entries: Dict[str, tk.Entry] = {}
        entries["apiUrl"] = self._add_settings_field(form, "api_url", "API URL", llm["apiUrl"], row=0)
        api_key_entry = self._add_settings_field(
            form,
            "api_key",
            "API Key",
            llm["apiKey"],
            row=1,
            show="*",
            suffix="eye",
        )
        entries["apiKey"] = api_key_entry
        entries["model"] = self._add_settings_field(form, "模型名", "模型名", llm["model"], row=2)
        entries["temperature"] = self._add_settings_field(
            form,
            "temperature",
            "Temperature",
            str(llm["temperature"]),
            row=3,
        )
        entries["requestTimeoutMs"] = self._add_settings_field(
            form,
            "请求超时",
            "请求超时(ms)",
            str(llm["requestTimeoutMs"]),
            row=4,
        )

        status_label = tk.Label(
            container,
            text="",
            fg=COLOR_TEXT,
            bg="#FBFEFE",
            anchor="w",
            font=("Microsoft YaHei UI", 9),
        )
        status_label.pack(fill=tk.X, pady=(8, 0))

        buttons = tk.Frame(container, bg="#FBFEFE")
        buttons.pack(fill=tk.X, pady=(18, 0))
        save_icon = self._load_settings_icon("保存配置")
        trash_icon = self._load_settings_icon("清空配置")
        save_button = tk.Button(
            buttons,
            text="  保存配置",
            image=save_icon,
            compound=tk.LEFT,
            command=lambda: self._save_settings_from_form(entries, status_label),
            bg=COLOR_PRIMARY,
            fg="#FFFFFF",
            relief=tk.FLAT,
            activebackground="#35A7A1",
            activeforeground="#FFFFFF",
            cursor="hand2",
            padx=22,
            pady=12,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        save_button.image = save_icon
        save_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

        clear_button = tk.Button(
            buttons,
            text="  清空配置",
            image=trash_icon,
            compound=tk.LEFT,
            command=lambda: self._clear_settings_form(entries, status_label),
            bg="#EEF7F6",
            fg=COLOR_TEXT,
            relief=tk.FLAT,
            activebackground="#E2F1F0",
            activeforeground=COLOR_TEXT,
            cursor="hand2",
            padx=22,
            pady=12,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        clear_button.image = trash_icon
        clear_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(16, 0))

        self._enable_window_drag(win, panel)
        self._enable_window_drag(win, header)
        self._enable_window_drag(win, title)

    def _settings_geometry(self) -> str:
        x = max(20, self.root.winfo_x() + 20)
        y = max(20, self.root.winfo_y() + 90)
        return f"430x540+{x}+{y}"

    def _settings_round_rect(self, canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs: Any) -> None:
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        canvas.create_polygon(points, smooth=True, **kwargs)

    def _add_settings_field(
        self,
        parent: tk.Frame,
        icon: str,
        label_text: str,
        value: str,
        row: int,
        show: str = "",
        suffix: str = "",
    ) -> tk.Entry:
        line = tk.Frame(parent, bg="#FBFEFE")
        line.grid(row=row, column=0, sticky="ew", pady=8)
        parent.grid_columnconfigure(0, weight=1)

        icon_image = self._load_settings_icon(icon)
        icon_label = tk.Label(line, image=icon_image, bg="#FBFEFE")
        icon_label.image = icon_image
        icon_label.pack(side=tk.LEFT, padx=(0, 8))

        label = tk.Label(
            line,
            text=label_text,
            width=13,
            anchor="w",
            fg=COLOR_TEXT,
            bg="#FBFEFE",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        label.pack(side=tk.LEFT)

        field = tk.Frame(line, bg="#FBFEFE", width=238, height=38)
        field.pack(side=tk.LEFT, fill=tk.X, expand=True)
        field.pack_propagate(False)

        shell = tk.Canvas(field, width=238, height=38, bg="#FBFEFE", highlightthickness=0)
        shell.place(x=0, y=0, relwidth=1, relheight=1)
        self._settings_round_rect(shell, 1, 1, 237, 37, 12, fill="#FFFFFF", outline=COLOR_BORDER, width=1)

        entry = tk.Entry(
            field,
            show=show,
            relief=tk.FLAT,
            bd=0,
            fg="#4D5C68",
            bg="#FFFFFF",
            insertbackground=COLOR_TEXT,
            font=("Microsoft YaHei UI", 9),
        )
        entry.place(x=14, y=9, width=176 if suffix == "eye" else 208, height=20)
        entry.insert(0, str(value or ""))

        if suffix == "eye":
            button = tk.Button(
                field,
                text="⊙",
                command=lambda: self._toggle_api_key(entry, button),
                bg="#FFFFFF",
                fg=COLOR_TEXT,
                relief=tk.FLAT,
                activebackground="#FFFFFF",
                activeforeground=COLOR_TEXT,
                cursor="hand2",
                font=("Microsoft YaHei UI", 12, "bold"),
            )
            button.place(x=200, y=5, width=28, height=28)

        return entry

    def _load_settings_icon(self, name: str) -> tk.PhotoImage:
        if not hasattr(self, "_settings_icon_images"):
            self._settings_icon_images = {}
        cache = self._settings_icon_images
        if name not in cache:
            icon_path = Path(__file__).resolve().parent.parent / "ui" / f"{name}.png"
            image = tk.PhotoImage(file=str(icon_path))
            max_side = max(image.width(), image.height())
            factor = max(1, round(max_side / 22))
            cache[name] = image.subsample(factor, factor)
        return cache[name]

    def _load_ui_image(self, name: str) -> tk.PhotoImage:
        if not hasattr(self, "_ui_images"):
            self._ui_images = {}
        cache = self._ui_images
        if name not in cache:
            icon_path = Path(__file__).resolve().parent.parent / "ui" / f"{name}.png"
            cache[name] = tk.PhotoImage(file=str(icon_path))
        return cache[name]

    def _toggle_api_key(self, entry: tk.Entry, button: tk.Button) -> None:
        self.show_api_key = not self.show_api_key
        entry.configure(show="" if self.show_api_key else "*")
        button.configure(text="◉" if self.show_api_key else "⊙")

    def _save_settings_from_form(self, entries: Dict[str, tk.Entry], status_label: tk.Label) -> None:
        try:
            current = sanitize_settings(load_settings())
            next_settings = {
                **current,
                "llm": {
                    **current["llm"],
                    "apiUrl": entries["apiUrl"].get().strip(),
                    "apiKey": entries["apiKey"].get().strip(),
                    "model": entries["model"].get().strip(),
                    "temperature": entries["temperature"].get().strip(),
                    "requestTimeoutMs": entries["requestTimeoutMs"].get().strip(),
                },
            }
            save_settings(next_settings)
            status_label.configure(text="配置已保存。")
        except Exception as error:
            status_label.configure(text=f"保存失败：{error}")

    def _clear_settings_form(self, entries: Dict[str, tk.Entry], status_label: tk.Label) -> None:
        reset_settings()
        defaults = sanitize_settings(DEFAULT_SETTINGS)["llm"]
        for key, entry in entries.items():
            entry.delete(0, tk.END)
            entry.insert(0, str(defaults.get(key) or ""))
        status_label.configure(text="配置已清空。")
