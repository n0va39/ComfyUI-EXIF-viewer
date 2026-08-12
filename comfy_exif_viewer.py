from __future__ import annotations

import argparse
import ctypes
import hashlib
import ipaddress
import json
import os
import queue
import re
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

try:
    from PIL import Image, ImageGrab, ImageOps, ImageTk
except ImportError:
    Image = None
    ImageGrab = None
    ImageOps = None
    ImageTk = None

from comfy_metadata_reader import (
    MetadataEntry,
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
MAX_CIVITAI_PAGE_BYTES = 8 * 1024 * 1024
CIVITAI_PAGE_HOSTS = frozenset({"civitai.com", "civitai.red"})
CIVITAI_IMAGE_HOST = "image.civitai.com"
CIVITAI_DELIVERY_HOST = "image-b2.civitai.com"
DEFAULT_ALLOWED_DROP_URL_HOSTS = (
    "ac-o.namu.la",
    "civitai.com",
    "civitai.red",
    CIVITAI_IMAGE_HOST,
    CIVITAI_DELIVERY_HOST,
)
APP_DIR_NAME = "ComfyUI-EXIF-viewer"
RECENT_CACHE_FILENAME = "recent_images.json"
SETTINGS_FILENAME = "settings.json"
SETTINGS_SCHEMA_VERSION = 2
MAX_RECENT_IMAGES = 8
APP_VERSION = "0.1.5"
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


@dataclass(frozen=True)
class _CivitaiImageInfo:
    image_url: str
    metadata_entries: tuple[MetadataEntry, ...]


@dataclass(frozen=True)
class _DownloadedImage:
    path: Path
    supplemental_entries: tuple[MetadataEntry, ...] = ()


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
        self.allowed_url_hosts = _load_allowed_url_hosts()
        self.recent_expanded = True
        self.content_pane: tk.PanedWindow | None = None
        self.drop_queue: list[str] = []
        self.processing_drop_queue = False
        self.preview_photo: object | None = None
        self.preview_after_id: str | None = None
        self.download_thread: threading.Thread | None = None
        self.download_events: queue.Queue[tuple[object, ...]] = queue.Queue()
        self.download_poll_after_id: str | None = None
        self.download_progress_indeterminate = False
        self.navigation_generation = 0

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

        settings_menu = tk.Menu(menubar, tearoff=False)
        settings_menu.add_command(
            label="Allowed Image Sites...",
            command=self.edit_allowed_image_sites,
        )
        menubar.add_cascade(label="Settings", menu=settings_menu)
        self.configure(menu=menubar)

    def edit_allowed_image_sites(self) -> None:
        existing = getattr(self, "allowed_sites_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return

        window = tk.Toplevel(self)
        self.allowed_sites_window = window
        window.title("Allowed Image Sites")
        window.transient(self)
        window.resizable(True, True)
        window.minsize(520, 360)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        ttk.Label(
            window,
            text=(
                "Enter one HTTPS hostname per line. Use *.example.com to allow "
                "all of its subdomains. An empty list disables URL downloads."
            ),
            wraplength=490,
            justify=tk.LEFT,
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))

        hosts_text = tk.Text(
            window,
            wrap=tk.NONE,
            height=12,
            font=(self.mono_font_family, 10),
        )
        hosts_text.grid(row=1, column=0, sticky="nsew", padx=16)
        hosts_text.insert("1.0", "\n".join(sorted(self.allowed_url_hosts)))

        button_row = ttk.Frame(window)
        button_row.grid(row=2, column=0, sticky="e", padx=16, pady=16)

        def save() -> None:
            try:
                hosts = _parse_allowed_url_hosts(hosts_text.get("1.0", tk.END))
                _save_allowed_url_hosts(hosts)
            except ValueError as exc:
                messagebox.showerror("Invalid hostname", str(exc), parent=window)
                return
            except OSError as exc:
                messagebox.showerror("Settings save failed", str(exc), parent=window)
                return
            self.allowed_url_hosts = hosts
            self.status_var.set(
                f"Allowed image sites updated: {len(hosts)} host rule(s)."
            )
            window.destroy()

        ttk.Button(button_row, text="Cancel", command=window.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(button_row, text="Save", command=save).grid(row=0, column=1)

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

    def open_path(
        self,
        path: str | Path,
        *,
        from_queue: bool = False,
        supplemental_entries: tuple[MetadataEntry, ...] = (),
    ) -> None:
        try:
            result = read_metadata(path)
        except Exception as exc:
            messagebox.showerror("Read failed", str(exc))
            return

        if supplemental_entries:
            result.entries.extend(supplemental_entries)
        if not from_queue:
            self._supersede_drop_navigation()
        self.current_result = result
        self.path_var.set(str(result.path))
        self.current_image_path = result.path
        self._render_preview(result.path, result.format_name)
        self._render_result(result)
        self._add_recent_path(result.path)

    def _supersede_drop_navigation(self) -> None:
        self.navigation_generation += 1
        self.drop_queue.clear()
        self.processing_drop_queue = False
        if self.download_thread is not None:
            self.drop_frame.configure(cursor="")
            self.drop_label.configure(cursor="")
            self._hide_download_progress()

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
        if self.download_thread is not None:
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

        current_cache_path = (
            self.current_image_path
            if self.current_image_path is not None
            and _is_app_cache_path(self.current_image_path)
            else None
        )
        deleted_count = _clear_app_cache_files()
        if current_cache_path is not None and not current_cache_path.exists():
            self.current_image_path = None
        self.recent_paths = []
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
        self._persist_recent_paths()
        self._refresh_recent_images()

    def _persist_recent_paths(self) -> bool:
        try:
            _save_recent_paths(self.recent_paths)
        except OSError as exc:
            self.status_var.set(
                f"Recent image history could not be saved: {exc}"
            )
            return False
        return True

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
                self._persist_recent_paths()
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
        if self.processing_drop_queue or self.download_thread is not None:
            self.status_var.set(
                f"Queued {len(supported_items)} image(s). "
                f"{len(self.drop_queue)} waiting."
            )
            return True

        self.navigation_generation += 1
        self._process_next_drop_queue_item()
        return True

    def _process_next_drop_queue_item(self) -> None:
        if self.download_thread is not None:
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
            self.open_path(
                Path(urllib.request.url2pathname(parsed.path)), from_queue=True
            )
        else:
            self.open_path(Path(item), from_queue=True)

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
        return [
            item
            for item in items
            if _is_supported_drop_item(item, self.allowed_url_hosts)
        ]

    def open_url(self, url: str, from_queue: bool = False) -> None:
        allowed_hosts = frozenset(self.allowed_url_hosts)
        if not _is_allowed_drop_url(url, allowed_hosts):
            messagebox.showerror(
                "Unsupported URL",
                "This HTTPS hostname is not in Settings > Allowed Image Sites.",
            )
            if from_queue and self.processing_drop_queue:
                self.after(0, self._process_next_drop_queue_item)
            return

        if self.download_thread is not None:
            if not from_queue:
                self.drop_queue.append(url)
            self.status_var.set(
                f"Queued dropped image URL. {len(self.drop_queue)} waiting."
            )
            return

        if not from_queue:
            self._supersede_drop_navigation()

        self.status_var.set(
            "Downloading dropped image URL..."
            + self._drop_queue_status_suffix()
        )
        self._show_download_progress()
        self.drop_frame.configure(cursor="watch")
        self.drop_label.configure(cursor="watch")
        self.download_thread = threading.Thread(
            target=self._download_image_url_worker,
            args=(url, self.navigation_generation, allowed_hosts),
            daemon=True,
        )
        self.download_thread.start()
        self._schedule_download_event_poll()

    def _download_image_url_worker(
        self,
        url: str,
        generation: int,
        allowed_hosts: frozenset[str] | None = None,
    ) -> None:
        try:
            downloaded = self._download_image_url(url, generation, allowed_hosts)
        except Exception as exc:
            self.download_events.put(("finished", generation, None, str(exc)))
            return
        self.download_events.put(("finished", generation, downloaded, ""))

    def _finish_url_download(
        self,
        downloaded: _DownloadedImage | Path | None,
        error: str,
        generation: int,
    ) -> None:
        self.download_thread = None
        self.drop_frame.configure(cursor="")
        self.drop_label.configure(cursor="")
        self._hide_download_progress()
        if generation != self.navigation_generation:
            if self.drop_queue:
                self.after(0, self._process_next_drop_queue_item)
            else:
                self.processing_drop_queue = False
            return
        if error:
            messagebox.showerror("Download failed", error)
            self.status_var.set(
                "Image URL download failed." + self._drop_queue_status_suffix()
            )
            if self.processing_drop_queue:
                self.after(0, self._process_next_drop_queue_item)
            return
        if downloaded is not None:
            if isinstance(downloaded, Path):
                downloaded = _DownloadedImage(downloaded)
            self.open_path(
                downloaded.path,
                from_queue=True,
                supplemental_entries=downloaded.supplemental_entries,
            )
        if self.processing_drop_queue:
            self.after(0, self._process_next_drop_queue_item)

    def _drop_queue_status_suffix(self) -> str:
        if not self.drop_queue:
            return ""
        return f" {len(self.drop_queue)} queued."

    def _download_image_url(
        self,
        url: str,
        generation: int | None = None,
        allowed_hosts: frozenset[str] | None = None,
    ) -> _DownloadedImage:
        if allowed_hosts is None:
            allowed_hosts = frozenset(
                getattr(self, "allowed_url_hosts", DEFAULT_ALLOWED_DROP_URL_HOSTS)
            )
        if not _is_allowed_drop_url(url, allowed_hosts):
            raise ValueError("Image URL hostname is not allowed in settings.")

        source_url = url
        supplemental_entries: tuple[MetadataEntry, ...] = ()
        hostname = _url_hostname(url)
        if hostname in CIVITAI_PAGE_HOSTS:
            civitai_info = _resolve_civitai_image_page(url, allowed_hosts)
            url = civitai_info.image_url
            supplemental_entries = civitai_info.metadata_entries
            if not _is_allowed_drop_url(url, allowed_hosts):
                raise ValueError(
                    "Civitai image delivery requires image.civitai.com in "
                    "Settings > Allowed Image Sites."
                )
        elif hostname == CIVITAI_IMAGE_HOST:
            image_id = _civitai_asset_image_id(url)
            url = _civitai_original_asset_url(url)
            canonical_page = (
                f"https://civitai.com/images/{image_id}" if image_id else ""
            )
            if canonical_page and _is_allowed_drop_url(canonical_page, allowed_hosts):
                try:
                    civitai_info = _resolve_civitai_image_page(
                        canonical_page, allowed_hosts
                    )
                except (OSError, ValueError):
                    supplemental_entries = ()
                else:
                    source_asset = _civitai_asset_key(url)
                    resolved_asset = _civitai_asset_key(civitai_info.image_url)
                    if source_asset and source_asset == resolved_asset:
                        supplemental_entries = civitai_info.metadata_entries

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 ComfyUI-EXIF-viewer "
                    "(image metadata drag-and-drop)"
                )
            },
        )
        with _open_allowed_drop_url(request, allowed_hosts) as response:
            final_url = response.geturl()
            if not _is_allowed_drop_url(final_url, allowed_hosts):
                raise ValueError("Image URL redirected outside the allowed host.")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DROP_DOWNLOAD_BYTES:
                raise ValueError("Dropped image URL is too large.")

            suffix = _suffix_from_url_or_content_type(final_url, content_type)
            if suffix not in SUPPORTED_SUFFIXES:
                raise ValueError("Dropped URL does not look like a supported image.")

            total_bytes = int(content_length) if content_length else None
            temp_dir = _app_cache_dir()
            temp_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]
            path = temp_dir / f"chrome-drop-{digest}{suffix}"
            partial_path = path.with_name(path.name + ".part")
            received_bytes = 0
            try:
                with partial_path.open("wb") as output:
                    while True:
                        chunk = response.read(DROP_DOWNLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        received_bytes += len(chunk)
                        if received_bytes > MAX_DROP_DOWNLOAD_BYTES:
                            raise ValueError("Dropped image URL is too large.")
                        output.write(chunk)
                        self._queue_download_progress(
                            received_bytes, total_bytes, generation
                        )
                partial_path.replace(path)
            except Exception:
                partial_path.unlink(missing_ok=True)
                raise
        return _DownloadedImage(path, supplemental_entries)

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
        self,
        received_bytes: int,
        total_bytes: int | None,
        generation: int | None = None,
    ) -> None:
        if generation is None:
            generation = self.navigation_generation
        self.download_events.put(
            ("progress", generation, received_bytes, total_bytes)
        )

    def _schedule_download_event_poll(self) -> None:
        if self.download_poll_after_id is None:
            self.download_poll_after_id = self.after(50, self._poll_download_events)

    def _poll_download_events(self) -> None:
        self.download_poll_after_id = None
        while True:
            try:
                event = self.download_events.get_nowait()
            except queue.Empty:
                break
            event_type, generation, *payload = event
            if event_type == "progress":
                if generation == self.navigation_generation:
                    self._set_download_progress(payload[0], payload[1])
            elif event_type == "finished":
                self._finish_url_download(payload[0], payload[1], generation)

        if self.download_thread is not None:
            self._schedule_download_event_poll()

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
        self.preview_after_id = self.after(100, self._refresh_scheduled_preview)

    def _refresh_scheduled_preview(self) -> None:
        self.preview_after_id = None
        if self.current_image_path is not None:
            self._render_preview(self.current_image_path)

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


