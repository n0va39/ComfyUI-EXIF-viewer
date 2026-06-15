from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageGrab, ImageOps, ImageTk
except ImportError:
    Image = None
    ImageGrab = None
    ImageOps = None
    ImageTk = None

from comfy_metadata_reader import (
    MetadataResult,
    extract_civitai_resources,
    extract_sections,
    format_report,
    read_metadata,
)
from comfy_workflow_prompts import decode_delimiter, infer_workflow_prompts
from comfy_workflow_prompts import describe_workflow_node

try:
    from tkinterdnd2 import DND_FILES, DND_TEXT, TkinterDnD
except ImportError:
    DND_FILES = None
    DND_TEXT = None
    TkinterDnD = None


SUPPORTED_FILETYPES = (
    ("Image files", "*.png *.webp *.jpg *.jpeg"),
    ("PNG files", "*.png"),
    ("WEBP files", "*.webp"),
    ("JPEG files", "*.jpg *.jpeg"),
    ("All files", "*.*"),
)

SUPPORTED_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg"}
MAX_DROP_DOWNLOAD_BYTES = 100 * 1024 * 1024
DROP_DOWNLOAD_CHUNK_BYTES = 256 * 1024
ALLOWED_DROP_URL_HOSTS = {"ac-o.namu.la"}
APP_DIR_NAME = "ComfyUI-EXIF-viewer"
RECENT_CACHE_FILENAME = "recent_images.json"
MAX_RECENT_IMAGES = 8
PREVIEW_PANEL_MIN_WIDTH = 760
RECENT_PANEL_MIN_WIDTH = 220
LEFT_REGION_MIN_WIDTH = PREVIEW_PANEL_MIN_WIDTH + RECENT_PANEL_MIN_WIDTH + 18
RIGHT_REGION_MIN_WIDTH = 640
TAB_MIN_CHARS = 10
LEFT_CONTROLS_HEIGHT = 360


def enable_high_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


BaseTk = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk


class MetadataViewer(BaseTk):
    def __init__(self, initial_path: str | None = None) -> None:
        super().__init__()
        self.title("ComfyUI EXIF Viewer")
        self.geometry("1800x980")
        self.minsize(1560, 780)
        self._configure_dpi_scaling()
        self._configure_fonts()

        self.path_var = tk.StringVar(value="No file loaded")
        self.status_var = tk.StringVar(value="Open or drop an image file.")
        self.platform_var = tk.StringVar(value="Platform: -")
        self.infer_enabled_var = tk.BooleanVar(value=False)
        self.infer_mode_var = tk.StringVar(value="Auto CLIP")
        self.infer_positive_nodes_var = tk.StringVar(value="")
        self.infer_negative_nodes_var = tk.StringVar(value="")
        self.infer_delimiter_var = tk.StringVar(value="\\n")
        self.node_lookup_var = tk.StringVar(value="")
        self.download_progress_var = tk.DoubleVar(value=0.0)
        self.download_progress_text_var = tk.StringVar(value="")
        self.show_recent_var = tk.BooleanVar(value=True)
        self.text_widgets: dict[str, tk.Text] = {}
        self.current_result: MetadataResult | None = None
        self.current_image_path: Path | None = None
        self.recent_paths: list[Path] = _load_recent_paths()
        self.recent_expanded = True
        self.content_pane: tk.PanedWindow | None = None
        self.drop_queue: list[str] = []
        self.processing_drop_queue = False
        self.preview_photo: object | None = None
        self.preview_after_id: str | None = None
        self.download_thread: threading.Thread | None = None
        self.download_progress_indeterminate = False

        self._build_ui()
        self._refresh_recent_images()
        self._setup_paste_shortcuts()
        self._setup_drag_and_drop()
        if initial_path:
            self.open_path(initial_path)

    def _configure_dpi_scaling(self) -> None:
        try:
            pixels_per_inch = self.winfo_fpixels("1i")
            if pixels_per_inch > 0:
                self.tk.call("tk", "scaling", pixels_per_inch / 72.0)
        except tk.TclError:
            pass

    def _configure_fonts(self) -> None:
        self.color_bg = "#f4f6f8"
        self.color_panel = "#ffffff"
        self.color_text = "#1f2933"
        self.color_accent = "#2f7d57"
        self.tone_border = _mix_hex(self.color_bg, self.color_text, 0.32)
        self.tone_soft = _mix_hex(self.color_bg, self.color_text, 0.06)
        self.tone_selected = _mix_hex(self.color_panel, self.color_accent, 0.18)
        self.tone_muted = _mix_hex(self.color_text, self.color_bg, 0.32)
        self.tone_progress = _mix_hex(self.color_panel, self.color_accent, 0.14)

        self.ui_font_family = self._choose_font_family(
            ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Malgun Gothic")
        )
        self.mono_font_family = self._choose_font_family(
            ("Cascadia Mono", "Cascadia Code", "Consolas", "Courier New")
        )

        fonts = {
            "TkDefaultFont": (self.ui_font_family, 10),
            "TkTextFont": (self.ui_font_family, 10),
            "TkMenuFont": (self.ui_font_family, 10),
            "TkHeadingFont": (self.ui_font_family, 10, "bold"),
            "TkCaptionFont": (self.ui_font_family, 9),
            "TkSmallCaptionFont": (self.ui_font_family, 9),
            "TkIconFont": (self.ui_font_family, 10),
            "TkTooltipFont": (self.ui_font_family, 9),
        }
        for font_name, config in fonts.items():
            try:
                tkfont.nametofont(font_name).configure(family=config[0], size=config[1])
                if len(config) > 2:
                    tkfont.nametofont(font_name).configure(weight=config[2])
            except tk.TclError:
                pass

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=(self.ui_font_family, 10))
        style.configure(".", background=self.color_bg, foreground=self.color_text)
        style.configure("TFrame", background=self.color_bg)
        style.configure("Panel.TFrame", background=self.color_panel)
        style.configure(
            "TButton",
            font=(self.ui_font_family, 10),
            padding=(11, 7),
            background=self.color_panel,
            foreground=self.color_text,
            bordercolor=self.tone_border,
            lightcolor=self.color_panel,
            darkcolor=self.tone_border,
            focusthickness=1,
            focuscolor=self.tone_border,
            relief=tk.SOLID,
            borderwidth=1,
        )
        style.map(
            "TButton",
            background=[("active", self.tone_soft), ("pressed", self.tone_selected)],
            bordercolor=[("active", self.color_accent), ("pressed", self.color_accent)],
            foreground=[("disabled", self.tone_muted)],
        )
        style.configure(
            "TCheckbutton",
            font=(self.ui_font_family, 10),
            padding=(2, 4),
            background=self.color_bg,
            foreground=self.color_text,
        )
        style.configure(
            "TEntry",
            font=(self.ui_font_family, 10),
            padding=(6, 4),
            fieldbackground=self.color_panel,
            bordercolor=self.tone_border,
            lightcolor=self.color_panel,
            darkcolor=self.tone_border,
            borderwidth=1,
        )
        style.configure(
            "TCombobox",
            font=(self.ui_font_family, 10),
            padding=(6, 4),
            fieldbackground=self.color_panel,
            background=self.color_panel,
            bordercolor=self.tone_border,
            arrowcolor=self.color_text,
            borderwidth=1,
        )
        style.configure("TLabel", font=(self.ui_font_family, 10), background=self.color_bg)
        style.configure(
            "Panel.TLabel",
            font=(self.ui_font_family, 10),
            background=self.color_panel,
            foreground=self.color_text,
        )
        style.configure(
            "Muted.Panel.TLabel",
            font=(self.ui_font_family, 9),
            background=self.color_panel,
            foreground=self.tone_muted,
        )
        style.configure(
            "TLabelframe",
            background=self.color_bg,
            bordercolor=self.tone_border,
            lightcolor=self.color_bg,
            darkcolor=self.tone_border,
            relief=tk.SOLID,
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label",
            font=(self.ui_font_family, 10, "bold"),
            background=self.color_bg,
            foreground=self.color_text,
        )
        style.layout(
            "Fixed.TNotebook.Tab",
            [
                (
                    "Notebook.tab",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "Notebook.padding",
                                {
                                    "side": "top",
                                    "sticky": "nswe",
                                    "children": [
                                        (
                                            "Notebook.label",
                                            {"side": "top", "sticky": "nswe"},
                                        )
                                    ],
                                },
                            )
                        ],
                    },
                )
            ],
        )
        style.configure(
            "Fixed.TNotebook",
            background=self.color_bg,
            bordercolor=self.tone_border,
            borderwidth=1,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "Fixed.TNotebook.Tab",
            font=(self.ui_font_family, 9),
            padding=(4, 8),
            width=TAB_MIN_CHARS,
            anchor=tk.CENTER,
            background=self.color_panel,
            foreground=self.tone_muted,
            bordercolor=self.tone_border,
            lightcolor=self.color_panel,
            darkcolor=self.tone_border,
            borderwidth=1,
        )
        style.map(
            "Fixed.TNotebook.Tab",
            background=[("selected", self.tone_selected), ("active", self.tone_soft)],
            foreground=[("selected", self.color_text)],
            bordercolor=[("selected", self.color_accent), ("active", self.tone_border)],
            padding=[
                ("selected", (4, 8)),
                ("active", (4, 8)),
                ("!selected", (4, 8)),
            ],
            borderwidth=[
                ("selected", 1),
                ("active", 1),
                ("!selected", 1),
            ],
            expand=[
                ("selected", (0, 0, 0, 0)),
                ("active", (0, 0, 0, 0)),
                ("!selected", (0, 0, 0, 0)),
            ],
        )
        style.configure(
            "Green.Horizontal.TProgressbar",
            troughcolor=self.tone_progress,
            background=self.color_accent,
            bordercolor=self.tone_progress,
            lightcolor=self.color_accent,
            darkcolor=self.color_accent,
            thickness=3,
        )
        self.option_add("*TCombobox*Listbox.font", (self.ui_font_family, 10))
        self.configure(bg=self.color_bg)

    def _choose_font_family(self, candidates: tuple[str, ...]) -> str:
        available = {name.lower(): name for name in tkfont.families(self)}
        for candidate in candidates:
            found = available.get(candidate.lower())
            if found:
                return found
        return candidates[-1]

    def _build_ui(self) -> None:
        self._build_menu()

        path_bar = ttk.Frame(self, padding=(14, 10, 14, 8))
        path_bar.pack(fill=tk.X)
        path_label = ttk.Label(path_bar, textvariable=self.path_var, anchor=tk.W)
        path_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        content = tk.PanedWindow(
            self,
            orient=tk.HORIZONTAL,
            bg=self.color_bg,
            bd=0,
            sashwidth=7,
            sashrelief=tk.RAISED,
            opaqueresize=True,
        )
        self.content_pane = content
        content.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))

        self.left_region = ttk.Frame(content, width=LEFT_REGION_MIN_WIDTH)
        self.left_region.rowconfigure(0, weight=1)
        self.left_region.columnconfigure(0, weight=0, minsize=RECENT_PANEL_MIN_WIDTH)
        self.left_region.columnconfigure(1, weight=1, minsize=PREVIEW_PANEL_MIN_WIDTH)

        self._build_recent_images(self.left_region)

        left_panel = ttk.Frame(
            self.left_region,
            padding=(10, 0, 10, 10),
            width=PREVIEW_PANEL_MIN_WIDTH,
        )
        left_panel.grid(row=0, column=1, sticky="nsew")
        left_panel.columnconfigure(0, weight=1)
        left_panel.rowconfigure(0, weight=1)
        left_panel.rowconfigure(1, weight=0, minsize=LEFT_CONTROLS_HEIGHT)

        self.drop_frame = tk.Frame(
            left_panel,
            bg=self.color_panel,
            bd=1,
            relief=tk.SOLID,
            takefocus=1,
            highlightthickness=1,
            highlightbackground=self.tone_border,
        )
        self.drop_frame.grid(row=0, column=0, sticky="nsew")
        self.drop_frame.columnconfigure(0, weight=1)
        self.drop_frame.rowconfigure(0, weight=1)
        self.drop_frame.rowconfigure(1, weight=0)

        self.drop_label = tk.Label(
            self.drop_frame,
            text="Drop or paste image here\nCtrl+V link/image\nor click Open",
            bg=self.color_panel,
            fg=self.color_text,
            font=(self.ui_font_family, 12, "bold"),
            justify=tk.CENTER,
            wraplength=280,
        )
        self.drop_label.grid(row=0, column=0, sticky="nsew", padx=20, pady=(20, 10))
        self.progress_frame = ttk.Frame(self.drop_frame, style="Panel.TFrame")
        self.progress_frame.grid(row=1, column=0, sticky="ew", padx=1, pady=(0, 1))
        self.progress_frame.columnconfigure(0, weight=1)
        self.download_progress = ttk.Progressbar(
            self.progress_frame,
            variable=self.download_progress_var,
            maximum=100.0,
            mode="determinate",
            style="Green.Horizontal.TProgressbar",
        )
        self.download_progress.grid(row=0, column=0, sticky="ew")
        self.progress_frame.grid_remove()
        self.drop_frame.bind("<Configure>", self._schedule_preview_refresh)

        controls_frame = self._build_left_controls(left_panel)
        ttk.Label(controls_frame, textvariable=self.platform_var).grid(
            row=0, column=0, sticky="ew", pady=(0, 0)
        )
        ttk.Label(
            controls_frame,
            textvariable=self.status_var,
            wraplength=PREVIEW_PANEL_MIN_WIDTH - 40,
        ).grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self._build_inference_controls(controls_frame)

        right_panel = ttk.Frame(content, width=RIGHT_REGION_MIN_WIDTH)
        right_panel.rowconfigure(0, weight=1)
        right_panel.columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(right_panel, style="Fixed.TNotebook")
        self.notebook.grid(row=0, column=0, sticky="nsew")

        content.add(self.left_region, minsize=LEFT_REGION_MIN_WIDTH, stretch="never")
        content.add(right_panel, minsize=RIGHT_REGION_MIN_WIDTH, stretch="always")

        for name in (
            "Summary",
            "Prompt",
            "Negative",
            "Settings",
            "Resources",
            "Guess",
            "Workflow",
            "Raw",
        ):
            self._add_text_tab(name)

    def _build_left_controls(self, parent: ttk.Frame) -> ttk.Frame:
        shell = ttk.Frame(parent, height=LEFT_CONTROLS_HEIGHT)
        shell.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        shell.grid_propagate(False)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            shell,
            highlightthickness=0,
            bg=self.color_bg,
            bd=0,
        )
        scrollbar = ttk.Scrollbar(shell, orient=tk.VERTICAL, command=canvas.yview)
        controls = ttk.Frame(canvas)
        controls.columnconfigure(0, weight=1)
        window_id = canvas.create_window((0, 0), window=controls, anchor="nw")

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        def sync_scroll_region(_event: object | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_width(event: object) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        controls.bind("<Configure>", sync_scroll_region)
        canvas.bind("<Configure>", sync_width)
        return controls

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Open...", command=self.open_dialog)
        file_menu.add_command(label="Copy Current Tab", command=self.copy_current_tab)
        file_menu.add_command(label="Save Current Tab...", command=self.save_current_tab)
        file_menu.add_separator()
        file_menu.add_command(label="Clear Cache", command=self.clear_cache)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_checkbutton(
            label="Recent Images",
            variable=self.show_recent_var,
            command=self._apply_recent_visibility,
        )
        menubar.add_cascade(label="View", menu=view_menu)
        self.configure(menu=menubar)

    def _build_recent_images(self, parent: ttk.Frame) -> None:
        self.recent_frame = ttk.Frame(parent, padding=(0, 0, 8, 10))
        self.recent_frame.grid(row=0, column=0, sticky="nsw")
        self.recent_frame.columnconfigure(0, weight=1)
        self.recent_frame.rowconfigure(0, weight=1)

        self.recent_body = ttk.Frame(self.recent_frame)
        self.recent_body.grid(row=0, column=0, sticky="nsew")
        self.recent_body.columnconfigure(0, weight=1)
        self.recent_body.rowconfigure(1, weight=1)

        ttk.Label(self.recent_body, text="Recent images").grid(
            row=0, column=0, sticky="ew", pady=(0, 6)
        )

        self.recent_listbox = tk.Listbox(
            self.recent_body,
            width=26,
            height=18,
            activestyle="dotbox",
            font=(self.ui_font_family, 9),
            exportselection=False,
            bd=1,
            relief=tk.SOLID,
            highlightthickness=1,
            highlightbackground=self.tone_border,
            bg=self.color_panel,
            fg=self.color_text,
            selectbackground=self.tone_selected,
            selectforeground=self.color_text,
        )
        self.recent_listbox.grid(row=1, column=0, sticky="nsew")
        self.recent_listbox.bind("<Double-Button-1>", self._open_selected_recent_image)
        self.recent_listbox.bind("<Return>", self._open_selected_recent_image)

    def _build_inference_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Workflow prompt guess", padding=10)
        frame.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        frame.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            frame,
            text="Enable",
            variable=self.infer_enabled_var,
            command=self.refresh_current_result,
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))

        mode_box = ttk.Combobox(
            frame,
            textvariable=self.infer_mode_var,
            values=("Auto CLIP", "Manual nodes"),
            state="readonly",
            width=16,
        )
        mode_box.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=(0, 2))
        mode_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh_current_result())

        ttk.Label(frame, text="Positive IDs").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.infer_positive_nodes_var).grid(
            row=1, column=1, sticky="ew", padx=(10, 0), pady=(8, 0)
        )

        ttk.Label(frame, text="Negative IDs").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(frame, textvariable=self.infer_negative_nodes_var).grid(
            row=2, column=1, sticky="ew", padx=(10, 0), pady=(6, 0)
        )

        ttk.Label(frame, text="Concat").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(frame, textvariable=self.infer_delimiter_var, width=10).grid(
            row=3, column=1, sticky="w", padx=(10, 0), pady=(6, 0)
        )

        ttk.Button(frame, text="Apply", command=self.refresh_current_result).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )

        ttk.Label(frame, text="Node ID").grid(row=5, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frame, textvariable=self.node_lookup_var).grid(
            row=5, column=1, sticky="ew", padx=(10, 0), pady=(10, 0)
        )
        ttk.Button(frame, text="Lookup Node", command=self.lookup_node).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

    def _add_text_tab(self, name: str) -> None:
        frame = ttk.Frame(self.notebook)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        text = tk.Text(
            frame,
            wrap=tk.WORD,
            undo=False,
            font=(self.mono_font_family, 10),
            bg=self.color_panel,
            fg=self.color_text,
            insertbackground=self.color_text,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.tone_border,
            padx=12,
            pady=12,
            spacing1=3,
            spacing3=5,
        )
        text.grid(row=0, column=0, sticky="nsew")
        text.configure(state=tk.DISABLED)

        yscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=yscroll.set)

        self.notebook.add(frame, text=name)
        self.text_widgets[name] = text

    def open_dialog(self) -> None:
        selected = filedialog.askopenfilename(
            title="Open image metadata", filetypes=SUPPORTED_FILETYPES
        )
        if selected:
            self.open_path(selected)

    def open_path(self, path: str | Path) -> None:
        try:
            result = read_metadata(path)
        except Exception as exc:
            messagebox.showerror("Read failed", str(exc))
            return

        self.current_result = result
        self.path_var.set(str(result.path))
        self.current_image_path = result.path
        self._render_preview(result.path, result.format_name)
        self._render_result(result)
        self._add_recent_path(result.path)

    def _render_result(self, result: MetadataResult) -> None:
        sections = extract_sections(result)
        display_prompt = sections.prompt
        display_negative = sections.negative_prompt
        guess_report = self._workflow_guess_report(result, sections)
        guess = getattr(self, "_last_prompt_guess", None)
        if self.infer_enabled_var.get() and not sections.raw_parameters and guess is not None:
            if guess.positive:
                display_prompt = guess.positive
            if guess.negative:
                display_negative = guess.negative

        self.platform_var.set(f"Platform: {sections.platform}")
        self.status_var.set(f"{result.format_name}, {len(result.entries)} metadata entries")

        summary = [
            f"File: {result.path}",
            f"Format: {result.format_name}",
            f"Platform: {sections.platform}",
            f"Entries: {len(result.entries)}",
            "",
            f"parameters: {'yes' if sections.raw_parameters else 'no'}",
            f"prompt: {'yes' if sections.prompt else 'no'}",
            f"negative: {'yes' if sections.negative_prompt else 'no'}",
            f"workflow: {'yes' if sections.workflow else 'no'}",
        ]
        if result.warnings:
            summary.extend(["", "Warnings:", *result.warnings])

        self._set_text("Summary", "\n".join(summary))
        self._set_text("Prompt", display_prompt)
        self._set_text("Negative", display_negative)
        self._set_text("Settings", sections.settings)
        self._set_resources(result)
        self._set_text("Guess", guess_report)
        self._set_text("Workflow", sections.workflow)
        self._set_text("Raw", format_report(result))

    def _workflow_guess_report(self, result: MetadataResult, sections: object) -> str:
        self._last_prompt_guess = None
        if not self.infer_enabled_var.get():
            return "Workflow prompt guess is disabled."

        workflow_json = result.first_value("workflow")
        prompt_json = result.first_value("prompt")
        if not workflow_json and not prompt_json:
            return "No ComfyUI workflow/prompt JSON found."

        if getattr(sections, "raw_parameters", ""):
            return (
                "Skipped.\n\n"
                "A1111/WebUI parameters are already stored in the image, so workflow "
                "prompt guessing was not applied."
            )

        mode = "manual" if self.infer_mode_var.get() == "Manual nodes" else "auto"
        delimiter = decode_delimiter(self.infer_delimiter_var.get())
        guess = infer_workflow_prompts(
            workflow_json=workflow_json,
            prompt_json=prompt_json,
            mode=mode,
            positive_node_ids=self.infer_positive_nodes_var.get(),
            negative_node_ids=self.infer_negative_nodes_var.get(),
            delimiter=delimiter,
        )
        self._last_prompt_guess = guess

        lines = [guess.details]
        if guess.positive:
            lines.extend(["", "[positive]", guess.positive])
        if guess.negative:
            lines.extend(["", "[negative]", guess.negative])
        if guess.warnings:
            lines.extend(["", "[warnings]", *guess.warnings])
        return "\n".join(lines).strip()

    def _set_text(self, tab_name: str, value: str) -> None:
        text = self.text_widgets[tab_name]
        text.configure(state=tk.NORMAL)
        text.delete("1.0", tk.END)
        text.insert("1.0", value or "")
        text.configure(state=tk.DISABLED)

    def _set_resources(self, result: MetadataResult) -> None:
        text = self.text_widgets["Resources"]
        text.configure(state=tk.NORMAL)
        text.delete("1.0", tk.END)
        text.tag_configure("link", foreground=self.color_accent, underline=True)
        text.tag_bind("link", "<Enter>", lambda _event: text.configure(cursor="hand2"))
        text.tag_bind("link", "<Leave>", lambda _event: text.configure(cursor=""))

        resources = extract_civitai_resources(result)
        if not resources:
            text.insert(
                "1.0",
                "No confirmed Civitai resources found.\n\n"
                "This tab only uses explicitly stored Civitai resources metadata.",
            )
            text.configure(state=tk.DISABLED)
            return

        text.insert(tk.END, "Confirmed Civitai resources\n\n")
        for index, resource in enumerate(resources, start=1):
            text.insert(tk.END, f"{index}. ")
            label = resource.name
            if resource.version:
                label = f"{label} - {resource.version}"
            text.insert(tk.END, label, ("link", f"resource_{index}"))
            text.tag_bind(
                f"resource_{index}",
                "<Button-1>",
                lambda _event, url=resource.url: webbrowser.open(url),
            )

            details = []
            if resource.weight:
                details.append(f"weight: {resource.weight}")
            details.append(f"air: {resource.air}")
            text.insert(tk.END, "\n   " + "\n   ".join(details) + "\n")

        text.configure(state=tk.DISABLED)

    def _current_text_widget(self) -> tk.Text:
        selected_id = self.notebook.select()
        tab_name = self.notebook.tab(selected_id, "text")
        return self.text_widgets[tab_name]

    def copy_current_tab(self) -> None:
        text = self._current_text_widget().get("1.0", tk.END).strip()
        self.clipboard_clear()
        self.clipboard_append(text)

    def save_current_tab(self) -> None:
        text = self._current_text_widget().get("1.0", tk.END).rstrip()
        if not text:
            return
        selected = filedialog.asksaveasfilename(
            title="Save metadata text",
            defaultextension=".txt",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        )
        if not selected:
            return
        Path(selected).write_text(text + "\n", encoding="utf-8")

    def clear_cache(self) -> None:
        if self.download_thread is not None and self.download_thread.is_alive():
            messagebox.showinfo(
                "Clear cache",
                "Wait for the current image download to finish before clearing cache.",
            )
            return

        if not messagebox.askyesno(
            "Clear cache",
            "Clear recent image history and downloaded image cache?\n\n"
            "Local image originals outside the app cache will not be deleted.",
        ):
            return

        deleted_count = _clear_app_cache_files()
        self.recent_paths = []
        _save_recent_paths(self.recent_paths)
        self._refresh_recent_images()
        self.status_var.set(f"Cache cleared. Deleted {deleted_count} cached files.")

    def refresh_current_result(self) -> None:
        if self.current_result is not None:
            self._render_result(self.current_result)

    def lookup_node(self) -> None:
        if self.current_result is None:
            return
        workflow_json = self.current_result.first_value("workflow")
        prompt_json = self.current_result.first_value("prompt")
        report = describe_workflow_node(
            workflow_json,
            prompt_json,
            self.node_lookup_var.get(),
        )
        self._set_text("Guess", report)
        self.notebook.select(self.text_widgets["Guess"].master)

    def _add_recent_path(self, path: Path) -> None:
        resolved = Path(path)
        self.recent_paths = [item for item in self.recent_paths if item != resolved]
        self.recent_paths.insert(0, resolved)
        del self.recent_paths[MAX_RECENT_IMAGES:]
        _save_recent_paths(self.recent_paths)
        self._refresh_recent_images()

    def _refresh_recent_images(self) -> None:
        if not hasattr(self, "recent_listbox"):
            return
        self.recent_listbox.delete(0, tk.END)
        for path in self.recent_paths:
            self.recent_listbox.insert(tk.END, path.name)

    def _open_selected_recent_image(self, _event: object | None = None) -> str:
        if not hasattr(self, "recent_listbox"):
            return "break"
        selection = self.recent_listbox.curselection()
        if not selection:
            return "break"
        index = int(selection[0])
        if index < len(self.recent_paths):
            path = self.recent_paths[index]
            if not path.exists():
                self.status_var.set(f"Recent image is missing: {path.name}")
                self.recent_paths.pop(index)
                _save_recent_paths(self.recent_paths)
                self._refresh_recent_images()
                return "break"
            self.open_path(path)
        return "break"

    def _apply_recent_visibility(self) -> None:
        self.recent_expanded = self.show_recent_var.get()
        if not hasattr(self, "recent_frame"):
            return
        if self.recent_expanded:
            self.left_region.columnconfigure(0, minsize=RECENT_PANEL_MIN_WIDTH)
            self.recent_frame.grid()
            if self.content_pane is not None:
                self.content_pane.paneconfigure(
                    self.left_region,
                    minsize=LEFT_REGION_MIN_WIDTH,
                )
        else:
            self.recent_frame.grid_remove()
            self.left_region.columnconfigure(0, minsize=0)
            if self.content_pane is not None:
                self.content_pane.paneconfigure(
                    self.left_region,
                    minsize=PREVIEW_PANEL_MIN_WIDTH,
                )

    def _setup_paste_shortcuts(self) -> None:
        self.drop_frame.bind("<Button-1>", self._focus_drop_area, add="+")
        self.drop_label.bind("<Button-1>", self._focus_drop_area, add="+")
        for sequence in ("<Control-v>", "<Control-V>"):
            self.drop_frame.bind(sequence, self._handle_paste)
            self.drop_label.bind(sequence, self._handle_paste)

    def _focus_drop_area(self, _event: object | None = None) -> None:
        self.drop_frame.focus_set()

    def _handle_paste(self, _event: object | None = None) -> str:
        if self._open_clipboard_image():
            return "break"

        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            text = ""

        if text:
            self._open_drop_items(self._parse_drop_items(text))
        else:
            messagebox.showinfo(
                "Paste image",
                "Clipboard does not contain a supported image, file path, or URL.",
            )
        return "break"

    def _open_clipboard_image(self) -> bool:
        if ImageGrab is None:
            return False
        try:
            clipboard_data = ImageGrab.grabclipboard()
        except Exception:
            return False

        if isinstance(clipboard_data, list):
            items = [str(item) for item in clipboard_data]
            return self._open_drop_items(items)

        if Image is not None and isinstance(clipboard_data, Image.Image):
            path = self._save_clipboard_image(clipboard_data)
            self.open_path(path)
            return True
        return False

    def _save_clipboard_image(self, image: object) -> Path:
        cache_dir = _app_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / "clipboard-image.png"
        if hasattr(image, "save"):
            image.save(path, format="PNG")
        return path

    def _setup_drag_and_drop(self) -> None:
        if DND_FILES is None or not hasattr(self, "drop_target_register"):
            self.status_var.set(
                "Drag and drop requires tkinterdnd2. Use Open or install requirements."
            )
            return

        dnd_types = tuple(value for value in (DND_FILES, DND_TEXT) if value)
        for widget in (self, self.drop_frame, self.drop_label):
            widget.drop_target_register(*dnd_types)
            widget.dnd_bind("<<Drop>>", self._handle_drop)

    def _handle_drop(self, event: object) -> None:
        data = getattr(event, "data", "")
        self._open_drop_items(self._parse_drop_items(data))

    def _open_drop_items(self, items: list[str]) -> bool:
        if not items:
            return False

        supported_items = self._supported_drop_items(items)
        if not supported_items:
            messagebox.showinfo(
                "Unsupported drop",
                "Drop a PNG, WEBP, JPG, JPEG file, or a direct image URL.",
            )
            return False

        self.drop_queue.extend(supported_items)
        if self.processing_drop_queue or (
            self.download_thread is not None and self.download_thread.is_alive()
        ):
            self.status_var.set(
                f"Queued {len(supported_items)} image(s). "
                f"{len(self.drop_queue)} waiting."
            )
            return True

        self._process_next_drop_queue_item()
        return True

    def _process_next_drop_queue_item(self) -> None:
        if self.download_thread is not None and self.download_thread.is_alive():
            return

        if not self.drop_queue:
            self.processing_drop_queue = False
            return

        self.processing_drop_queue = True
        item = self.drop_queue.pop(0)
        parsed = urllib.parse.urlparse(item)
        if parsed.scheme in {"http", "https"}:
            self.open_url(item, from_queue=True)
            return

        if parsed.scheme == "file":
            self.open_path(Path(urllib.request.url2pathname(parsed.path)))
        else:
            self.open_path(Path(item))

        self.after(0, self._process_next_drop_queue_item)

    def _parse_drop_items(self, data: str) -> list[str]:
        try:
            split_items = list(self.tk.splitlist(data))
        except tk.TclError:
            split_items = []

        if not split_items:
            split_items = data.replace("\r", "\n").split("\n")

        items = []
        for item in split_items:
            stripped = item.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.lower().startswith("url="):
                stripped = stripped.split("=", 1)[1].strip()
            items.append(stripped)
        return items

    def _supported_drop_items(self, items: list[str]) -> list[str]:
        return [item for item in items if _is_supported_drop_item(item)]

    def open_url(self, url: str, from_queue: bool = False) -> None:
        if not _is_allowed_drop_url(url):
            messagebox.showerror(
                "Unsupported URL",
                "Only ac-o.namu.la image URLs are allowed for security.",
            )
            return

        if self.download_thread is not None and self.download_thread.is_alive():
            if not from_queue:
                self.drop_queue.append(url)
            self.status_var.set(
                f"Queued dropped image URL. {len(self.drop_queue)} waiting."
            )
            return

        self.status_var.set(
            "Downloading dropped image URL..."
            + self._drop_queue_status_suffix()
        )
        self._show_download_progress()
        self.drop_frame.configure(cursor="watch")
        self.drop_label.configure(cursor="watch")
        self.download_thread = threading.Thread(
            target=self._download_image_url_worker,
            args=(url,),
            daemon=True,
        )
        self.download_thread.start()

    def _download_image_url_worker(self, url: str) -> None:
        try:
            path = self._download_image_url(url)
        except Exception as exc:
            error = str(exc)
            self.after(0, lambda error=error: self._finish_url_download(None, error))
            return
        self.after(0, lambda path=path: self._finish_url_download(path, ""))

    def _finish_url_download(self, path: Path | None, error: str) -> None:
        self.download_thread = None
        self.drop_frame.configure(cursor="")
        self.drop_label.configure(cursor="")
        self._hide_download_progress()
        if error:
            messagebox.showerror("Download failed", error)
            self.status_var.set(
                "Image URL download failed." + self._drop_queue_status_suffix()
            )
            if self.processing_drop_queue:
                self.after(0, self._process_next_drop_queue_item)
            return
        if path is not None:
            self.open_path(path)
        if self.processing_drop_queue:
            self.after(0, self._process_next_drop_queue_item)

    def _drop_queue_status_suffix(self) -> str:
        if not self.drop_queue:
            return ""
        return f" {len(self.drop_queue)} queued."

    def _download_image_url(self, url: str) -> Path:
        if not _is_allowed_drop_url(url):
            raise ValueError("Only ac-o.namu.la image URLs are allowed.")

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 ComfyUI-EXIF-viewer "
                    "(image metadata drag-and-drop)"
                )
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DROP_DOWNLOAD_BYTES:
                raise ValueError("Dropped image URL is too large.")

            suffix = _suffix_from_url_or_content_type(url, content_type)
            if suffix not in SUPPORTED_SUFFIXES:
                raise ValueError("Dropped URL does not look like a supported image.")

            total_bytes = int(content_length) if content_length else None
            chunks = []
            received_bytes = 0
            while True:
                chunk = response.read(DROP_DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                chunks.append(chunk)
                received_bytes += len(chunk)
                if received_bytes > MAX_DROP_DOWNLOAD_BYTES:
                    raise ValueError("Dropped image URL is too large.")
                self._queue_download_progress(received_bytes, total_bytes)
            data = b"".join(chunks)

        temp_dir = _app_cache_dir()
        temp_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        path = temp_dir / f"chrome-drop-{digest}{suffix}"
        path.write_bytes(data)
        return path

    def _show_download_progress(self) -> None:
        self.download_progress.stop()
        self.download_progress_indeterminate = False
        self.download_progress.configure(mode="determinate")
        self.download_progress_var.set(0.0)
        self.download_progress_text_var.set("0%")
        self.progress_frame.grid()

    def _hide_download_progress(self) -> None:
        self.download_progress.stop()
        self.download_progress_indeterminate = False
        self.download_progress_var.set(0.0)
        self.download_progress_text_var.set("")
        self.progress_frame.grid_remove()

    def _queue_download_progress(
        self, received_bytes: int, total_bytes: int | None
    ) -> None:
        self.after(
            0,
            lambda received=received_bytes, total=total_bytes: self._set_download_progress(
                received, total
            ),
        )

    def _set_download_progress(
        self, received_bytes: int, total_bytes: int | None
    ) -> None:
        if total_bytes:
            if self.download_progress_indeterminate:
                self.download_progress.stop()
                self.download_progress_indeterminate = False
            percent = min(100.0, received_bytes * 100.0 / total_bytes)
            self.download_progress.configure(mode="determinate")
            self.download_progress_var.set(percent)
            self.download_progress_text_var.set(f"{percent:.0f}%")
            self.status_var.set(
                "Downloading dropped image URL... "
                f"{percent:.0f}% ({_format_bytes(received_bytes)} / {_format_bytes(total_bytes)})"
            )
            return

        self.download_progress.configure(mode="indeterminate")
        if not self.download_progress_indeterminate:
            self.download_progress.start(12)
            self.download_progress_indeterminate = True
        self.download_progress_text_var.set(_format_bytes(received_bytes))
        self.status_var.set(
            f"Downloading dropped image URL... {_format_bytes(received_bytes)}"
        )

    def _schedule_preview_refresh(self, _event: object | None = None) -> None:
        if self.current_image_path is None:
            return
        if self.preview_after_id is not None:
            self.after_cancel(self.preview_after_id)
        self.preview_after_id = self.after(
            100, lambda: self._render_preview(self.current_image_path)
        )

    def _render_preview(self, path: Path, format_name: str | None = None) -> None:
        self.preview_after_id = None
        if Image is None or ImageOps is None or ImageTk is None:
            self.preview_photo = None
            self.drop_label.configure(
                image="",
                text=f"{path.name}\n\nPreview requires Pillow.",
                compound=tk.NONE,
            )
            return

        width = max(self.drop_frame.winfo_width() - 36, 180)
        height = max(self.drop_frame.winfo_height() - 86, 180)

        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((width, height), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
        except Exception as exc:
            self.preview_photo = None
            self.drop_label.configure(
                image="",
                text=f"{path.name}\n\nPreview failed:\n{exc}",
                compound=tk.NONE,
            )
            return

        self.preview_photo = photo
        label = path.name
        if format_name:
            label = f"{label}\n{format_name}"
        self.drop_label.configure(image=photo, text=label, compound=tk.TOP)


def _suffix_from_url_or_content_type(url: str, content_type: str) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in SUPPORTED_SUFFIXES:
        return suffix
    return {
        "image/png": ".png",
        "image/webp": ".webp",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
    }.get(content_type.lower(), "")


def _is_allowed_drop_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https"}:
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname in ALLOWED_DROP_URL_HOSTS


def _is_supported_drop_item(item: str) -> bool:
    parsed = urllib.parse.urlparse(item)
    if _is_allowed_drop_url(item):
        return True
    if parsed.scheme in {"http", "https"}:
        return False
    if parsed.scheme == "file":
        suffix = Path(urllib.request.url2pathname(parsed.path)).suffix.lower()
        return suffix in SUPPORTED_SUFFIXES
    return Path(item).suffix.lower() in SUPPORTED_SUFFIXES


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} MB"


def _app_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if root:
        return Path(root) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME}"


