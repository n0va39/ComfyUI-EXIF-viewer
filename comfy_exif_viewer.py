from __future__ import annotations

import argparse
import ctypes
import os
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageOps, ImageTk
except ImportError:
    Image = None
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
        self.geometry("1240x840")
        self.minsize(980, 640)
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
        self.text_widgets: dict[str, tk.Text] = {}
        self.current_result: MetadataResult | None = None
        self.current_image_path: Path | None = None
        self.preview_photo: object | None = None
        self.preview_after_id: str | None = None

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

    def _configure_fonts(self) -> None:
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
        style.configure(".", font=(self.ui_font_family, 10))
        style.configure("TButton", font=(self.ui_font_family, 10), padding=(10, 7))
        style.configure("TCheckbutton", font=(self.ui_font_family, 10), padding=(2, 4))
        style.configure("TEntry", font=(self.ui_font_family, 10), padding=(6, 4))
        style.configure("TCombobox", font=(self.ui_font_family, 10), padding=(6, 4))
        style.configure("TLabel", font=(self.ui_font_family, 10))
        style.configure("TLabelframe.Label", font=(self.ui_font_family, 10, "bold"))
        style.configure("TNotebook.Tab", font=(self.ui_font_family, 10), padding=(14, 8))
        self.option_add("*TCombobox*Listbox.font", (self.ui_font_family, 10))

    def _choose_font_family(self, candidates: tuple[str, ...]) -> str:
        available = {name.lower(): name for name in tkfont.families(self)}
        for candidate in candidates:
            found = available.get(candidate.lower())
            if found:
                return found
        return candidates[-1]

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(10, 10, 10, 6))
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
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        left_panel = ttk.Frame(content, padding=10, width=360)
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
            font=(self.ui_font_family, 12, "bold"),
            justify=tk.CENTER,
            wraplength=280,
        )
        self.drop_label.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        self.drop_frame.bind("<Configure>", self._schedule_preview_refresh)

        ttk.Label(left_panel, textvariable=self.platform_var).grid(
            row=1, column=0, sticky="ew", pady=(10, 0)
        )
        ttk.Label(left_panel, textvariable=self.status_var, wraplength=340).grid(
            row=2, column=0, sticky="ew", pady=(6, 0)
        )
        self._build_inference_controls(left_panel)

        right_panel = ttk.Frame(content)
        right_panel.rowconfigure(0, weight=1)
        right_panel.columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(right_panel)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        content.add(left_panel, weight=1)
        content.add(right_panel, weight=3)

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

    def _build_inference_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Workflow prompt guess", padding=10)
        frame.grid(row=3, column=0, sticky="ew", pady=(12, 0))
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
            bg="#fbfbfb",
            fg="#1f2933",
            insertbackground="#1f2933",
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
        text.tag_configure("link", foreground="#0563c1", underline=True)
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