class _CivitaiScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture: str | None = None
        self._parts: list[str] = []
        self.json_ld_scripts: list[str] = []
        self.next_data_script = ""

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "script":
            return
        values = dict(attrs)
        if values.get("id") == "__NEXT_DATA__":
            self._capture = "next"
        elif values.get("type") == "application/ld+json":
            self._capture = "json-ld"
        else:
            return
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or self._capture is None:
            return
        value = "".join(self._parts)
        if self._capture == "next":
            self.next_data_script = value
        else:
            self.json_ld_scripts.append(value)
        self._capture = None
        self._parts = []


def _url_hostname(url: str) -> str:
    try:
        return _normalize_allowed_url_host(
            urllib.parse.urlparse(url).hostname or ""
        )
    except ValueError:
        return ""


def _civitai_image_page_id(url: str) -> int | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or _url_hostname(url) not in CIVITAI_PAGE_HOSTS:
        return None
    match = re.fullmatch(r"/images/(\d+)/?", parsed.path)
    return int(match.group(1)) if match else None


def _civitai_asset_image_id(url: str) -> int | None:
    if _url_hostname(url) != CIVITAI_IMAGE_HOST:
        return None
    stem = Path(urllib.parse.urlparse(url).path).stem
    return int(stem) if stem.isdigit() else None


