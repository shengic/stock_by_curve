import asyncio
import datetime
import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from capture_tw import (
    DEFAULT_RANGE_CODE,
    DEFAULT_VIEWPORT_HEIGHT,
    DEVICE_SCALE_FACTOR,
    TARGET_DPI,
    TIME_RANGES,
    VIEWPORT_WIDTH,
    capture_ticker_list,
    read_tickers,
)

APP_VERSION = "4.1-TW"
APP_BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
APP_DEFAULT_STOCK_FILE = APP_BASE_DIR / "tw_stock.txt"
APP_DEFAULT_OUTPUT_PDF_DIR = APP_BASE_DIR / "tw_stock_image_pdf"
APP_DEFAULT_OUTPUT_PDF_PATH = APP_DEFAULT_OUTPUT_PDF_DIR / "tw_stock_capture.pdf"
MAX_PROGRESS_LOG_LINES = 200


class StockCaptureApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(f"Stock Curve Capture v{APP_VERSION}")
        self.geometry("900x760")
        self.minsize(780, 680)
        self.option_add("*Font", "Iansui 10")

        self.event_queue = queue.Queue()
        self.worker_thread = None
        self.is_running = False
        self.tickers = []
        self.ticker_vars = {}
        self.selected_ticker = None
        self.select_all_var = tk.BooleanVar(value=True)
        self.failures = []
        self.success_count = 0

        self.stock_file_var = tk.StringVar(value=str(APP_DEFAULT_STOCK_FILE))
        self.output_pdf_dir_var = tk.StringVar(value=str(APP_DEFAULT_OUTPUT_PDF_DIR))
        self.output_pdf_name_var = tk.StringVar(value=str(APP_DEFAULT_OUTPUT_PDF_PATH))
        self.range_label_var = tk.StringVar(value=self._default_range_label())
        self.status_var = tk.StringVar(value="就緒")
        self.summary_var = tk.StringVar(value="尚未執行擷取。")
        self.last_output_pdf_path = None

        self._ensure_default_dirs()
        self._build_ui()
        self._load_tickers()
        self.after(100, self._poll_events)

    def _ensure_default_dirs(self):
        APP_DEFAULT_OUTPUT_PDF_DIR.mkdir(parents=True, exist_ok=True)
        Path(self.output_pdf_dir_var.get()).mkdir(parents=True, exist_ok=True)

    def _default_range_label(self):
        for label, code in TIME_RANGES.items():
            if code == DEFAULT_RANGE_CODE:
                return label
        return next(iter(TIME_RANGES))

    def _default_pdf_name(self):
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        return f"tw_stock_capture_{stamp}.pdf"

    def _build_ui(self):
        style = ttk.Style(self)
        style.configure(".", font=("Iansui", 10))
        style.configure("TLabelframe.Label", font=("Iansui", 10))

        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        file_frame = ttk.LabelFrame(
            root,
            text=f"台股走勢擷取工具 -- by Albert Sheng (v{APP_VERSION})",
            padding=10,
        )
        file_frame.pack(fill=tk.X)
        file_frame.columnconfigure(1, weight=1)

        ttk.Label(file_frame, text="股票代碼檔案").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        ttk.Entry(file_frame, textvariable=self.stock_file_var).grid(row=0, column=1, sticky=tk.EW)
        ttk.Button(file_frame, text="瀏覽...", command=self._browse_stock_file).grid(
            row=0, column=2, padx=(8, 0)
        )
        ttk.Button(file_frame, text="載入", command=self._load_tickers).grid(
            row=0, column=3, padx=(8, 0)
        )

        ttk.Label(file_frame, text="輸出 PDF 檔案").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(8, 0)
        )
        ttk.Entry(file_frame, textvariable=self.output_pdf_name_var).grid(
            row=1, column=1, sticky=tk.EW, pady=(8, 0)
        )
        ttk.Button(file_frame, text="瀏覽...", command=self._browse_output_dir).grid(
            row=1, column=2, padx=(8, 0), pady=(8, 0)
        )
        self.open_pdf_button = ttk.Button(
            file_frame, text="開啟 PDF", command=self._open_output_pdf, state=tk.DISABLED
        )
        self.open_pdf_button.grid(row=1, column=3, padx=(8, 0), pady=(8, 0))
        self.open_folder_button = ttk.Button(
            file_frame, text="開啟資料夾", command=self._open_output_folder
        )
        self.open_folder_button.grid(row=2, column=3, padx=(8, 0), pady=(4, 0))
        self.output_pdf_folder_label = ttk.Label(file_frame, text=self.output_pdf_dir_var.get())
        self.output_pdf_folder_label.grid(row=2, column=1, sticky=tk.W, pady=(4, 0))

        settings_frame = ttk.LabelFrame(root, text="擷取設定", padding=10)
        settings_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(settings_frame, text="時間區間").grid(row=0, column=0, sticky=tk.W)
        range_box = ttk.Combobox(
            settings_frame,
            textvariable=self.range_label_var,
            values=list(TIME_RANGES.keys()),
            state="readonly",
            width=18,
        )
        range_box.grid(row=0, column=1, sticky=tk.W, padx=(8, 20))
        range_box.bind("<<ComboboxSelected>>", lambda _event: self._update_preview())

        self.resolution_label = ttk.Label(settings_frame)
        self.resolution_label.grid(row=0, column=2, sticky=tk.W)

        content = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        content.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        ticker_frame = ttk.LabelFrame(content, text="股票清單", padding=10)
        ticker_frame.rowconfigure(1, weight=1)
        ticker_frame.columnconfigure(0, weight=1)

        self.select_all_check = ttk.Checkbutton(
            ticker_frame,
            text="全選",
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

        preview_frame = ttk.LabelFrame(content, text="預覽 / 記錄", padding=10)
        preview_frame.rowconfigure(2, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        self.preview_label = ttk.Label(preview_frame, anchor=tk.W)
        self.preview_label.grid(row=0, column=0, sticky=tk.EW)

        self.status_label = ttk.Label(preview_frame, textvariable=self.status_var, anchor=tk.W)
        self.status_label.grid(row=1, column=0, sticky=tk.EW, pady=(8, 4))

        self.log_text = tk.Text(preview_frame, height=14, wrap=tk.WORD, font=("Iansui", 10))
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
        self.start_button = ttk.Button(progress_frame, text="開始擷取", command=self._start_capture)
        self.start_button.grid(row=0, column=1, padx=(10, 0))
        self.progress_log_text = tk.Text(progress_frame, height=4, wrap=tk.NONE, font=("Iansui", 9))
        self.progress_log_text.grid(row=1, column=0, sticky=tk.EW, pady=(6, 0))
        progress_log_scroll = ttk.Scrollbar(
            progress_frame, orient=tk.VERTICAL, command=self.progress_log_text.yview
        )
        progress_log_scroll.grid(row=1, column=1, sticky=tk.NS, pady=(6, 0))
        self.progress_log_text.configure(yscrollcommand=progress_log_scroll.set, state=tk.DISABLED)
        self.exit_button = ttk.Button(progress_frame, text="離開", command=self.destroy)
        self.exit_button.grid(row=2, column=1, padx=(10, 0), pady=(8, 0), sticky=tk.E)

        ttk.Label(root, textvariable=self.summary_var).pack(fill=tk.X, pady=(8, 0))
        self._update_preview()

    def _browse_stock_file(self):
        path = filedialog.askopenfilename(
            title="選擇股票代碼檔案",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialdir=str(Path(self.stock_file_var.get()).parent),
        )
        if path:
            self.stock_file_var.set(path)
            self._load_tickers()

    def _browse_output_dir(self):
        default_dir = APP_DEFAULT_OUTPUT_PDF_DIR
        default_dir.mkdir(parents=True, exist_ok=True)
        current_dir = Path(self.output_pdf_dir_var.get())
        initial_dir = current_dir if current_dir.exists() else default_dir
        path = filedialog.askdirectory(
            title="選擇輸出 PDF 資料夾",
            initialdir=str(initial_dir),
        )
        if path:
            Path(path).mkdir(parents=True, exist_ok=True)
            self.output_pdf_dir_var.set(path)
            self.output_pdf_folder_label.configure(text=path)
            current_name = Path(self.output_pdf_name_var.get()).name or APP_DEFAULT_OUTPUT_PDF_PATH.name
            self.output_pdf_name_var.set(str(Path(path) / current_name))

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
            self.summary_var.set(f"找不到股票代碼檔案：{path}")
            self._update_select_all_state()
            self._update_preview()
            return

        try:
            self.tickers = read_tickers(path)
        except Exception as exc:
            self.tickers = []
            messagebox.showerror("載入失敗", str(exc))

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

        self.summary_var.set(f"已載入 {len(self.tickers)} 檔股票（來源：{path}）")
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
        range_code = self.range_label_var.get()
        ticker = self._selected_ticker()
        
        # Goodinfo is our primary preview
        from capture_tw import build_goodinfo_url, SOURCE_CONFIG
        url = build_goodinfo_url(ticker, TIME_RANGES.get(range_code, "DAILY"))
        height = SOURCE_CONFIG["GOODINFO"]["height"]
        width = VIEWPORT_WIDTH

        self.preview_label.configure(text=f"Primary URL preview (Goodinfo): {url}")
        self.resolution_label.configure(
            text=(
                f"Goodinfo: {width} x {height} px | "
                f"Others: {width} x 1500 px"
            )
        )

    def _selected_ticker(self):
        if self.selected_ticker:
            return self.selected_ticker
        if self.tickers:
            self.selected_ticker = self.tickers[0]
            return self.selected_ticker
        return "2330"

    def _start_capture(self):
        if self.is_running:
            return

        if not self.tickers:
            messagebox.showwarning("沒有股票", "請先選擇至少含一檔股票的 txt 檔。")
            return

        checked_tickers = self._checked_tickers()
        if not checked_tickers:
            messagebox.showwarning("未勾選股票", "請至少勾選一檔股票再開始擷取。")
            return

        output_pdf_dir = self.output_pdf_dir_var.get().strip()
        if not output_pdf_dir:
            messagebox.showwarning("未設定輸出資料夾", "請先選擇輸出 PDF 資料夾。")
            return
        Path(output_pdf_dir).mkdir(parents=True, exist_ok=True)
        output_pdf_name = self._default_pdf_name()
        self.output_pdf_name_var.set(str(Path(output_pdf_dir) / output_pdf_name))
        output_pdf_path = str(Path(output_pdf_dir) / f"__capture_temp_{output_pdf_name}")

        height = DEFAULT_VIEWPORT_HEIGHT
        width = VIEWPORT_WIDTH

        self.is_running = True
        self.failures = []
        self.success_count = 0
        self.progress["value"] = 0
        self.start_button.configure(state=tk.DISABLED)
        self.open_pdf_button.configure(state=tk.DISABLED)
        self.last_output_pdf_path = None
        self._clear_log()
        self._clear_progress_log()
        self._append_log("擷取開始。")
        self._append_progress_log(
            f"擷取開始。width={width}, height={height}"
        )

        range_code = TIME_RANGES[self.range_label_var.get()]
        args = (checked_tickers, output_pdf_path, range_code, height, width)
        self.worker_thread = threading.Thread(target=self._capture_worker, args=args, daemon=True)
        self.worker_thread.start()

    def _capture_worker(self, tickers, output_pdf_path, range_code, height, width):
        def on_progress(event):
            self.event_queue.put(("progress", event))

        try:
            result = asyncio.run(
                capture_ticker_list(
                    tickers,
                    output_pdf_path,
                    range_code=range_code,
                    height=height,
                    width=width,
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
            self.status_var.set(f"[{index}/{total}] 擷取中：{ticker}")
            self._append_log(f"[{index}/{total}] 擷取 {ticker}: {event['url']}")
            self._append_progress_log(
                f"[{index}/{total}] 擷取 {ticker} ({event.get('width')}x{event.get('height')})"
            )
        elif event["event"] == "success":
            self.success_count += 1
            self.status_var.set(f"[{index}/{total}] {ticker} 已完成")
            self._append_log(f"[{ticker}] 成功：{event['output_path']}")
            self._append_progress_log(f"[{index}/{total}] {ticker} 已完成")
        elif event["event"] == "failure":
            self.failures.append((ticker, event.get("error", "")))
            self.status_var.set(f"[{index}/{total}] {ticker} 失敗")
            self._append_log(f"[{ticker}] 失敗：{event.get('error', '')}")
            self._append_progress_log(f"[{index}/{total}] {ticker} 失敗")

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
            f"擷取完成。成功：{success_count}，失敗：{failed_count}，PDF 頁數：{saved_pages}。"
        )

        if failures:
            self.status_var.set("完成（含失敗）")
            self._append_log("失敗股票：")
            for ticker, error in failures:
                self._append_log(f"- {ticker}: {error}")
            if final_pdf_path:
                self._append_log(f"已儲存 PDF：{final_pdf_path}")
            messagebox.showwarning("擷取完成", f"有 {failed_count} 檔股票擷取失敗。")
            self._append_progress_log("完成（含失敗）")
        else:
            self.status_var.set("全部擷取成功")
            if final_pdf_path:
                self._append_log(f"已儲存 PDF：{final_pdf_path}")
                self._cleanup_generated_pdfs(Path(final_pdf_path).parent, keep_paths={Path(final_pdf_path)})
            messagebox.showinfo("擷取完成", "全部股票擷取成功。")
            self._append_progress_log("全部擷取成功")

    def _handle_error(self, error):
        self.is_running = False
        self.start_button.configure(state=tk.NORMAL)
        self.status_var.set("擷取因錯誤中止")
        self.summary_var.set("擷取失敗。")
        self._append_log(f"錯誤：{error}")
        self._append_progress_log(f"錯誤：{error}")
        messagebox.showerror("擷取失敗", error)

    def _open_output_pdf(self):
        if not self.last_output_pdf_path:
            return
        pdf_path = Path(self.last_output_pdf_path)
        if not pdf_path.exists():
            messagebox.showwarning("找不到 PDF", f"找不到 PDF 檔案：{pdf_path}")
            return
        os.startfile(str(pdf_path))

    def _open_output_folder(self):
        folder = Path(self.output_pdf_dir_var.get() or APP_DEFAULT_OUTPUT_PDF_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def _prompt_save_pdf_path(self, temp_pdf_path, saved_pages):
        if not temp_pdf_path:
            return None
        temp_path = Path(temp_pdf_path)
        if saved_pages <= 0 or not temp_path.exists():
            return None

        default_name = self._default_pdf_name()
        target_path = filedialog.asksaveasfilename(
            title="另存擷取結果 PDF",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
            initialdir=self.output_pdf_dir_var.get() or str(APP_DEFAULT_OUTPUT_PDF_DIR),
            initialfile=default_name,
            parent=self,
        )
        if not target_path:
            if temp_path.exists():
                temp_path.unlink()
            self._append_log("已取消儲存，暫存 PDF 已刪除。")
            self._append_progress_log("已取消儲存，暫存 PDF 已刪除。")
            return None

        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        shutil.move(str(temp_path), str(target))
        self.output_pdf_dir_var.set(str(target.parent))
        self.output_pdf_folder_label.configure(text=str(target.parent))
        self.output_pdf_name_var.set(str(target))
        return str(target)

    def _cleanup_generated_pdfs(self, folder, keep_paths=None):
        keep_resolved = set()
        for keep in (keep_paths or set()):
            try:
                keep_resolved.add(Path(keep).resolve())
            except Exception:
                pass

        for pdf_path in Path(folder).glob("*.pdf"):
            try:
                if pdf_path.resolve() in keep_resolved:
                    continue
                pdf_path.unlink(missing_ok=True)
            except Exception:
                # Best effort cleanup; do not block capture flow.
                pass

    def _append_log(self, message):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _append_progress_log(self, message):
        self.progress_log_text.configure(state=tk.NORMAL)
        self.progress_log_text.insert(tk.END, message + "\n")
        line_count = int(self.progress_log_text.index("end-1c").split(".")[0])
        if line_count > MAX_PROGRESS_LOG_LINES:
            overflow = line_count - MAX_PROGRESS_LOG_LINES
            self.progress_log_text.delete("1.0", f"{overflow + 1}.0")
        self.progress_log_text.see(tk.END)
        self.progress_log_text.configure(state=tk.DISABLED)

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _clear_progress_log(self):
        self.progress_log_text.configure(state=tk.NORMAL)
        self.progress_log_text.delete("1.0", tk.END)
        self.progress_log_text.configure(state=tk.DISABLED)


if __name__ == "__main__":
    app = StockCaptureApp()
    app.mainloop()
