from __future__ import annotations

import argparse
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from comfy_metadata_reader import (
    MetadataResult,
    format_report,
    pretty_value,
    read_metadata,
    split_a1111_parameters,
)


SUPPORTED_FILETYPES = (
    ("Image files", "*.png *.webp *.jpg *.jpeg"),
    ("PNG files", "*.png"),
    ("WEBP files", "*.webp"),
    ("JPEG files", "*.jpg *.jpeg"),
    ("All files", "*.*"),
)


class MetadataViewer(tk.Tk):
    def __init__(self, initial_path: str | None = None) -> None:
        super().__init__()
        self.title("ComfyUI EXIF Viewer")
        self.geometry("980x760")
        self.minsize(760, 520)

        self.path_var = tk.StringVar(value="No file loaded")
        self.text_widgets: dict[str, tk.Text] = {}
        self.current_result: MetadataResult | None = None

        self._build_ui()
        if initial_path:
            self.open_path(initial_path)

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

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

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
        self._render_result(result)

    def _render_result(self, result: MetadataResult) -> None:
        parameters = result.first_value("parameters")
        prompt_json = result.first_value("prompt")
        workflow = result.first_value("workflow")
        split_params = split_a1111_parameters(parameters) if parameters else {}

        summary = [
            f"File: {result.path}",
            f"Format: {result.format_name}",
            f"Entries: {len(result.entries)}",
            "",
            f"parameters: {'yes' if parameters else 'no'}",
            f"prompt: {'yes' if prompt_json else 'no'}",
            f"workflow: {'yes' if workflow else 'no'}",
        ]
        if result.warnings:
            summary.extend(["", "Warnings:", *result.warnings])

        self._set_text("Summary", "\n".join(summary))
        self._set_text(
            "Prompt",
            split_params.get("prompt") or pretty_value(prompt_json) or parameters,
        )
        self._set_text("Negative", split_params.get("negative_prompt", ""))
        self._set_text("Settings", split_params.get("settings", ""))
        self._set_text("Workflow", pretty_value(workflow))
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

    app = MetadataViewer(args.image)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