def _app_cache_dir() -> Path:
    return _app_data_dir() / "cache"


def _legacy_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / APP_DIR_NAME


def _recent_cache_path() -> Path:
    return _app_data_dir() / RECENT_CACHE_FILENAME


def _load_recent_paths() -> list[Path]:
    path = _recent_cache_path()
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if not isinstance(payload, list):
        return []

    paths: list[Path] = []
    for item in payload:
        if not isinstance(item, str):
            continue
        candidate = Path(item)
        if candidate.exists() and candidate not in paths:
            paths.append(candidate)
        if len(paths) >= MAX_RECENT_IMAGES:
            break
    return paths


def _save_recent_paths(paths: list[Path]) -> None:
    path = _recent_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [str(item) for item in paths[:MAX_RECENT_IMAGES]]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_app_cache_files(include_legacy: bool = True) -> int:
    deleted_count = 0
    directories = [_app_cache_dir()]
    if include_legacy:
        directories.append(_legacy_cache_dir())

    for directory in directories:
        if not directory.exists():
            continue
        for pattern in ("chrome-drop-*", "clipboard-image.png"):
            for path in directory.glob(pattern):
                if not path.is_file():
                    continue
                try:
                    path.unlink()
                    deleted_count += 1
                except OSError:
                    pass

    recent_path = _recent_cache_path()
    if recent_path.exists():
        try:
            recent_path.unlink()
        except OSError:
            pass
    return deleted_count


def _mix_hex(first: str, second: str, ratio: float) -> str:
    ratio = max(0.0, min(1.0, ratio))
    left = first.lstrip("#")
    right = second.lstrip("#")
    if len(left) != 6 or len(right) != 6:
        return first
    channels = []
    for index in range(0, 6, 2):
        left_value = int(left[index : index + 2], 16)
        right_value = int(right[index : index + 2], 16)
        mixed = round(left_value * (1.0 - ratio) + right_value * ratio)
        channels.append(f"{mixed:02x}")
    return "#" + "".join(channels)


def main() -> int:
    parser = argparse.ArgumentParser(description="ComfyUI EXIF metadata viewer.")
    parser.add_argument("image", nargs="?", help="Optional image path to open")
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Print metadata to the terminal instead of opening the GUI",
    )
    args = parser.parse_args()

    if args.dump:
        if not args.image:
            parser.error("--dump requires an image path")
        _force_utf8_stdout()
        print(format_report(read_metadata(args.image)))
        return 0

    enable_high_dpi_awareness()
    app = MetadataViewer(args.image)
    app.mainloop()
    return 0


def _force_utf8_stdout() -> None:
    try:
        import sys

        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