def _civitai_asset_key(url: str) -> str:
    match = re.search(
        r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:/|$)",
        urllib.parse.urlparse(url).path,
        flags=re.IGNORECASE,
    )
    return match.group(0).strip("/").lower() if match else ""


def _civitai_original_asset_url(url: str) -> str:
    if _url_hostname(url) != CIVITAI_IMAGE_HOST:
        return url
    parsed = urllib.parse.urlparse(url)
    segments = parsed.path.split("/")
    asset_index = next(
        (
            index
            for index, segment in enumerate(segments)
            if re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                segment,
                flags=re.IGNORECASE,
            )
        ),
        None,
    )
    if asset_index is None or asset_index + 1 >= len(segments):
        return url
    filename = segments[-1]
    if not filename:
        return url
    original_path = "/".join(
        [*segments[: asset_index + 1], "original=true", filename]
    )
    return urllib.parse.urlunparse(parsed._replace(path=original_path))


def _read_limited_response(response: Any, maximum: int, label: str) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > maximum:
            raise ValueError(f"{label} is too large.")
    payload = response.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError(f"{label} is too large.")
    return payload


def _resolve_civitai_image_page(
    url: str,
    allowed_hosts: frozenset[str],
) -> _CivitaiImageInfo:
    image_id = _civitai_image_page_id(url)
    if image_id is None:
        raise ValueError("Civitai URLs must use /images/<numeric-id>.")

    canonical_url = f"https://civitai.com/images/{image_id}"
    if not _is_allowed_drop_url(canonical_url, allowed_hosts):
        raise ValueError(
            "Resolving civitai.red links requires civitai.com in "
            "Settings > Allowed Image Sites."
        )
    request = urllib.request.Request(
        canonical_url,
        headers={
            "User-Agent": "Mozilla/5.0 ComfyUI-EXIF-viewer (image metadata)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with _open_allowed_drop_url(request, allowed_hosts) as response:
        final_url = response.geturl()
        if _civitai_image_page_id(final_url) != image_id:
            raise ValueError("Civitai image page redirected unexpectedly.")
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type:
            raise ValueError("Civitai image page did not return HTML.")
        source = _read_limited_response(
            response, MAX_CIVITAI_PAGE_BYTES, "Civitai image page"
        ).decode("utf-8", errors="replace")
    return _parse_civitai_image_page(source, image_id)


def _parse_civitai_image_page(source: str, image_id: int) -> _CivitaiImageInfo:
    parser = _CivitaiScriptParser()
    parser.feed(source)

    image_url = ""
    for script in parser.json_ld_scripts:
        try:
            payload = json.loads(script)
        except json.JSONDecodeError:
            continue
        image_object = _find_civitai_image_object(payload)
        if image_object is None:
            continue
        candidate = str(image_object.get("contentUrl") or "")
        if (
            urllib.parse.urlparse(candidate).scheme == "https"
            and _url_hostname(candidate) == CIVITAI_IMAGE_HOST
        ):
            image_url = candidate
            break
    if not image_url:
        raise ValueError("Civitai page did not expose an original image URL.")

    metadata: dict[str, Any] = {}
    if parser.next_data_script:
        try:
            next_data = json.loads(parser.next_data_script)
        except json.JSONDecodeError:
            next_data = {}
        metadata = _find_civitai_page_metadata(next_data, image_id)

    return _CivitaiImageInfo(
        image_url=image_url,
        metadata_entries=_civitai_metadata_entries(metadata),
    )


def _find_civitai_image_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("@type") == "ImageObject" and value.get("contentUrl"):
            return value
        for item in value.values():
            result = _find_civitai_image_object(item)
            if result is not None:
                return result
    elif isinstance(value, list):
        for item in value:
            result = _find_civitai_image_object(item)
            if result is not None:
                return result
    return None


def _find_civitai_page_metadata(value: Any, image_id: int) -> dict[str, Any]:
    fallback: dict[str, Any] = {}

    def visit(item: Any) -> dict[str, Any] | None:
        nonlocal fallback
        if isinstance(item, dict):
            state = item.get("state")
            data = state.get("data") if isinstance(state, dict) else None
            if isinstance(data, dict) and data.get("type") == "image":
                meta = data.get("meta")
                if isinstance(meta, dict):
                    if not fallback:
                        fallback = meta
                    if _contains_value(item.get("queryKey"), image_id):
                        return meta
            for child in item.values():
                result = visit(child)
                if result is not None:
                    return result
        elif isinstance(item, list):
            for child in item:
                result = visit(child)
                if result is not None:
                    return result
        return None

    return visit(value) or fallback


def _contains_value(value: Any, expected: int) -> bool:
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(_contains_value(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_value(item, expected) for item in value)
    return False


def _civitai_metadata_entries(
    metadata: dict[str, Any],
) -> tuple[MetadataEntry, ...]:
    if not metadata:
        return ()
    entries: list[MetadataEntry] = []
    prompt = metadata.get("prompt")
    negative_prompt = metadata.get("negativePrompt")
    if isinstance(prompt, str) and prompt.strip():
        entries.append(MetadataEntry("Civitai", "prompt", prompt.strip()))
    if isinstance(negative_prompt, str) and negative_prompt.strip():
        entries.append(
            MetadataEntry("Civitai", "negative_prompt", negative_prompt.strip())
        )

    settings: list[str] = []
    for label, key in (
        ("Steps", "steps"),
        ("Sampler", "sampler"),
        ("CFG scale", "cfgScale"),
        ("Seed", "seed"),
        ("Model", "Model"),
    ):
        value = metadata.get(key)
        if value is not None and value != "":
            settings.append(f"{label}: {value}")
    width = metadata.get("width")
    height = metadata.get("height")
    if width and height:
        settings.append(f"Size: {width}x{height}")
    if settings:
        entries.append(MetadataEntry("Civitai", "settings", "\n".join(settings)))
    entries.append(
        MetadataEntry(
            "Civitai",
            "metadata",
            json.dumps(metadata, ensure_ascii=False, indent=2),
        )
    )
    return tuple(entries)


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


class _AllowedDropRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: frozenset[str] | None = None) -> None:
        super().__init__()
        self.allowed_hosts = (
            frozenset(DEFAULT_ALLOWED_DROP_URL_HOSTS)
            if allowed_hosts is None
            else allowed_hosts
        )

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not _is_allowed_drop_url(newurl, self.allowed_hosts):
            raise ValueError("Image URL redirected outside the allowed host.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_allowed_drop_url(
    request: urllib.request.Request,
    allowed_hosts: frozenset[str] | None = None,
) -> Any:
    opener = urllib.request.build_opener(_AllowedDropRedirectHandler(allowed_hosts))
    return opener.open(request, timeout=20)


def _is_allowed_drop_url(
    url: str,
    allowed_hosts: frozenset[str] | None = None,
) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https"}:
        return False
    try:
        hostname = _normalize_allowed_url_host(parsed.hostname or "")
    except ValueError:
        return False
    rules = (
        frozenset(DEFAULT_ALLOWED_DROP_URL_HOSTS)
        if allowed_hosts is None
        else allowed_hosts
    )
    return any(_host_matches_rule(hostname, rule) for rule in rules)


def _is_supported_drop_item(
    item: str,
    allowed_hosts: frozenset[str] | None = None,
) -> bool:
    parsed = urllib.parse.urlparse(item)
    if _is_allowed_drop_url(item, allowed_hosts):
        if _url_hostname(item) in CIVITAI_PAGE_HOSTS:
            return _civitai_image_page_id(item) is not None
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


def _settings_path() -> Path:
    return _app_data_dir() / SETTINGS_FILENAME


def _normalize_allowed_url_host(value: str) -> str:
    candidate = value.strip().lower().rstrip(".")
    if not candidate:
        raise ValueError("Hostname is empty.")

    if "://" in candidate:
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError(f"Only HTTPS site URLs are supported: {value}")
        candidate = parsed.hostname.lower().rstrip(".")

    wildcard = candidate.startswith("*.")
    if wildcard:
        candidate = candidate[2:]
    if not candidate or any(marker in candidate for marker in "/?#@"):
        raise ValueError(f"Invalid hostname: {value}")

    try:
        ipaddress.ip_address(candidate)
        normalized = candidate
    except ValueError:
        try:
            normalized = candidate.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError(f"Invalid hostname: {value}") from exc
        if len(normalized) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in normalized.split(".")
        ):
            raise ValueError(f"Invalid hostname: {value}")

    return f"*.{normalized}" if wildcard else normalized


def _parse_allowed_url_hosts(value: str) -> frozenset[str]:
    hosts: set[str] = set()
    for line in value.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        hosts.add(_normalize_allowed_url_host(candidate))
    return frozenset(hosts)


def _host_matches_rule(hostname: str, rule: str) -> bool:
    if rule.startswith("*."):
        suffix = rule[1:]
        return hostname.endswith(suffix) and hostname != rule[2:]
    return hostname == rule


def _load_allowed_url_hosts() -> frozenset[str]:
    path = _settings_path()
    if not path.exists():
        return frozenset(DEFAULT_ALLOWED_DROP_URL_HOSTS)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("settings root is not an object")
        values = payload.get("allowed_image_hosts")
        if not isinstance(values, list) or not all(
            isinstance(item, str) for item in values
        ):
            raise ValueError("allowed_image_hosts is not a string list")
        hosts = _parse_allowed_url_hosts("\n".join(values))
        if payload.get("version", 1) == 1 and hosts == frozenset({"ac-o.namu.la"}):
            return frozenset(DEFAULT_ALLOWED_DROP_URL_HOSTS)
        return hosts
    except (OSError, ValueError, json.JSONDecodeError):
        return frozenset(DEFAULT_ALLOWED_DROP_URL_HOSTS)


def _save_allowed_url_hosts(hosts: frozenset[str]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    payload = {
        "version": SETTINGS_SCHEMA_VERSION,
        "allowed_image_hosts": sorted(hosts),
    }
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(path)
    except OSError:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


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
    temporary_path = path.with_name(path.name + ".tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(path)
    except OSError:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _is_app_cache_path(path: Path) -> bool:
    candidate_parent = Path(path).resolve(strict=False).parent
    return any(
        candidate_parent == directory.resolve(strict=False)
        for directory in (_app_cache_dir(), _legacy_cache_dir())
    )


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
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
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
