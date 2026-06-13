from __future__ import annotations

import argparse
import ctypes
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from comfy_metadata_reader import (
    MetadataResult,
    extract_sections,
    format_report,
    read_metadata,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None


SUPPORTED_FILETYPES = (
    ("Image files", "*.png *.webp *.jpg *.jpeg"),
    ("PNG files", "*.png"),
    ("WEBP files", "*.webp"),
    ("JPEG files", "*.jpg *.jpeg"),
    ("All files", "*.*"),
)


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
        self.geometry("1120x780")
        self.minsize(860, 560)
        self._configure_dpi_scaling()

        self.path_var = tk.StringVar(value="No file loaded")
        self.status_var = tk.StringVar(value="Open or drop an image file.")
        self.platform_var = tk.StringVar(value="Platform: -")
        self.text_widgets: dict[str, tk.Text] = {}
        self.current_result: MetadataResult | None = None

        self._build_ui()
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

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(8, 8, 8, 4))
        toolbar.pack(fill=tk.X)

        ttk.Button(toolbar, text="Open", command=self.open_dialog).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Copy Tab", command=self.copy_current_tab).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(toolbar, text="Save Tab", command=self.save_current_tab).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        path_label = ttk.Label(toolbar, textvariable=self.path_var, anchor=tk.W)
        path_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))

        content = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        content.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        left_panel = ttk.Frame(content, padding=8)
        left_panel.columnconfigure(0, weight=1)
        left_panel.rowconfigure(0, weight=1)

        self.drop_frame = tk.Frame(
            left_panel,
            bg="#f7e9e9",
            bd=2,
            relief=tk.RIDGE,
            highlightthickness=1,
            highlightbackground="#ffffff",
        )
        self.drop_frame.grid(row=0, column=0, sticky="nsew")
        self.drop_frame.columnconfigure(0, weight=1)
        self.drop_frame.rowconfigure(0, weight=1)

        self.drop_label = tk.Label(
            self.drop_frame,
            text="Drop image here\nor click Open",
            bg="#f7e9e9",
            fg="#333333",
            justify=tk.CENTER,
            wraplength=280,
        )
        self.drop_label.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)

        ttk.Label(left_panel, textvariable=self.platform_var).grid(
            row=1, column=0, sticky="ew", pady=(8, 0)
        )
        ttk.Label(left_panel, textvariable=self.status_var, wraplength=320).grid(
            row=2, column=0, sticky="ew", pady=(4, 0)
        )

        right_panel = ttk.Frame(content)
        right_panel.rowconfigure(0, weight=1)
        right_panel.columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(right_panel)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        content.add(left_panel, weight=1)
        content.add(right_panel, weight=3)

        for name in ("Summary", "Prompt", "Negative", "Settings", "Workflow", "Raw"):
            self._add_text_tab(name)

    def _add_text_tab(self, name: str) -> None:
        frame = ttk.Frame(self.notebook)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        text = tk.Text(frame, wrap=tk.WORD, undo=False)
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
        self.drop_label.configure(text=f"{result.path.name}\n\n{result.format_name}")
        self._render_result(result)

    def _render_result(self, result: MetadataResult) -> None:
        sections = extract_sections(result)
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
        self._set_text("Prompt", sections.prompt)
        self._set_text("Negative", sections.negative_prompt)
        self._set_text("Settings", sections.settings)
        self._set_text("Workflow", sections.workflow)
        self._set_text("Raw", format_report(result))

    def _set_text(self, tab_name: str, value: str) -> None:
        text = self.text_widgets[tab_name]
        text.configure(state=tk.NORMAL)
        text.delete("1.0", tk.END)
        text.insert("1.0", value or "")
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

    def _setup_drag_and_drop(self) -> None:
        if DND_FILES is None or not hasattr(self, "drop_target_register"):
            self.status_var.set(
                "Drag and drop requires tkinterdnd2. Use Open or install requirements."
            )
            return

        for widget in (self, self.drop_frame, self.drop_label):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._handle_drop)

    def _handle_drop(self, event: object) -> None:
        data = getattr(event, "data", "")
        paths = self.tk.splitlist(data)
        if not paths:
            return
        if len(paths) > 1:
            messagebox.showinfo("Drop image", "Drop one image file at a time.")
            return

        path = Path(paths[0])
        if path.suffix.lower() not in {".png", ".webp", ".jpg", ".jpeg"}:
            messagebox.showinfo("Unsupported file", "Use PNG, WEBP, JPG, or JPEG.")
            return
        self.open_path(path)


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
        print(format_report(read_metadata(args.image)))
        return 0

    enable_high_dpi_awareness()
    app = MetadataViewer(args.image)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
