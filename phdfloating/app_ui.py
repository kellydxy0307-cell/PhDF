"""Tkinter desktop floating ball UI."""

from __future__ import annotations

from collections import deque
import ctypes
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageTk

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
COLOR_TEXT = "#0F766E"
COLOR_BORDER = "#D6EEEC"
TRANSPARENT = "#123456"

SUMMARY_LANGUAGE_CHOICES = (
    ("中文简体", "zh-CN"),
    ("English", "en"),
)

ICON_COLLAPSED = "floating_ball_60.png"
ICON_LOADING_BACKGROUND = "gif/expanded_loading_bg.png"
ICON_EXPAND_SETTINGS = "expand_settings_1_2x.png"
ICON_EXPAND_CLOSE = "expand_close_1_2x.png"
ICON_COLLAPSED_LOADING = "gif/collapsed_loading_ball_60.png"
SEQUENCE_LOADING = "gif/loading_frames"
SEQUENCE_EXPAND = "gif/expand_frames"

COLLAPSED_SIZE = (60, 60)
BASE_EXPANDED_SIZE = (158, 60)
TARGET_EXPANDED_HEIGHT = 66
BASE_BUTTON_CENTERS = ((30, 30), (79, 30), (127, 30))
BASE_BUTTON_RADIUS = 20
EXPAND_ANIMATION_MS = 38
LOADING_ANIMATION_MS = 70
OUTER_ALPHA_THRESHOLD = 56
LOW_ALPHA_NOISE_THRESHOLD = 8


class FloatingSummaryApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.geometry("+120+160")

        self.ui_root = Path(__file__).resolve().parent.parent / "ui"
        self.expanded_size = self._infer_expanded_size()
        self.expanded_image_center = (self.expanded_size[0] // 2, self.expanded_size[1] // 2)
        self.button_centers = self._scale_button_centers()
        self.button_radius = self._scale_button_radius()
        self.loading_size = self._infer_loading_size()

        self.expanded = False
        self.running = False
        self.animating_expand = False
        self.dragging = False
        self.moved = False
        self.drag_start = (0, 0)
        self.window_start = (80, 120)
        self.single_click_job: Optional[str] = None
        self.expand_animation_job: Optional[str] = None
        self.loading_animation_job: Optional[str] = None
        self.selection_poll_job: Optional[str] = None
        self.expand_animation_target = False
        self.expand_animation_frames: List[ImageTk.PhotoImage] = []
        self.loading_frame_index = 0
        self.pressed_button: Optional[int] = None
        self.settings_window: Optional[tk.Toplevel] = None
        self.show_api_key = False
        self.selection_snapshot: List[str] = []
        self.selection_snapshot_at = 0.0
        self.result_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()

        self._ui_images: Dict[Tuple[str, Tuple[int, int] | None], ImageTk.PhotoImage] = {}
        self._ui_sequences: Dict[Tuple[str, Tuple[int, int] | None], List[ImageTk.PhotoImage]] = {}
        self._settings_icon_images: Dict[str, tk.PhotoImage] = {}

        self.canvas = tk.Canvas(
            self.root,
            width=COLLAPSED_SIZE[0],
            height=COLLAPSED_SIZE[1],
            highlightthickness=0,
            bg=TRANSPARENT,
        )
        self.canvas.pack()

        self._set_window_size(*COLLAPSED_SIZE)
        self._bind_events()
        self._apply_no_activate_style()
        self._redraw()
        self._refresh_selection_snapshot()
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

    def _set_window_size(self, width: int, height: int) -> None:
        self.canvas.configure(width=width, height=height)
        self.root.geometry(f"{width}x{height}+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def _redraw(self) -> None:
        if self.animating_expand:
            return
        self.canvas.delete("all")
        if self.expanded:
            self._draw_expanded_state()
        else:
            self._draw_collapsed_state()

    def _draw_collapsed_state(self) -> None:
        ball = self._load_ui_image(ICON_COLLAPSED)
        self.canvas.create_image(COLLAPSED_SIZE[0] // 2, COLLAPSED_SIZE[1] // 2, image=ball)
        if self.running:
            spinner = self._current_loading_frame()
            if spinner is not None:
                self.canvas.create_image(COLLAPSED_SIZE[0] // 2, COLLAPSED_SIZE[1] // 2, image=spinner)

    def _draw_expanded_state(self) -> None:
        if self.running:
            self._draw_expanded_loading_state()
            return
        self.canvas.create_image(*self.expanded_image_center, image=self._static_expanded_frame())

    def _draw_expanded_loading_state(self) -> None:
        background = self._load_ui_image(ICON_LOADING_BACKGROUND, size=self.expanded_size)
        gear_icon = self._load_ui_image(ICON_EXPAND_SETTINGS)
        close_icon = self._load_ui_image(ICON_EXPAND_CLOSE)

        self.canvas.create_image(*self.expanded_image_center, image=background)
        spinner = self._current_loading_frame()
        if spinner is not None:
            self.canvas.create_image(*self.button_centers[0], image=spinner)
        self.canvas.create_image(*self.button_centers[1], image=gear_icon)
        self.canvas.create_image(*self.button_centers[2], image=close_icon)

    def _hit_button(self, x: int, y: int) -> Optional[int]:
        if not self.expanded or self.animating_expand:
            return None
        for index, (cx, cy) in enumerate(self.button_centers):
            if (x - cx) ** 2 + (y - cy) ** 2 <= self.button_radius ** 2:
                return index
        return None

    def _on_press(self, event: tk.Event) -> None:
        self._capture_selection_snapshot()
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
            self.pressed_button = None
            return

        released_button = self._hit_button(event.x, event.y)
        if self.expanded and released_button is not None and released_button == self.pressed_button:
            self.pressed_button = None
            self._run_expanded_action(released_button)
            return
        self.pressed_button = None

        if self.animating_expand:
            return

        if self.single_click_job:
            self.root.after_cancel(self.single_click_job)
        self.single_click_job = self.root.after(230, self._single_click)

    def _on_double_click(self, event: tk.Event) -> None:
        if self.single_click_job:
            self.root.after_cancel(self.single_click_job)
            self.single_click_job = None
        if self.animating_expand:
            return
        if self._hit_button(event.x, event.y) is not None:
            return
        self._toggle_expanded()

    def _single_click(self) -> None:
        self.single_click_job = None
        if self.expanded or self.animating_expand:
            return
        self.start_summary()

    def _toggle_expanded(self) -> None:
        self._play_expand_animation(expand=not self.expanded)

    def _play_expand_animation(self, expand: bool) -> None:
        frames = self._load_ui_sequence(SEQUENCE_EXPAND, size=self.expanded_size)
        if not frames:
            self.expanded = expand
            self._set_window_size(*(self.expanded_size if expand else COLLAPSED_SIZE))
            self._redraw()
            return

        if self.expand_animation_job:
            self.root.after_cancel(self.expand_animation_job)
            self.expand_animation_job = None

        self.animating_expand = True
        self.expand_animation_target = expand
        self.expand_animation_frames = frames if expand else list(reversed(frames))
        self._set_window_size(*self.expanded_size)
        self._advance_expand_animation(0)

    def _advance_expand_animation(self, frame_index: int) -> None:
        if frame_index >= len(self.expand_animation_frames):
            self.animating_expand = False
            self.expand_animation_job = None
            self.expanded = self.expand_animation_target
            if not self.expanded:
                self._set_window_size(*COLLAPSED_SIZE)
            self._redraw()
            return

        self.canvas.delete("all")
        self.canvas.create_image(*self.expanded_image_center, image=self.expand_animation_frames[frame_index])
        self.expand_animation_job = self.root.after(
            EXPAND_ANIMATION_MS,
            lambda: self._advance_expand_animation(frame_index + 1),
        )

    def _run_expanded_action(self, index: int) -> None:
        if index == 0:
            self.start_summary()
        elif index == 1:
            self.show_settings()
        elif index == 2:
            self.root.destroy()

    def _collapse(self) -> None:
        if self.expanded and not self.animating_expand:
            self._play_expand_animation(expand=False)

    def start_summary(self) -> None:
        settings = sanitize_settings(load_settings())
        try:
            ensure_api_settings(settings)
        except ValueError as error:
            self.show_settings()
            messagebox.showwarning("缺少 API 配置", str(error))
            return

        if self.running:
            messagebox.showinfo("正在总结", "上一批 PDF 还在处理中，请稍等。")
            return

        selected_pdfs = self._resolve_selected_pdfs()
        if not selected_pdfs:
            messagebox.showwarning(
                "未找到选中的 PDF",
                "请先在 Windows 文件资源管理器中选中 PDF 文件，再单击悬浮球。",
            )
            return

        self.running = True
        self.loading_frame_index = 0
        self._start_loading_animation()
        self._redraw()

        try:
            extraction = build_input_json_list(
                selected_pdfs,
                max_files=settings["limits"]["maxPdfFiles"],
                max_chars_per_pdf=settings["limits"]["maxCharsPerPdf"],
            )
        except Exception as error:
            self._reset_loading_state()
            messagebox.showerror("PDF 读取失败", str(error))
            return

        if not extraction.input_json_list:
            self._reset_loading_state()
            messagebox.showwarning("未找到 PDF", "当前选择中没有可处理的 PDF 文件。")
            return

        if extraction.truncated:
            messagebox.showwarning(
                "PDF 数量超过限制",
                f"检测到 {len(selected_pdfs)} 份 PDF，本次只处理前 {len(extraction.accepted_paths)} 份。",
            )

        worker = threading.Thread(
            target=self._summary_worker,
            args=(settings, extraction.input_json_list, extraction.errors),
            daemon=True,
        )
        worker.start()

    def _capture_selection_snapshot(self) -> None:
        try:
            selected_pdfs = get_selected_pdf_paths()
        except Exception:
            selected_pdfs = []
        if selected_pdfs:
            self.selection_snapshot = selected_pdfs
            self.selection_snapshot_at = time.monotonic()

    def _refresh_selection_snapshot(self) -> None:
        self.selection_poll_job = None
        self._capture_selection_snapshot()
        self.selection_poll_job = self.root.after(350, self._refresh_selection_snapshot)

    def _resolve_selected_pdfs(self) -> List[str]:
        selected_pdfs = get_selected_pdf_paths()
        if selected_pdfs:
            self.selection_snapshot = selected_pdfs
            self.selection_snapshot_at = time.monotonic()
            return selected_pdfs

        if time.monotonic() - self.selection_snapshot_at > 5.0:
            return []

        resolved_paths: List[str] = []
        seen = set()
        for raw_path in self.selection_snapshot:
            try:
                path = Path(raw_path)
            except OSError:
                continue
            if path.suffix.lower() != ".pdf" or not path.exists():
                continue
            resolved = str(path.resolve())
            key = resolved.lower()
            if key in seen:
                continue
            seen.add(key)
            resolved_paths.append(resolved)
        return resolved_paths

    def _summary_worker(
        self,
        settings: Dict[str, Any],
        input_json_list: List[Dict[str, str]],
        extraction_errors: List[str],
    ) -> None:
        try:
            client = OpenAICompatibleLLMClient(settings)
            summaries = client.summarize_input_json_list(input_json_list)
            output_path = write_summary_pdf(
                summaries,
                summary_language=settings["llm"]["summaryLanguage"],
            )
            self.result_queue.put(("success", (output_path, extraction_errors)))
        except Exception as error:
            self.result_queue.put(("error", str(error)))

    def _poll_result_queue(self) -> None:
        try:
            while True:
                status, payload = self.result_queue.get_nowait()
                self._reset_loading_state()
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

    def _start_loading_animation(self) -> None:
        if self.loading_animation_job is None:
            self._advance_loading_animation()

    def _stop_loading_animation(self) -> None:
        if self.loading_animation_job:
            self.root.after_cancel(self.loading_animation_job)
            self.loading_animation_job = None
        self.loading_frame_index = 0

    def _reset_loading_state(self) -> None:
        self.running = False
        self._stop_loading_animation()
        self._redraw()

    def _advance_loading_animation(self) -> None:
        self.loading_animation_job = None
        if not self.running:
            return
        frames = self._load_ui_sequence(SEQUENCE_LOADING, size=self.loading_size)
        if frames:
            self.loading_frame_index = (self.loading_frame_index + 1) % len(frames)
            if not self.animating_expand:
                self._redraw()
        self.loading_animation_job = self.root.after(LOADING_ANIMATION_MS, self._advance_loading_animation)

    def _current_loading_frame(self) -> Optional[ImageTk.PhotoImage]:
        frames = self._load_ui_sequence(SEQUENCE_LOADING, size=self.loading_size)
        if not frames:
            return None
        return frames[self.loading_frame_index % len(frames)]

    def _static_expanded_frame(self) -> ImageTk.PhotoImage:
        frames = self._load_ui_sequence(SEQUENCE_EXPAND, size=self.expanded_size)
        if frames:
            return frames[-1]
        return self._load_ui_image("expanded_combined_1_2x.png", size=self.expanded_size)

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
        win.bind("<Destroy>", lambda _event: setattr(self, "settings_window", None))

        panel = tk.Canvas(win, width=430, height=620, bg=TRANSPARENT, highlightthickness=0)
        panel.pack(fill=tk.BOTH, expand=True)
        panel.create_oval(18, 18, 418, 616, fill="#E7F4F3", outline="")
        self._settings_round_rect(panel, 8, 8, 422, 608, 16, fill="#FBFEFE", outline=COLOR_BORDER, width=1)

        container = tk.Frame(panel, bg="#FBFEFE")
        panel.create_window(24, 24, anchor="nw", width=382, height=566, window=container)

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
        entries["apiKey"] = self._add_settings_field(
            form,
            "api_key",
            "API Key",
            llm["apiKey"],
            row=1,
            show="*",
            suffix="eye",
        )
        entries["model"] = self._add_settings_field(form, "model_name", "模型名", llm["model"], row=2)
        entries["temperature"] = self._add_settings_field(
            form,
            "temperature",
            "Temperature",
            str(llm["temperature"]),
            row=3,
        )
        entries["requestTimeoutMs"] = self._add_settings_field(
            form,
            "request_timeout",
            "请求超时(ms)",
            str(llm["requestTimeoutMs"]),
            row=4,
        )
        summary_language_var = self._add_summary_language_selector(container, llm["summaryLanguage"])

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

        save_icon = self._load_settings_icon("save_settings")
        clear_icon = self._load_settings_icon("clear_settings")

        save_button = tk.Button(
            buttons,
            text="  保存配置",
            image=save_icon,
            compound=tk.LEFT,
            command=lambda: self._save_settings_from_form(entries, summary_language_var, status_label),
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
            image=clear_icon,
            compound=tk.LEFT,
            command=lambda: self._clear_settings_form(entries, summary_language_var, status_label),
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
        clear_button.image = clear_icon
        clear_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(16, 0))

        self._enable_window_drag(win, panel)
        self._enable_window_drag(win, header)
        self._enable_window_drag(win, title)

    def _settings_geometry(self) -> str:
        x = max(20, self.root.winfo_x() + 20)
        y = max(20, self.root.winfo_y() + 90)
        return f"430x620+{x}+{y}"

    def _settings_round_rect(
        self,
        canvas: tk.Canvas,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int,
        **kwargs: Any,
    ) -> None:
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        canvas.create_polygon(points, smooth=True, **kwargs)

    def _add_summary_language_selector(self, parent: tk.Frame, selected_value: str) -> tk.StringVar:
        line = tk.Frame(parent, bg="#FBFEFE")
        line.pack(fill=tk.X, pady=(12, 0))

        label = tk.Label(
            line,
            text="总结语言",
            width=16,
            anchor="w",
            fg=COLOR_TEXT,
            bg="#FBFEFE",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        label.pack(side=tk.LEFT)

        shell = tk.Canvas(line, width=238, height=38, bg="#FBFEFE", highlightthickness=0)
        shell.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._settings_round_rect(shell, 1, 1, 237, 37, 12, fill="#FFFFFF", outline=COLOR_BORDER, width=1)

        tabs = tk.Frame(shell, bg="#FFFFFF")
        shell.create_window(4, 4, anchor="nw", width=230, height=30, window=tabs)

        valid_values = {value for _, value in SUMMARY_LANGUAGE_CHOICES}
        variable = tk.StringVar(value=selected_value if selected_value in valid_values else "zh-CN")
        buttons: Dict[str, tk.Button] = {}

        for index, (label_text, value) in enumerate(SUMMARY_LANGUAGE_CHOICES):
            button = tk.Button(
                tabs,
                text=label_text,
                relief=tk.FLAT,
                bd=0,
                cursor="hand2",
                font=("Microsoft YaHei UI", 9, "bold"),
                command=lambda selected=value: self._set_summary_language(variable, selected, buttons),
            )
            button.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6 if index == 0 else 0))
            buttons[value] = button

        setattr(variable, "_buttons", buttons)
        self._set_summary_language(variable, variable.get(), buttons)
        return variable

    def _set_summary_language(
        self,
        variable: tk.StringVar,
        selected_value: str,
        buttons: Dict[str, tk.Button],
    ) -> None:
        variable.set(selected_value)
        for value, button in buttons.items():
            is_selected = value == selected_value
            button.configure(
                bg=COLOR_PRIMARY if is_selected else "#FFFFFF",
                fg="#FFFFFF" if is_selected else COLOR_TEXT,
                activebackground="#35A7A1" if is_selected else "#F3FBFA",
                activeforeground="#FFFFFF" if is_selected else COLOR_TEXT,
            )

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
                text="显示",
                command=lambda: self._toggle_api_key(entry, button),
                bg="#FFFFFF",
                fg=COLOR_TEXT,
                relief=tk.FLAT,
                activebackground="#FFFFFF",
                activeforeground=COLOR_TEXT,
                cursor="hand2",
                font=("Microsoft YaHei UI", 8, "bold"),
            )
            button.place(x=192, y=6, width=36, height=26)

        return entry

    def _load_settings_icon(self, name: str) -> tk.PhotoImage:
        if name not in self._settings_icon_images:
            icon_path = self.ui_root / f"{name}.png"
            image = tk.PhotoImage(file=str(icon_path))
            max_side = max(image.width(), image.height())
            factor = max(1, round(max_side / 22))
            self._settings_icon_images[name] = image.subsample(factor, factor)
        return self._settings_icon_images[name]

    def _load_ui_image(
        self,
        name: str,
        size: Optional[Tuple[int, int]] = None,
    ) -> ImageTk.PhotoImage:
        if self.running and name.endswith("_60.png"):
            return self._load_collapsed_loading_ball_image()
        key = (name, size)
        if key not in self._ui_images:
            image = Image.open(self.ui_root / name).convert("RGBA")
            if size is not None and image.size != size:
                image = self._alpha_safe_resize(image, size)
            if self._should_harden_outer_silhouette(name):
                image = self._harden_outer_silhouette(image)
                image = self._strip_low_alpha_noise(image)
            self._ui_images[key] = ImageTk.PhotoImage(image)
        return self._ui_images[key]

    def _load_collapsed_loading_ball_image(self) -> ImageTk.PhotoImage:
        key = ("collapsed_loading_ball", COLLAPSED_SIZE)
        if key not in self._ui_images:
            image = Image.open(self.ui_root / ICON_COLLAPSED_LOADING).convert("RGBA")
            if image.size != COLLAPSED_SIZE:
                image = self._alpha_safe_resize(image, COLLAPSED_SIZE)
            image = self._harden_outer_silhouette(image)
            image = self._strip_low_alpha_noise(image)
            self._ui_images[key] = ImageTk.PhotoImage(image)
        return self._ui_images[key]

    def _load_ui_sequence(
        self,
        folder_name: str,
        size: Optional[Tuple[int, int]] = None,
    ) -> List[ImageTk.PhotoImage]:
        key = (folder_name, size)
        if key not in self._ui_sequences:
            folder = self.ui_root / folder_name
            frames: List[ImageTk.PhotoImage] = []
            for path in sorted(folder.glob("*.png"), key=lambda item: item.name):
                image = Image.open(path).convert("RGBA")
                if size is not None and image.size != size:
                    image = self._alpha_safe_resize(image, size)
                if size == self.expanded_size:
                    image = self._harden_outer_silhouette(image)
                    image = self._strip_low_alpha_noise(image)
                frames.append(ImageTk.PhotoImage(image))
            self._ui_sequences[key] = frames
        return self._ui_sequences[key]

    def _alpha_safe_resize(
        self,
        image: Image.Image,
        size: Tuple[int, int],
    ) -> Image.Image:
        return image.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")

    def _should_harden_outer_silhouette(self, name: str) -> bool:
        normalized = name.replace("\\", "/")
        return (
            normalized.endswith("_60.png")
            or normalized.endswith("expanded_loading_bg.png")
            or normalized.endswith("000010.png")
            or normalized.endswith("expanded_combined_1_2x.png")
        )

    def _harden_outer_silhouette(
        self,
        image: Image.Image,
        alpha_threshold: int = OUTER_ALPHA_THRESHOLD,
    ) -> Image.Image:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        width, height = rgba.size
        component_mask = [
            [alpha.getpixel((x, y)) > 0 for x in range(width)]
            for y in range(height)
        ]
        visited = [[False] * width for _ in range(height)]
        largest_component: List[Tuple[int, int]] = []
        directions = (
            (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1),
        )

        for y in range(height):
            for x in range(width):
                if not component_mask[y][x] or visited[y][x]:
                    continue
                queue_: "deque[Tuple[int, int]]" = deque([(x, y)])
                visited[y][x] = True
                current_component: List[Tuple[int, int]] = []
                while queue_:
                    cx, cy = queue_.popleft()
                    current_component.append((cx, cy))
                    for dx, dy in directions:
                        nx = cx + dx
                        ny = cy + dy
                        if 0 <= nx < width and 0 <= ny < height:
                            if component_mask[ny][nx] and not visited[ny][nx]:
                                visited[ny][nx] = True
                                queue_.append((nx, ny))
                if len(current_component) > len(largest_component):
                    largest_component = current_component

        if not largest_component:
            return rgba

        pixels = rgba.load()
        for x, y in largest_component:
            r, g, b, a = pixels[x, y]
            pixels[x, y] = (r, g, b, 255 if a >= alpha_threshold else 0)
        return rgba

    def _strip_low_alpha_noise(
        self,
        image: Image.Image,
        max_alpha: int = LOW_ALPHA_NOISE_THRESHOLD,
    ) -> Image.Image:
        rgba = image.convert("RGBA")
        pixels = rgba.load()
        width, height = rgba.size
        for y in range(height):
            for x in range(width):
                r, g, b, a = pixels[x, y]
                if 0 < a <= max_alpha:
                    pixels[x, y] = (r, g, b, 0)
        return rgba

    def _toggle_api_key(self, entry: tk.Entry, button: tk.Button) -> None:
        self.show_api_key = not self.show_api_key
        entry.configure(show="" if self.show_api_key else "*")
        button.configure(text="隐藏" if self.show_api_key else "显示")

    def _save_settings_from_form(
        self,
        entries: Dict[str, tk.Entry],
        summary_language_var: tk.StringVar,
        status_label: tk.Label,
    ) -> None:
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
                    "summaryLanguage": summary_language_var.get().strip(),
                },
            }
            save_settings(next_settings)
            status_label.configure(text="配置已保存。")
        except Exception as error:
            status_label.configure(text=f"保存失败：{error}")

    def _clear_settings_form(
        self,
        entries: Dict[str, tk.Entry],
        summary_language_var: tk.StringVar,
        status_label: tk.Label,
    ) -> None:
        reset_settings()
        defaults = sanitize_settings(DEFAULT_SETTINGS)["llm"]
        for key, entry in entries.items():
            entry.delete(0, tk.END)
            entry.insert(0, str(defaults.get(key) or ""))
        self._set_summary_language(
            summary_language_var,
            str(defaults.get("summaryLanguage") or "zh-CN"),
            getattr(summary_language_var, "_buttons", {}),
        )
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

    def _infer_expanded_size(self) -> Tuple[int, int]:
        first_frame = self.ui_root / SEQUENCE_EXPAND / "合成 1_00000.png"
        if not first_frame.exists():
            return BASE_EXPANDED_SIZE
        with Image.open(first_frame) as image:
            width, height = image.size
        if width <= 0 or height <= 0:
            return BASE_EXPANDED_SIZE
        target_height = TARGET_EXPANDED_HEIGHT
        target_width = round(width * target_height / height)
        return (target_width, target_height)

    def _scale_button_centers(self) -> Tuple[Tuple[int, int], ...]:
        scale_x = self.expanded_size[0] / BASE_EXPANDED_SIZE[0]
        scale_y = self.expanded_size[1] / BASE_EXPANDED_SIZE[1]
        return tuple(
            (round(x * scale_x), round(y * scale_y))
            for x, y in BASE_BUTTON_CENTERS
        )

    def _scale_button_radius(self) -> int:
        scale = self.expanded_size[1] / BASE_EXPANDED_SIZE[1]
        return max(18, round(BASE_BUTTON_RADIUS * scale))

    def _infer_loading_size(self) -> Tuple[int, int]:
        gear_path = self.ui_root / ICON_EXPAND_SETTINGS
        if gear_path.exists():
            with Image.open(gear_path) as image:
                return image.size
        return (31, 31)
