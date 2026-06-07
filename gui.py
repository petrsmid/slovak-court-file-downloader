import math
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from dotenv import load_dotenv

from auth import authenticate, needs_auth, clear_auth
from downloader import download_documents

load_dotenv()

_MSG_STATUS = "status"
_MSG_COLLECT = "collect"
_MSG_DOWNLOAD = "download"
_MSG_DONE = "done"
_MSG_ERROR = "error"


class _Spinner(tk.Canvas):
    _N = 8
    _SHADES = ["#d0d0d0", "#b8b8b8", "#a0a0a0", "#888888", "#707070", "#585858", "#404040", "#202020"]

    def __init__(self, parent, size: int = 32, **kw):
        bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        super().__init__(parent, width=size, height=size, bd=0, highlightthickness=0, bg=bg, **kw)
        self._size = size
        self._step = 0
        self._job = None
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        cx = cy = self._size / 2
        r_out = cx - 3
        r_in = r_out * 0.42
        w = max(2, int(self._size / 10))
        for i in range(self._N):
            shade = self._SHADES[(i - self._step) % self._N]
            a = math.radians(90 - 360 / self._N * i)
            self.create_line(
                cx + r_in * math.cos(a), cy - r_in * math.sin(a),
                cx + r_out * math.cos(a), cy - r_out * math.sin(a),
                fill=shade, width=w, capstyle=tk.ROUND,
            )

    def start(self) -> None:
        if self._job is None:
            self._tick()

    def stop(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        self._step = 0
        self._draw()

    def _tick(self) -> None:
        self._step = (self._step + 1) % self._N
        self._draw()
        self._job = self.after(80, self._tick)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("SpisDownloader")
        self.root.resizable(False, False)
        self._queue: queue.Queue = queue.Queue()
        self._build_ui()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=20)
        frame.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)

        r = 0

        # URL input
        ttk.Label(frame, text="Documents URL:").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        self._url_var = tk.StringVar(value=os.getenv("DOCUMENTS_URL", ""))
        ttk.Entry(frame, textvariable=self._url_var, width=64).grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=(2, 12)
        )
        r += 1

        # Folder picker
        ttk.Label(frame, text="Download folder:").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        self._folder_var = tk.StringVar(value=os.getenv("DOWNLOAD_DIR", "./downloads"))
        ttk.Entry(frame, textvariable=self._folder_var, width=52).grid(
            row=r, column=0, sticky="ew", pady=(2, 12)
        )
        ttk.Button(frame, text="Browse…", command=self._browse).grid(
            row=r, column=1, sticky="w", padx=(8, 0), pady=(2, 12)
        )
        r += 1

        # Re-auth checkbox
        self._reauth_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Force re-authentication", variable=self._reauth_var).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )
        r += 1

        # Run button
        self._run_btn = ttk.Button(frame, text="Run download", command=self._start)
        self._run_btn.grid(row=r, column=0, columnspan=2, pady=(0, 8))
        r += 1

        # Spinner (shown only while running)
        self._spinner = _Spinner(frame, size=32)
        self._spinner_row = r
        self._spinner.grid(row=r, column=0, columnspan=2, pady=(0, 8))
        self._spinner.grid_remove()
        r += 1

        # Collecting progress
        ttk.Label(frame, text="Collecting documents:").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        self._collect_bar = ttk.Progressbar(frame, length=440, mode="determinate", maximum=100)
        self._collect_bar.grid(row=r, column=0, sticky="ew", pady=(2, 2))
        self._collect_lbl = ttk.Label(frame, text="", width=10)
        self._collect_lbl.grid(row=r, column=1, sticky="w", padx=(8, 0))
        r += 1

        # Downloading progress
        ttk.Label(frame, text="Downloading:").grid(row=r, column=0, columnspan=2, sticky="w", pady=(10, 0))
        r += 1
        self._dl_bar = ttk.Progressbar(frame, length=440, mode="determinate", maximum=100)
        self._dl_bar.grid(row=r, column=0, sticky="ew", pady=(2, 2))
        self._dl_lbl = ttk.Label(frame, text="", width=10)
        self._dl_lbl.grid(row=r, column=1, sticky="w", padx=(8, 0))
        r += 1

        # Status line
        self._status_var = tk.StringVar()
        ttk.Label(frame, textvariable=self._status_var, foreground="gray").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

    def _browse(self) -> None:
        folder = filedialog.askdirectory(initialdir=self._folder_var.get() or ".")
        if folder:
            self._folder_var.set(folder)

    def _start(self) -> None:
        url = self._url_var.get().strip()
        folder = self._folder_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter the documents URL.")
            return
        if not folder:
            messagebox.showwarning("Missing folder", "Please select a download folder.")
            return

        self._run_btn.config(state="disabled")
        self._collect_bar["value"] = 0
        self._dl_bar["value"] = 0
        self._collect_lbl.config(text="")
        self._dl_lbl.config(text="")
        self._status_var.set("")
        self._spinner.grid(row=self._spinner_row, column=0, columnspan=2, pady=(0, 8))
        self._spinner.start()

        threading.Thread(
            target=self._worker,
            args=(url, folder, self._reauth_var.get()),
            daemon=True,
        ).start()
        self._poll()

    def _worker(self, documents_url: str, download_dir: str, reauth: bool) -> None:
        try:
            login_url = os.environ["LOGIN_URL"]
            logged_in_selector = os.getenv("LOGGED_IN_SELECTOR")

            if reauth:
                clear_auth()

            if needs_auth():
                self._queue.put((_MSG_STATUS, "Opening browser for authentication…"))
                authenticate(login_url, logged_in_selector)

            self._queue.put((_MSG_STATUS, "Navigating to documents page…"))
            download_documents(
                documents_url=documents_url,
                login_url=login_url,
                download_dir=download_dir,
                collect_progress_cb=lambda cur, tot: self._queue.put((_MSG_COLLECT, cur, tot)),
                download_progress_cb=lambda cur, tot: self._queue.put((_MSG_DOWNLOAD, cur, tot)),
            )
            self._queue.put((_MSG_DONE,))
        except Exception as exc:
            self._queue.put((_MSG_ERROR, str(exc)))

    def _poll(self) -> None:
        try:
            while True:
                msg = self._queue.get_nowait()
                kind = msg[0]
                if kind == _MSG_STATUS:
                    self._status_var.set(msg[1])
                elif kind == _MSG_COLLECT:
                    cur, tot = msg[1], msg[2]
                    self._collect_bar["value"] = cur / tot * 100
                    self._collect_lbl.config(text=f"{cur}/{tot}")
                    self._status_var.set(f"Scanning page {cur} of {tot}…")
                elif kind == _MSG_DOWNLOAD:
                    cur, tot = msg[1], msg[2]
                    self._dl_bar["value"] = cur / tot * 100
                    self._dl_lbl.config(text=f"{cur}/{tot}")
                    self._status_var.set(f"Downloading file {cur} of {tot}…")
                elif kind == _MSG_DONE:
                    self._collect_bar["value"] = 100
                    self._dl_bar["value"] = 100
                    self._status_var.set("")
                    self._spinner.stop()
                    self._spinner.grid_remove()
                    self._run_btn.config(state="normal")
                    messagebox.showinfo("Done", "Download finished.")
                    return
                elif kind == _MSG_ERROR:
                    self._status_var.set("")
                    self._spinner.stop()
                    self._spinner.grid_remove()
                    self._run_btn.config(state="normal")
                    messagebox.showerror("Error", msg[1])
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll)


def run_gui() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
