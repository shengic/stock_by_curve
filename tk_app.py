import asyncio
import datetime
import os
import queue
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from capture import (
    DEFAULT_OUTPUT_PDF_DIR,
    DEFAULT_OUTPUT_PDF_PATH,
    DEFAULT_RANGE_CODE,
    DEFAULT_STOCK_FILE,
    DEFAULT_VIEWPORT_HEIGHT,
    DEVICE_SCALE_FACTOR,
    TARGET_DPI,
    TIME_RANGES,
    VIEWPORT_WIDTH,
    build_finviz_url,
    capture_ticker_list,
    read_tickers,
)

APP_VERSION = "2.0"


class StockCaptureApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(f"Stock Curve Capture v{APP_VERSION}")
        self.geometry("900x640")
        self.minsize(780, 560)
        self.option_add("*Font", "Georgia 10")

        self.event_queue = queue.Queue()
        self.worker_thread = None
        self.is_running = False
        self.tickers = []
        self.ticker_vars = {}
        self.selected_ticker = None
        self.select_all_var = tk.BooleanVar(value=True)
        self.failures = []
        self.success_count = 0

        self.stock_file_var = tk.StringVar(value=str(DEFAULT_STOCK_FILE))
        self.output_pdf_dir_var = tk.StringVar(value=str(DEFAULT_OUTPUT_PDF_DIR))
        self.output_pdf_name_var = tk.StringVar(value=DEFAULT_OUTPUT_PDF_PATH.name)
        self.range_label_var = tk.StringVar(value=self._default_range_label())
        self.height_var = tk.IntVar(value=DEFAULT_VIEWPORT_HEIGHT)
        self.status_var = tk.StringVar(value="Ready")
        self.summary_var = tk.StringVar(value="No capture has been run.")
        self.last_output_pdf_path = None

        self._build_ui()
        self._load_tickers()
        self.after(100, self._poll_events)

    def _default_range_label(self):
        for label, code in TIME_RANGES.items():
            if code == DEFAULT_RANGE_CODE:
                return label
        return next(iter(TIME_RANGES))

    def _default_pdf_name(self):
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        return f"stock_capture_{stamp}.pdf"

    def _build_ui(self):
        style = ttk.Style(self)
        style.configure(".", font=("Georgia", 10))
        style.configure("TLabelframe.Label", font=("Georgia", 10))

        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        file_frame = ttk.LabelFrame(
            root,
            text=f"Stock curve capture tool -- by Albert Sheng (v{APP_VERSION})",
            padding=10,
        )
        file_frame.pack(fill=tk.X)
        file_frame.columnconfigure(1, weight=1)

        ttk.Label(file_frame, text="Ticker txt file").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        ttk.Entry(file_frame, textvariable=self.stock_file_var).grid(row=0, column=1, sticky=tk.EW)
        ttk.Button(file_frame, text="Browse...", command=self._browse_stock_file).grid(
            row=0, column=2, padx=(8, 0)
        )
        ttk.Button(file_frame, text="Load", command=self._load_tickers).grid(
            row=0, column=3, padx=(8, 0)
        )

        ttk.Label(file_frame, text="Captured stock curves pdf filename").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(8, 0)
        )
        ttk.Entry(file_frame, textvariable=self.output_pdf_name_var).grid(
            row=1, column=1, sticky=tk.EW, pady=(8, 0)
        )
        ttk.Button(file_frame, text="Browse...", command=self._browse_output_dir).grid(
            row=1, column=2, padx=(8, 0), pady=(8, 0)
        )
        self.open_pdf_button = ttk.Button(
            file_frame, text="Load PDF", command=self._open_output_pdf, state=tk.DISABLED
        )
        self.open_pdf_button.grid(row=1, column=3, padx=(8, 0), pady=(8, 0))
        self.output_pdf_folder_label = ttk.Label(file_frame, text=str(DEFAULT_OUTPUT_PDF_DIR))
        self.output_pdf_folder_label.grid(row=2, column=1, sticky=tk.W, pady=(4, 0))

        settings_frame = ttk.LabelFrame(root, text="Capture Settings", padding=10)
        settings_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(settings_frame, text="Time interval").grid(row=0, column=0, sticky=tk.W)
        range_box = ttk.Combobox(
            settings_frame,
            textvariable=self.range_label_var,
            values=list(TIME_RANGES.keys()),
            state="readonly",
            width=18,
        )
        range_box.grid(row=0, column=1, sticky=tk.W, padx=(8, 20))
        range_box.bind("<<ComboboxSelected>>", lambda _event: self._update_preview())

        ttk.Label(settings_frame, text="Capture height").grid(row=0, column=2, sticky=tk.W)
        height_spin = ttk.Spinbox(
            settings_frame,
            from_=600,
            to=4000,
            increment=100,
            textvariable=self.height_var,
            width=8,
            command=self._update_preview,
        )
        height_spin.grid(row=0, column=3, sticky=tk.W, padx=(8, 20))
        height_spin.bind("<KeyRelease>", lambda _event: self._update_preview())

        self.resolution_label = ttk.Label(settings_frame)
        self.resolution_label.grid(row=0, column=4, sticky=tk.W)

        content = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        content.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        ticker_frame = ttk.LabelFrame(content, text="Tickers", padding=10)
        ticker_frame.rowconfigure(1, weight=1)
        ticker_frame.columnconfigure(0, weight=1)

        self.select_all_check = ttk.Checkbutton(
            ticker_frame,
            text="Select All",
            variable=self.select_all_var,
            command=self._toggle_all_tickers,
        )
        self.select_all_check.grid(row=0, column=0, sticky=tk.W, pady=(0, 6))

        self.ticker_canvas = tk.Canvas(ticker_frame, highlightthickness=0)
        self.ticker_canvas.grid(row=1, column=0, sticky=tk.NSEW)
        ticker_scroll = ttk.Scrollbar(ticker_frame, orient=tk.VERTICAL, command=self.ticker_canvas.yview)
        ticker_scroll.grid(row=1, column=1, sticky=tk.NS)
        self.ticker_canvas.configure(yscrollcommand=ticker_scroll.set)

        self.ticker_check_frame = ttk.Frame(self.ticker_canvas)
        self.ticker_window = self.ticker_canvas.create_window(
            (0, 0), window=self.ticker_check_frame, anchor=tk.NW
        )
        self.ticker_check_frame.bind("<Configure>", self._update_ticker_scroll_region)
        self.ticker_canvas.bind("<Configure>", self._resize_ticker_check_frame)

        preview_frame = ttk.LabelFrame(content, text="Preview / Log", padding=10)
        preview_frame.rowconfigure(2, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        self.preview_label = ttk.Label(preview_frame, anchor=tk.W)
        self.preview_label.grid(row=0, column=0, sticky=tk.EW)

        self.status_label = ttk.Label(preview_frame, textvariable=self.status_var, anchor=tk.W)
        self.status_label.grid(row=1, column=0, sticky=tk.EW, pady=(8, 4))

        self.log_text = tk.Text(preview_frame, height=14, wrap=tk.WORD, font=("Georgia", 10))
        self.log_text.grid(row=2, column=0, sticky=tk.NSEW)
        log_scroll = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        log_scroll.grid(row=2, column=1, sticky=tk.NS)
        self.log_text.configure(yscrollcommand=log_scroll.set, state=tk.DISABLED)

        content.add(ticker_frame, weight=1)
        content.add(preview_frame, weight=4)

        progress_frame = ttk.Frame(root)
        progress_frame.pack(fill=tk.X, pady=(10, 0))
        progress_frame.columnconfigure(0, weight=1)

        self.progress = ttk.Progressbar(progress_frame, maximum=100)
        self.progress.grid(row=0, column=0, sticky=tk.EW)
        self.start_button = ttk.Button(progress_frame, text="Start Capture", command=self._start_capture)
        self.start_button.grid(row=0, column=1, padx=(10, 0))
        self.exit_button = ttk.Button(progress_frame, text="Exit", command=self.destroy)
        self.exit_button.grid(row=1, column=1, padx=(10, 0), pady=(8, 0), sticky=tk.E)

        ttk.Label(root, textvariable=self.summary_var).pack(fill=tk.X, pady=(8, 0))
        self._update_preview()

    def _browse_stock_file(self):
        path = filedialog.askopenfilename(
            title="Select ticker txt file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialdir=str(Path(self.stock_file_var.get()).parent),
        )
        if path:
            self.stock_file_var.set(path)
            self._load_tickers()

    def _browse_output_dir(self):
        path = filedialog.askdirectory(
            title="Select output pdf folder",
            initialdir=self.output_pdf_dir_var.get() or str(DEFAULT_OUTPUT_PDF_DIR),
        )
        if path:
            self.output_pdf_dir_var.set(path)
            self.output_pdf_folder_label.configure(text=path)

    def _load_tickers(self):
        path = Path(self.stock_file_var.get())
        previous_checked = {
            ticker: var.get()
            for ticker, var in self.ticker_vars.items()
        }
        for child in self.ticker_check_frame.winfo_children():
            child.destroy()
        self.ticker_vars = {}

        if not path.exists():
            self.tickers = []
            self.selected_ticker = None
            self.summary_var.set(f"Ticker file not found: {path}")
            self._update_select_all_state()
            self._update_preview()
            return

        try:
            self.tickers = read_tickers(path)
        except Exception as exc:
            self.tickers = []
            messagebox.showerror("Load failed", str(exc))

        for row, ticker in enumerate(self.tickers):
            var = tk.BooleanVar(value=previous_checked.get(ticker, True))
            self.ticker_vars[ticker] = var
            check = ttk.Checkbutton(
                self.ticker_check_frame,
                text=ticker,
                variable=var,
                command=lambda symbol=ticker: self._on_ticker_checked(symbol),
            )
            check.grid(row=row, column=0, sticky=tk.W, pady=1)

        self.summary_var.set(f"Loaded {len(self.tickers)} ticker(s) from {path}")
        self._restore_ticker_selection()
        self._update_select_all_state()
        self._update_preview()

    def _on_ticker_checked(self, ticker):
        self.selected_ticker = ticker
        self._update_select_all_state()
        self._update_preview()

    def _toggle_all_tickers(self):
        checked = self.select_all_var.get()
        for var in self.ticker_vars.values():
            var.set(checked)
        self._update_preview()

    def _checked_tickers(self):
        return [
            ticker
            for ticker in self.tickers
            if ticker in self.ticker_vars and self.ticker_vars[ticker].get()
        ]

    def _update_select_all_state(self):
        if not self.ticker_vars:
            self.select_all_var.set(False)
            return
        self.select_all_var.set(all(var.get() for var in self.ticker_vars.values()))

    def _update_ticker_scroll_region(self, _event=None):
        self.ticker_canvas.configure(scrollregion=self.ticker_canvas.bbox("all"))

    def _resize_ticker_check_frame(self, event):
        self.ticker_canvas.itemconfigure(self.ticker_window, width=event.width)

    def _restore_ticker_selection(self):
        if not self.tickers:
            self.selected_ticker = None
            return

        if self.selected_ticker not in self.tickers:
            self.selected_ticker = self.tickers[0]

    def _update_preview(self):
        try:
            height = int(self.height_var.get())
        except tk.TclError:
            height = DEFAULT_VIEWPORT_HEIGHT

        range_code = TIME_RANGES[self.range_label_var.get()]
        ticker = self._selected_ticker()
        self.preview_label.configure(text=f"URL preview: {build_finviz_url(ticker, range_code)}")
        self.resolution_label.configure(
            text=(
                f"{VIEWPORT_WIDTH} x {height} CSS px, "
                f"{TARGET_DPI} DPI scale ({DEVICE_SCALE_FACTOR:.3f}x)"
            )
        )

    def _selected_ticker(self):
        if self.selected_ticker:
            return self.selected_ticker
        if self.tickers:
            self.selected_ticker = self.tickers[0]
            return self.selected_ticker
        return "AAPL"

    def _start_capture(self):
        if self.is_running:
            return

        if not self.tickers:
            messagebox.showwarning("No tickers", "Please select a txt file with at least one ticker.")
            return

        checked_tickers = self._checked_tickers()
        if not checked_tickers:
            messagebox.showwarning("No tickers checked", "Please check at least one ticker to capture.")
            return

        output_pdf_dir = self.output_pdf_dir_var.get().strip()
        if not output_pdf_dir:
            messagebox.showwarning("No output folder", "Please select an output pdf folder.")
            return
        output_pdf_name = self._default_pdf_name()
        self.output_pdf_name_var.set(output_pdf_name)
        output_pdf_path = str(Path(output_pdf_dir) / f"__capture_temp_{output_pdf_name}")

        try:
            height = int(self.height_var.get())
        except tk.TclError:
            messagebox.showwarning("Invalid height", "Capture height must be a number.")
            return

        self.is_running = True
        self.failures = []
        self.success_count = 0
        self.progress["value"] = 0
        self.start_button.configure(state=tk.DISABLED)
        self.open_pdf_button.configure(state=tk.DISABLED)
        self.last_output_pdf_path = None
        self._clear_log()
        self._append_log("Capture started.")

        range_code = TIME_RANGES[self.range_label_var.get()]
        args = (checked_tickers, output_pdf_path, range_code, height)
        self.worker_thread = threading.Thread(target=self._capture_worker, args=args, daemon=True)
        self.worker_thread.start()

    def _capture_worker(self, tickers, output_pdf_path, range_code, height):
        def on_progress(event):
            self.event_queue.put(("progress", event))

        try:
            result = asyncio.run(
                capture_ticker_list(
                    tickers,
                    output_pdf_path,
                    range_code=range_code,
                    height=height,
                    progress_callback=on_progress,
                )
            )
            self.event_queue.put(("done", result))
        except Exception as exc:
            self.event_queue.put(("error", str(exc)))

    def _poll_events(self):
        try:
            while True:
                event_type, payload = self.event_queue.get_nowait()
                if event_type == "progress":
                    self._handle_progress(payload)
                elif event_type == "done":
                    self._handle_done(payload)
                elif event_type == "error":
                    self._handle_error(payload)
        except queue.Empty:
            pass

        self.after(100, self._poll_events)

    def _handle_progress(self, event):
        index = event["index"]
        total = event["total"]
        ticker = event["ticker"]
        self.progress["value"] = index * 100 / total

        if event["event"] == "start":
            self.status_var.set(f"[{index}/{total}] Capturing {ticker}")
            self._append_log(f"[{index}/{total}] Capturing {ticker}: {event['url']}")
        elif event["event"] == "success":
            self.success_count += 1
            self.status_var.set(f"[{index}/{total}] {ticker} saved")
            self._append_log(f"[{ticker}] Success: {event['output_path']}")
        elif event["event"] == "failure":
            self.failures.append((ticker, event.get("error", "")))
            self.status_var.set(f"[{index}/{total}] {ticker} failed")
            self._append_log(f"[{ticker}] Failed: {event.get('error', '')}")

    def _handle_done(self, result):
        self.is_running = False
        self.start_button.configure(state=tk.NORMAL)
        self.progress["value"] = 100

        failures = result["failures"]
        pdf_path = result.get("pdf_path")
        saved_pages = result.get("saved_pages", 0)
        final_pdf_path = self._prompt_save_pdf_path(pdf_path, saved_pages)
        self.last_output_pdf_path = final_pdf_path
        if final_pdf_path and Path(final_pdf_path).exists():
            self.open_pdf_button.configure(state=tk.NORMAL)

        failed_count = len(failures)
        success_count = self.success_count
        self.summary_var.set(
            f"Capture completed. Success: {success_count}. Failed: {failed_count}. "
            f"PDF pages saved: {saved_pages}."
        )

        if failures:
            self.status_var.set("Completed with failures")
            self._append_log("Failed tickers:")
            for ticker, error in failures:
                self._append_log(f"- {ticker}: {error}")
            if final_pdf_path:
                self._append_log(f"Saved PDF: {final_pdf_path}")
            messagebox.showwarning("Capture completed", f"{failed_count} ticker(s) failed.")
        else:
            self.status_var.set("All captures completed successfully")
            if final_pdf_path:
                self._append_log(f"Saved PDF: {final_pdf_path}")
            messagebox.showinfo("Capture completed", "All captures completed successfully.")

    def _handle_error(self, error):
        self.is_running = False
        self.start_button.configure(state=tk.NORMAL)
        self.status_var.set("Capture stopped because of an error")
        self.summary_var.set("Capture failed.")
        self._append_log(f"Error: {error}")
        messagebox.showerror("Capture failed", error)

    def _open_output_pdf(self):
        if not self.last_output_pdf_path:
            return
        pdf_path = Path(self.last_output_pdf_path)
        if not pdf_path.exists():
            messagebox.showwarning("PDF not found", f"PDF file not found: {pdf_path}")
            return
        os.startfile(str(pdf_path))

    def _prompt_save_pdf_path(self, temp_pdf_path, saved_pages):
        if not temp_pdf_path:
            return None
        temp_path = Path(temp_pdf_path)
        if saved_pages <= 0 or not temp_path.exists():
            return None

        default_name = self._default_pdf_name()
        target_path = filedialog.asksaveasfilename(
            title="Save captured PDF as",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
            initialdir=self.output_pdf_dir_var.get() or str(DEFAULT_OUTPUT_PDF_DIR),
            initialfile=default_name,
            parent=self,
        )
        if not target_path:
            self._append_log(f"Save cancelled. Temporary PDF kept at: {temp_path}")
            self.output_pdf_name_var.set(temp_path.name)
            return str(temp_path)

        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_path), str(target))
        self.output_pdf_dir_var.set(str(target.parent))
        self.output_pdf_folder_label.configure(text=str(target.parent))
        self.output_pdf_name_var.set(target.name)
        return str(target)

    def _append_log(self, message):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)


if __name__ == "__main__":
    app = StockCaptureApp()
    app.mainloop()
