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
        self._run_btn.grid(row=r, column=0, columnspan=2, pady=(0, 16))
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
                    self._run_btn.config(state="normal")
                    messagebox.showinfo("Done", "Download finished.")
                    return
                elif kind == _MSG_ERROR:
                    self._status_var.set("")
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
