from __future__ import annotations

import configparser
from datetime import datetime
import importlib.util
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None


APP_NAME = "example-jupyter-sandbox"
BASE_DIR = Path(__file__).resolve().parent
GENERATED_SCRIPT = BASE_DIR / "_modal_jupyter_worker.py"
MODAL_CONFIG = Path.home() / ".modal.toml"
PORT = 8888
TUNNEL_TIMEOUT_SEC = 300

FONT_FAMILY = "맑은 고딕"

GPU_OPTIONS = ("T4", "L4", "A10G", "L40S", "A100", "H100")
TIME_OPTIONS = {
    "1시간": 1,
    "2시간": 2,
    "4시간": 4,
    "6시간": 6,
    "8시간": 8,
    "12시간": 12,
    "24시간": 24,
}

BG = "#111317"
PANEL = "#171a20"
PANEL_ALT = "#1e222b"
BORDER = "#2b303c"
TEXT = "#eceff4"
MUTED = "#9aa2b1"
DISABLED_BG = "#1a1d24"
DISABLED_FG = "#5c6370"
ACCENT = "#4f7cff"
ACCENT_HOVER = "#658dff"
TOGGLE_ON_BG = "#1c2538"
TOGGLE_ON_BORDER = "#4f7cff"
GREEN = "#45b787"
RED = "#e06c75"
YELLOW = "#d8a657"


def get_python_executable() -> str:
    exe = Path(sys.executable)
    if os.name == "nt" and exe.name.lower() == "pythonw.exe":
        console_exe = exe.with_name("python.exe")
        if console_exe.exists():
            return str(console_exe)
    return str(exe)


def sanitize_app_name(raw_name: str | None) -> str:
    cleaned = (raw_name or "").strip()
    return cleaned if cleaned else APP_NAME


def make_worker(gpu: str, hours: int, app_name_str: str = APP_NAME) -> str:
    app_name = json.dumps(sanitize_app_name(app_name_str))
    gpu_value = json.dumps(gpu)
    timeout = int(hours * 3600)

    return f'''import json
import secrets
import signal
import sys
import time
import traceback
import urllib.request
import modal

APP_NAME = {app_name}
GPU = {gpu_value}
TIMEOUT = {timeout}
PORT = {PORT}
TUNNEL_TIMEOUT_SEC = {TUNNEL_TIMEOUT_SEC}

app = modal.App.lookup(APP_NAME, create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git")
    .uv_pip_install("jupyterlab", "notebook", "torch", "torchvision")
)

_stop_requested = False


def _handle_stop_signal(signum, frame):
    global _stop_requested
    _stop_requested = True
    print(f"\\n[SIGNAL {{signum}}] 종료 신호 수신 — Sandbox 및 App 정리 시작", flush=True)
    raise KeyboardInterrupt()


def _install_signal_handlers():
    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle_stop_signal)
            except Exception:
                pass


def is_jupyter_ready(base_url: str, token: str) -> bool:
    try:
        req = urllib.request.Request(f"{{base_url.rstrip('/')}}/api/status?token={{token}}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                return bool(data.get("started", False))
    except Exception:
        return False
    return False


def _stop_modal_app():
    try:
        app_obj = modal.App.lookup(APP_NAME, create_if_missing=False)
        stop_fn = getattr(app_obj, "stop", None)
        if callable(stop_fn):
            stop_fn()
            print(f"Modal App({{APP_NAME}}) 정지 완료", flush=True)
    except Exception as exc:
        print(f"[APP STOP WARN] {{exc}}", flush=True)


def main():
    _install_signal_handlers()
    token = secrets.token_urlsafe(13)
    secret = modal.Secret.from_dict({{"JUPYTER_TOKEN": token}})

    print(f"[1/4] {{GPU}} GPU Sandbox 생성 요청 (최대 {{TIMEOUT // 3600}}시간)", flush=True)

    sb = None
    try:
        sb = modal.Sandbox.create(
            "jupyter",
            "lab",
            "--ip=0.0.0.0",
            f"--port={{PORT}}",
            "--no-browser",
            "--allow-root",
            f"--ServerApp.token={{token}}",
            "--ServerApp.allow_origin=*",
            app=app,
            image=image,
            secrets=[secret],
            encrypted_ports=[PORT],
            gpu=GPU,
            timeout=TIMEOUT,
        )
        print(f"SANDBOX_ID={{sb.object_id}}", flush=True)
        print(f"[2/4] Sandbox 할당 완료: {{sb.object_id}}", flush=True)

        if _stop_requested:
            raise KeyboardInterrupt()

        print(f"[3/4] {{PORT}} 포트 터널 및 JupyterLab 준비 대기 (최대 {{TUNNEL_TIMEOUT_SEC}}초)", flush=True)
        deadline = time.time() + TUNNEL_TIMEOUT_SEC
        last_log = 0.0
        ready_tunnel = None

        while time.time() < deadline:
            if _stop_requested:
                raise KeyboardInterrupt()
            tunnels = sb.tunnels()
            if PORT in tunnels:
                candidate = tunnels[PORT]
                if is_jupyter_ready(candidate.url, token):
                    ready_tunnel = candidate
                    break
            now = time.time()
            if now - last_log >= 15:
                rem = max(0, int(deadline - now))
                print(f"[대기] 포트={{PORT in tunnels}}, 남은시간={{rem}}초", flush=True)
                last_log = now
            time.sleep(2)

        if ready_tunnel is None:
            raise RuntimeError(f"{{TUNNEL_TIMEOUT_SEC}}초 내에 JupyterLab 터널이 준비되지 않았습니다.")

        url = ready_tunnel.url.rstrip("/") + f"/lab?token={{token}}"
        print("[4/4] JupyterLab 준비 완료", flush=True)
        print("JUPYTER_URL=" + url, flush=True)

        while not _stop_requested:
            ret = sb.poll()
            if ret is not None:
                print(f"원격 Sandbox 종료됨 (code={{ret}})", flush=True)
                break
            time.sleep(2)

    except KeyboardInterrupt:
        print("\\n종료 신호 처리 중 — Sandbox 및 App 정리", flush=True)
    except Exception as exc:
        print(f"[WORKER ERROR] {{type(exc).__name__}}: {{exc}}", flush=True)
        traceback.print_exc()
        raise
    finally:
        if sb is not None:
            try:
                sb.terminate()
                print("원격 Sandbox 종료 완료", flush=True)
            except Exception as exc:
                print(f"[REMOTE TERMINATE ERROR] {{exc}}", flush=True)
        _stop_modal_app()


if __name__ == "__main__":
    sys.exit(main() or 0)
'''


def inspect_modal_auth() -> dict:
    py_exe = get_python_executable()
    package_installed = importlib.util.find_spec("modal") is not None
    env_id = bool(os.environ.get("MODAL_TOKEN_ID"))
    env_secret = bool(os.environ.get("MODAL_TOKEN_SECRET"))
    env_complete = env_id and env_secret
    config_path = Path(os.environ.get("MODAL_CONFIG_PATH", str(MODAL_CONFIG))).expanduser()
    profile_name = os.environ.get("MODAL_PROFILE", "default")
    config_exists = config_path.is_file()
    config_complete = False

    if config_exists:
        try:
            if tomllib is not None:
                with config_path.open("rb") as handle:
                    data = tomllib.load(handle)
                profile = data.get(profile_name, {})
                config_complete = bool(profile.get("token_id") and profile.get("token_secret"))
            else:
                parser = configparser.ConfigParser()
                parser.read(config_path, encoding="utf-8")
                config_complete = bool(
                    parser.has_option(profile_name, "token_id")
                    and parser.get(profile_name, "token_id").strip()
                    and parser.has_option(profile_name, "token_secret")
                    and parser.get(profile_name, "token_secret").strip()
                )
        except Exception:
            config_complete = False

    cli_verified = False
    cli_message = ""
    if package_installed:
        try:
            result = subprocess.run(
                [py_exe, "-m", "modal", "token", "info"],
                cwd=str(BASE_DIR),
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            cli_verified = result.returncode == 0
            cli_lines = (result.stdout or result.stderr).strip().splitlines()
            cli_message = cli_lines[-1] if cli_lines else ""
        except subprocess.TimeoutExpired:
            cli_message = "Modal token info 확인 시간 초과 (20초)"
        except Exception as exc:
            cli_message = str(exc)

    return {
        "installed": package_installed,
        "env_complete": env_complete,
        "env_partial": env_id != env_secret,
        "config_exists": config_exists,
        "config_complete": config_complete,
        "cli_verified": cli_verified,
        "authenticated_hint": cli_verified,
        "config_path": str(config_path),
        "profile": profile_name,
        "cli_message": cli_message,
    }


def enable_windows_dpi_awareness():
    if os.name != "nt":
        return
    try:
        import ctypes

        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        pass


class ModalJupyterUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Modal JupyterLab")
        self.root.geometry("1024x810")
        self.root.minsize(1024, 810)
        self.root.configure(bg=BG)

        self.log_queue: queue.Queue = queue.Queue()
        self.process: subprocess.Popen | None = None
        self.reader_thread: threading.Thread | None = None
        self.running = False
        self.stopping = False
        self.closing = False
        self.sandbox_id: str | None = None
        self.jupyter_url: str | None = None
        self.active_app_name: str = APP_NAME
        self._poll_after_id: str | None = None
        self._log_entries: list[tuple[str, str | None]] = []

        self.auto_open_var = tk.BooleanVar(value=True)
        self.hide_warnings_var = tk.BooleanVar(value=True)
        self.app_name_var = tk.StringVar(value=APP_NAME)
        self.gpu_var = tk.StringVar(value="A10G")
        self.time_var = tk.StringVar(value="4시간")
        self.status_var = tk.StringVar(value="대기")
        self.auth_var = tk.StringVar(value="인증 확인 중")
        self.auth_detail_var = tk.StringVar(value="")
        self.url_var = tk.StringVar(value="실행 후 주소가 표시됩니다")
        self.size_var = tk.StringVar(value="1024x810")
        self.header_meta_var = tk.StringVar(value=f"app: {APP_NAME} · port {PORT}")

        self.app_name_var.trace_add("write", self._on_app_name_change)

        self._configure_option_db()
        self._setup_styles()
        self._build_ui()
        self._sync_auto_open_visual()
        self._sync_hide_warnings_visual()
        self.refresh_auth_status()
        self._poll_after_id = self.root.after(100, self._poll_logs)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_app_name_change(self, *_args):
        current = self.app_name_var.get().strip() or APP_NAME
        self.header_meta_var.set(f"app: {current} · port {PORT}")

    def _configure_option_db(self):
        self.root.option_add("*Font", f"{{{FONT_FAMILY}}} 9")
        self.root.option_add("*TCombobox*Listbox.background", PANEL_ALT)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", f"{{{FONT_FAMILY}}} 9")
        self.root.option_add("*TCombobox*Listbox.relief", "flat")
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)

    def _setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=(FONT_FAMILY, 9))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=(FONT_FAMILY, 8))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=(FONT_FAMILY, 9))
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=(FONT_FAMILY, 8))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=(FONT_FAMILY, 16, "bold"))
        style.configure("Section.TLabel", background=PANEL, foreground=TEXT, font=(FONT_FAMILY, 10, "bold"))

        style.configure(
            "TButton",
            font=(FONT_FAMILY, 9),
            padding=(9, 6),
            background=PANEL_ALT,
            foreground=TEXT,
            borderwidth=0,
        )
        style.map(
            "TButton",
            background=[("active", BORDER), ("disabled", DISABLED_BG)],
            foreground=[("disabled", DISABLED_FG)],
        )

        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", font=(FONT_FAMILY, 9, "bold"), padding=(10, 7))
        style.map(
            "Accent.TButton",
            background=[("active", ACCENT_HOVER), ("disabled", "#25314d")],
            foreground=[("disabled", "#6d7891")],
        )

        style.configure("Danger.TButton", background="#3b2228", foreground="#f2b8c0", font=(FONT_FAMILY, 9, "bold"), padding=(10, 7))
        style.map(
            "Danger.TButton",
            background=[("active", "#522b34"), ("disabled", "#1f1a1d")],
            foreground=[("disabled", "#695d62")],
        )

        style.configure(
            "Dark.TCombobox",
            fieldbackground=PANEL_ALT,
            background=PANEL_ALT,
            foreground=TEXT,
            arrowcolor=MUTED,
            selectbackground=PANEL_ALT,
            selectforeground=TEXT,
            bordercolor=BORDER,
            lightcolor=PANEL_ALT,
            darkcolor=PANEL_ALT,
            padding=6,
            font=(FONT_FAMILY, 9),
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", PANEL_ALT), ("disabled", DISABLED_BG), ("focus", "#222733")],
            background=[("readonly", PANEL_ALT), ("disabled", DISABLED_BG), ("active", BORDER)],
            foreground=[("readonly", TEXT), ("disabled", DISABLED_FG)],
            arrowcolor=[("disabled", DISABLED_FG), ("active", TEXT)],
            bordercolor=[("focus", ACCENT), ("!focus", BORDER)],
        )

        style.configure(
            "Log.Vertical.TScrollbar",
            gripcount=0,
            background="#2c3240",
            darkcolor="#2c3240",
            lightcolor="#2c3240",
            troughcolor="#12151b",
            bordercolor="#12151b",
            arrowcolor="#8892a4",
            relief="flat",
            borderwidth=0,
            arrowsize=0,
            width=8,
        )
        style.map("Log.Vertical.TScrollbar", background=[("active", "#4b556b"), ("pressed", "#63708c")])

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=(14, 12, 14, 10))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="Modal JupyterLab", style="Title.TLabel").pack(side="left")
        ttk.Label(header, textvariable=self.size_var, style="Muted.TLabel").pack(side="left", padx=(10, 0), anchor="s", pady=(0, 3))
        ttk.Label(header, textvariable=self.header_meta_var, style="Muted.TLabel").pack(side="right", anchor="s", pady=(0, 2))

        self.auth_panel = ttk.Frame(outer, style="Panel.TFrame", padding=11)
        self.auth_panel.pack(fill="x", pady=(0, 8))
        auth_top = ttk.Frame(self.auth_panel, style="Panel.TFrame")
        auth_top.pack(fill="x")
        ttk.Label(auth_top, text="인증", style="Section.TLabel").pack(side="left")
        self.auth_badge = tk.Label(
            auth_top,
            text="확인 중",
            bg=PANEL_ALT,
            fg=MUTED,
            font=(FONT_FAMILY, 8, "bold"),
            padx=8,
            pady=2,
        )
        self.auth_badge.pack(side="right")

        self.auth_var_label = ttk.Label(self.auth_panel, textvariable=self.auth_var, style="Panel.TLabel", wraplength=600, justify="left")
        self.auth_var_label.pack(anchor="w", fill="x", pady=(5, 1))
        self.auth_detail_label = ttk.Label(self.auth_panel, textvariable=self.auth_detail_var, style="PanelMuted.TLabel", wraplength=600, justify="left")
        self.auth_detail_label.pack(anchor="w", fill="x")

        self.auth_buttons = ttk.Frame(self.auth_panel, style="Panel.TFrame")
        self.auth_buttons.pack(fill="x", pady=(8, 0))
        self.install_btn = ttk.Button(self.auth_buttons, text="설치 / 업데이트", command=self.install_modal)
        self.login_btn = ttk.Button(self.auth_buttons, text="브라우저 로그인", command=self.run_modal_setup)
        self.token_btn = ttk.Button(self.auth_buttons, text="토큰 등록", command=self.open_token_dialog)
        self.refresh_btn = ttk.Button(self.auth_buttons, text="새로고침", command=self.refresh_auth_status)
        self._layout_auth_buttons(wide=True)

        content = ttk.Frame(outer)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=4, minsize=230)
        content.columnconfigure(1, weight=6, minsize=360)
        content.rowconfigure(0, weight=1)

        left = ttk.Frame(content, style="Panel.TFrame", padding=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        ttk.Label(left, text="실행 설정", style="Section.TLabel").pack(anchor="w", pady=(0, 7))

        ttk.Label(left, text="App 이름", style="PanelMuted.TLabel").pack(anchor="w", pady=(0, 2))
        app_box = tk.Frame(left, bg=PANEL_ALT, highlightbackground=BORDER, highlightthickness=1)
        app_box.pack(fill="x", pady=(0, 8))
        self.app_name_entry = tk.Entry(
            app_box,
            textvariable=self.app_name_var,
            bg=PANEL_ALT,
            fg=TEXT,
            disabledbackground=DISABLED_BG,
            disabledforeground=DISABLED_FG,
            insertbackground=TEXT,
            relief="flat",
            font=(FONT_FAMILY, 9),
        )
        self.app_name_entry.pack(fill="x", padx=8, pady=5)

        ttk.Label(left, text="GPU", style="PanelMuted.TLabel").pack(anchor="w", pady=(0, 2))
        self.gpu_combo = ttk.Combobox(
            left,
            textvariable=self.gpu_var,
            values=GPU_OPTIONS,
            state="readonly",
            style="Dark.TCombobox",
        )
        self.gpu_combo.pack(fill="x", pady=(0, 8))

        ttk.Label(left, text="최대 시간", style="PanelMuted.TLabel").pack(anchor="w", pady=(0, 2))
        self.time_combo = ttk.Combobox(
            left,
            textvariable=self.time_var,
            values=list(TIME_OPTIONS),
            state="readonly",
            style="Dark.TCombobox",
        )
        self.time_combo.pack(fill="x", pady=(0, 8))

        self.auto_open_card = tk.Frame(
            left,
            bg=TOGGLE_ON_BG,
            highlightbackground=TOGGLE_ON_BORDER,
            highlightthickness=1,
            cursor="hand2",
        )
        self.auto_open_card.pack(fill="x", pady=(0, 10))
        self.auto_open_indicator = tk.Label(
            self.auto_open_card,
            text="▪ ON",
            bg=TOGGLE_ON_BG,
            fg=ACCENT_HOVER,
            font=(FONT_FAMILY, 8, "bold"),
            padx=8,
            pady=6,
        )
        self.auto_open_indicator.pack(side="left")
        self.auto_open_label = tk.Label(
            self.auto_open_card,
            text="주소 준비 시 브라우저 자동 열기",
            bg=TOGGLE_ON_BG,
            fg=TEXT,
            font=(FONT_FAMILY, 8),
            anchor="w",
            wraplength=170,
            justify="left",
            pady=6,
        )
        self.auto_open_label.pack(side="left", fill="x", expand=True, padx=(0, 6))
        for w in (self.auto_open_card, self.auto_open_indicator, self.auto_open_label):
            w.bind("<Button-1>", self._toggle_auto_open)

        ttk.Separator(left).pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(left, text="▶  시작", style="Accent.TButton", command=self.start)
        self.start_button.pack(fill="x", pady=(0, 5))
        self.stop_button = ttk.Button(left, text="■  종료", style="Danger.TButton", command=self.stop, state="disabled")
        self.stop_button.pack(fill="x", pady=(0, 10))

        ttk.Label(left, text="상태", style="PanelMuted.TLabel").pack(anchor="w")
        self.status_label = tk.Label(
            left,
            textvariable=self.status_var,
            bg=PANEL,
            fg=GREEN,
            font=(FONT_FAMILY, 10, "bold"),
            anchor="w",
            wraplength=190,
            justify="left",
        )
        self.status_label.pack(fill="x", pady=(2, 0))

        right = ttk.Frame(content, style="Panel.TFrame", padding=12)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        ttk.Label(right, text="접속 주소", style="Section.TLabel").pack(anchor="w", pady=(0, 5))

        url_box = tk.Frame(right, bg=PANEL_ALT, highlightbackground=BORDER, highlightthickness=1)
        url_box.pack(fill="x")
        self.url_entry = tk.Entry(
            url_box,
            textvariable=self.url_var,
            bg=PANEL_ALT,
            fg=MUTED,
            insertbackground=TEXT,
            relief="flat",
            font=(FONT_FAMILY, 8),
            state="readonly",
            readonlybackground=PANEL_ALT,
        )
        self.url_entry.pack(fill="x", padx=8, pady=7)

        url_buttons = ttk.Frame(right, style="Panel.TFrame")
        url_buttons.pack(fill="x", pady=(6, 9))
        self.open_button = ttk.Button(url_buttons, text="브라우저 열기", command=self.open_browser, state="disabled")
        self.open_button.pack(side="left", padx=(0, 6))
        self.copy_button = ttk.Button(url_buttons, text="주소 복사", command=self.copy_url, state="disabled")
        self.copy_button.pack(side="left")

        self.log_header = ttk.Frame(right, style="Panel.TFrame")
        self.log_header.pack(fill="x", pady=(0, 5))
        ttk.Label(self.log_header, text="로그", style="Section.TLabel").pack(side="left")
        self.clear_log_btn = ttk.Button(self.log_header, text="지우기", command=self.clear_log)
        self.copy_log_btn = ttk.Button(self.log_header, text="복사", command=self.copy_log)
        self.hide_warn_btn = ttk.Button(self.log_header, text="경고 숨김: ON", command=self._toggle_hide_warnings)
        self._layout_log_buttons(compact=False)

        log_frame = tk.Frame(right, bg="#101318", highlightbackground=BORDER, highlightthickness=1)
        log_frame.pack(fill="both", expand=True)
        self.log_box = tk.Text(
            log_frame,
            wrap="word",
            height=8,
            bg="#101318",
            fg="#d0d7e2",
            insertbackground=TEXT,
            selectbackground="#2f3b52",
            relief="flat",
            borderwidth=0,
            font=(FONT_FAMILY, 8),
            padx=8,
            pady=7,
            undo=False,
            yscrollcommand=lambda first, last: self.log_scrollbar.set(first, last),
        )
        self.log_scrollbar = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.log_box.yview,
            style="Log.Vertical.TScrollbar",
        )
        self.log_box.pack(side="left", fill="both", expand=True)
        self.log_scrollbar.pack(side="right", fill="y", padx=(0, 2), pady=2)
        self.log_box.tag_configure("error", foreground=RED)
        self.log_box.tag_configure("success", foreground=GREEN)
        self.log_box.tag_configure("info", foreground="#8aa4e8")
        self.log_box.configure(state="disabled")

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(7, 0))
        ttk.Label(footer, text="Modal Sandbox & App", style="Muted.TLabel").pack(side="left")
        ttk.Label(footer, text="종료 시 Sandbox와 App 정리를 시도합니다.", style="Muted.TLabel").pack(side="right")
        self.log("준비됨.", "info")

        self._auth_wide_mode = True
        self._log_compact_mode = False
        self.root.bind("<Configure>", self._on_resize)
        self.root.after(50, self._update_responsive_wrap)

    def _layout_auth_buttons(self, wide: bool):
        for w in (self.install_btn, self.login_btn, self.token_btn, self.refresh_btn):
            w.pack_forget()
            w.grid_forget()
        if wide:
            self.install_btn.pack(side="left", padx=(0, 6))
            self.login_btn.pack(side="left", padx=(0, 6))
            self.token_btn.pack(side="left", padx=(0, 6))
            self.refresh_btn.pack(side="right")
        else:
            self.install_btn.grid(row=0, column=0, padx=(0, 6), pady=(0, 4), sticky="w")
            self.login_btn.grid(row=0, column=1, padx=(0, 6), pady=(0, 4), sticky="w")
            self.token_btn.grid(row=1, column=0, padx=(0, 6), sticky="w")
            self.refresh_btn.grid(row=1, column=1, sticky="w")

    def _layout_log_buttons(self, compact: bool):
        for w in (self.hide_warn_btn, self.copy_log_btn, self.clear_log_btn):
            w.pack_forget()
        pad_x = (0, 4) if compact else (0, 6)
        self.clear_log_btn.pack(side="right")
        self.copy_log_btn.pack(side="right", padx=pad_x)
        self.hide_warn_btn.pack(side="right", padx=pad_x)

    def _update_responsive_wrap(self):
        if self.closing:
            return
        self.root.update_idletasks()
        cur_w = self.root.winfo_width()
        cur_h = self.root.winfo_height()
        if cur_w > 1 and cur_h > 1:
            self.size_var.set(f"{cur_w}x{cur_h}")
        win_w = max(cur_w, 1024)

        auth_w = max(self.auth_panel.winfo_width() - 28, 220)
        self.auth_var_label.configure(wraplength=auth_w)
        self.auth_detail_label.configure(wraplength=auth_w)

        want_wide = win_w >= 820
        if want_wide != self._auth_wide_mode:
            self._auth_wide_mode = want_wide
            self._layout_auth_buttons(wide=want_wide)

        auto_w = max(self.auto_open_card.winfo_width() - 56, 110)
        self.auto_open_label.configure(wraplength=auto_w)

        status_w = max(self.status_label.winfo_width() - 8, 120)
        self.status_label.configure(wraplength=status_w)

        want_compact = win_w < 840
        if want_compact != self._log_compact_mode:
            self._log_compact_mode = want_compact
            self._layout_log_buttons(compact=want_compact)

    def _on_resize(self, event):
        if event.widget is self.root and not self.closing:
            self._update_responsive_wrap()

    def _set_auth_buttons_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.install_btn.configure(state=state)
        self.login_btn.configure(state=state)
        self.token_btn.configure(state=state)
        self.refresh_btn.configure(state=state)
        self.app_name_entry.configure(state=state)
        self.gpu_combo.configure(state="readonly" if enabled else "disabled")
        self.time_combo.configure(state="readonly" if enabled else "disabled")

    def _toggle_auto_open(self, _event=None):
        if self.running:
            return
        self.auto_open_var.set(not self.auto_open_var.get())
        self._sync_auto_open_visual()

    def _sync_auto_open_visual(self):
        is_on = self.auto_open_var.get()
        bg = TOGGLE_ON_BG if is_on else PANEL_ALT
        bd = TOGGLE_ON_BORDER if is_on else BORDER
        fg_ind = ACCENT_HOVER if is_on else MUTED
        fg_lbl = TEXT if is_on else MUTED
        self.auto_open_card.configure(bg=bg, highlightbackground=bd)
        self.auto_open_indicator.configure(
            text="▪ ON" if is_on else "▫ OFF",
            bg=bg,
            fg=fg_ind,
        )
        self.auto_open_label.configure(bg=bg, fg=fg_lbl)

    def _is_warning_line(self, message: str) -> bool:
        low = message.lower()
        return any(
            token in low
            for token in (
                "deprecationwarning",
                "warning:",
                "userwarning",
                "futurewarning",
                "windowsselectoreventlooppolicy",
                "set_event_loop_policy",
            )
        )

    def _should_hide_log(self, line: str, prev_was_warning: bool) -> bool:
        if not self.hide_warnings_var.get():
            return False
        if self._is_warning_line(line):
            return True
        if prev_was_warning and (line.startswith("  ") or line.lstrip().startswith("^")):
            return True
        return False

    def _format_log_line(self, message: str) -> str:
        ts = datetime.now().strftime("[%H:%M:%S]")
        return f"{ts} {message.rstrip()}"

    def _append_line_to_widget(self, line: str, tag: str | None):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n", tag or "")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _rerender_logs(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        prev_was_warning = False
        for line, tag in self._log_entries:
            is_warn = self._is_warning_line(line)
            if self._should_hide_log(line, prev_was_warning):
                prev_was_warning = is_warn
                continue
            prev_was_warning = is_warn
            self.log_box.insert("end", line + "\n", tag or "")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _sync_hide_warnings_visual(self):
        if self.hide_warnings_var.get():
            self.hide_warn_btn.configure(text="경고 숨김: ON")
        else:
            self.hide_warn_btn.configure(text="경고 숨김")

    def _toggle_hide_warnings(self):
        self.hide_warnings_var.set(not self.hide_warnings_var.get())
        self._sync_hide_warnings_visual()
        self._rerender_logs()

    def log(self, message: str, tag: str | None = None):
        if self.closing:
            return
        clean = message.rstrip()
        prev_was_warning = bool(self._log_entries and self._is_warning_line(self._log_entries[-1][0]))
        formatted = self._format_log_line(clean)
        self._log_entries.append((formatted, tag))
        if self._should_hide_log(clean, prev_was_warning):
            return
        self._append_line_to_widget(formatted, tag)

    def clear_log(self):
        self._log_entries.clear()
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def copy_log(self):
        content = self.log_box.get("1.0", "end-1c")
        if not content:
            self.log("복사할 로그가 없어.", "info")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.root.update()
            self.log("로그를 복사했어.", "success")
        except tk.TclError as exc:
            self.log(f"로그 복사 실패: {exc}", "error")

    def set_status(self, text: str, color: str = GREEN):
        self.status_var.set(text)
        self.status_label.configure(fg=color)

    def refresh_auth_status(self):
        info = inspect_modal_auth()
        if not info["installed"]:
            self.auth_var.set("Modal 패키지 미설치")
            self.auth_detail_var.set("'설치 / 업데이트' 버튼으로 설치해.")
            self.auth_badge.configure(text="미설치", bg="#382227", fg=RED)
        elif info["authenticated_hint"]:
            profile_name = info["profile"]
            profile_display = profile_name if profile_name.isascii() and profile_name.isprintable() else "default"
            self.auth_var.set("인증 완료")
            self.auth_detail_var.set(f"프로필: {profile_display}")
            self.auth_badge.configure(text="인증됨", bg="#1b3329", fg=GREEN)
        elif info["env_partial"]:
            self.auth_var.set("환경 변수 불완전")
            self.auth_detail_var.set("MODAL_TOKEN_ID와 MODAL_TOKEN_SECRET이 함께 필요해.")
            self.auth_badge.configure(text="오류", bg="#382227", fg=RED)
        else:
            profile_name = info["profile"]
            profile_display = profile_name if profile_name.isascii() and profile_name.isprintable() else "default"
            self.auth_var.set("인증 필요")
            self.auth_detail_var.set(f"프로필: {profile_display} | {info['cli_message']}")
            self.auth_badge.configure(text="미인증", bg="#382e1e", fg=YELLOW)

    def _run_aux_command(self, title: str, args: list[str], input_text: str | None = None):
        if self.running:
            return
        self._set_auth_buttons_enabled(False)
        self.start_button.configure(state="disabled")
        self.log(f"[{title}] {' '.join(args)}", "info")
        self.set_status(f"{title} 중", YELLOW)
        threading.Thread(target=self._aux_worker, args=(title, args, input_text), daemon=True).start()

    def _aux_worker(self, title: str, args: list[str], input_text: str | None):
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                args,
                cwd=str(BASE_DIR),
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if input_text is not None:
                try:
                    proc.stdin.write(input_text)
                    proc.stdin.flush()
                    proc.stdin.close()
                except Exception:
                    pass
            for line in proc.stdout or []:
                self.log_queue.put(("log", f"[{title}] {line.rstrip()}"))
            code = proc.wait()
            self.log_queue.put(("aux_exit", (title, code)))
        except Exception as exc:
            self.log_queue.put(("aux_error", (title, str(exc))))

    def install_modal(self):
        self._run_aux_command("Modal 설치", [get_python_executable(), "-m", "pip", "install", "--upgrade", "modal"])

    def run_modal_setup(self):
        self._run_aux_command("브라우저 로그인", [get_python_executable(), "-m", "modal", "token", "new"])

    def open_token_dialog(self):
        if self.running:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("토큰 등록")
        dialog.configure(bg=PANEL)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        frame = ttk.Frame(dialog, style="Panel.TFrame", padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Modal Token", style="Section.TLabel").pack(anchor="w", pady=(0, 10))

        ttk.Label(frame, text="Token ID", style="PanelMuted.TLabel").pack(anchor="w")
        token_id = tk.StringVar()
        id_entry = tk.Entry(
            frame,
            textvariable=token_id,
            width=44,
            bg=PANEL_ALT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=(FONT_FAMILY, 9),
        )
        id_entry.pack(fill="x", pady=(4, 10), ipady=6)

        ttk.Label(frame, text="Token Secret", style="PanelMuted.TLabel").pack(anchor="w")
        token_secret = tk.StringVar()
        secret_entry = tk.Entry(
            frame,
            textvariable=token_secret,
            width=44,
            show="•",
            bg=PANEL_ALT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=(FONT_FAMILY, 9),
        )
        secret_entry.pack(fill="x", pady=(4, 12), ipady=6)

        show_var = tk.BooleanVar(value=False)

        def toggle_secret():
            secret_entry.configure(show="" if show_var.get() else "•")

        ttk.Checkbutton(frame, text="Secret 표시", variable=show_var, command=toggle_secret).pack(anchor="w", pady=(0, 12))
        buttons = ttk.Frame(frame, style="Panel.TFrame")
        buttons.pack(fill="x")

        def submit():
            tid = token_id.get().strip()
            sec = token_secret.get().strip()
            if not tid or not sec:
                messagebox.showwarning("입력 확인", "ID와 Secret을 모두 입력해.", parent=dialog)
                return
            if any(c in tid + sec for c in "\r\n"):
                messagebox.showerror("입력 오류", "줄바꿈 문자는 사용할 수 없어.", parent=dialog)
                return
            dialog.destroy()
            self._run_aux_command(
                "토큰 등록",
                [get_python_executable(), "-m", "modal", "token", "set", "--token-id", "-", "--token-secret", "-", "--verify"],
                tid + "\n" + sec + "\n",
            )

        ttk.Button(buttons, text="취소", command=dialog.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="등록 및 검증", style="Accent.TButton", command=submit).pack(side="right")
        id_entry.focus_set()
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

    def start(self):
        if self.running or self.closing:
            return
        auth = inspect_modal_auth()
        if not auth["installed"]:
            messagebox.showerror("Modal 미설치", "먼저 설치 / 업데이트를 눌러.")
            return
        if not auth["authenticated_hint"] and not messagebox.askyesno("인증 확인", "인증 정보가 감지되지 않았어. 그래도 실행할까?"):
            return
        app_name = sanitize_app_name(self.app_name_var.get())
        self.app_name_var.set(app_name)
        self.active_app_name = app_name
        gpu = self.gpu_var.get()
        hours = TIME_OPTIONS[self.time_var.get()]
        try:
            GENERATED_SCRIPT.write_text(make_worker(gpu, hours, app_name), encoding="utf-8")
        except Exception as exc:
            self.log(f"스크립트 생성 실패: {exc}", "error")
            messagebox.showerror("오류", str(exc))
            return

        self.sandbox_id = None
        self.jupyter_url = None
        self.url_var.set("샌드박스 준비 중…")
        self.url_entry.configure(fg=MUTED)
        self.running = True
        self.stopping = False
        self._set_auth_buttons_enabled(False)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.copy_button.configure(state="disabled")
        self.set_status("시작 중", YELLOW)
        self.log(f"--- 시작: app={app_name}, GPU={gpu}, 최대 {hours}시간 ---", "info")
        self.reader_thread = threading.Thread(target=self._run_modal_process, daemon=True)
        self.reader_thread.start()

    def _run_modal_process(self):
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            self.process = subprocess.Popen(
                [get_python_executable(), "-u", str(GENERATED_SCRIPT)],
                cwd=str(BASE_DIR),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                **kwargs,
            )
            for line in self.process.stdout or []:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith("SANDBOX_ID="):
                    sb_id = line.split("=", 1)[1].strip()
                    self.log_queue.put(("sandbox_id", sb_id))
                elif line.startswith("JUPYTER_URL="):
                    self.log_queue.put(("url", line.split("=", 1)[1].strip()))
                else:
                    self.log_queue.put(("log", line))
            self.log_queue.put(("exit", self.process.wait()))
        except FileNotFoundError:
            self.log_queue.put(("error", "Python 실행 파일을 찾지 못했어."))
        except Exception as exc:
            self.log_queue.put(("error", f"실행 예외: {exc}"))

    def _terminate_remote_sandbox_and_app(self, sb_id: str | None, app_name: str | None = None, timeout_sec: float = 12.0):
        target_app = sanitize_app_name(app_name or self.active_app_name)
        self.log_queue.put(("log", f"원격 Sandbox 및 App({target_app}) 정지 요청 중..."))
        code = (
            "import subprocess, sys, modal\n"
            "sb_id = sys.argv[1]\n"
            "app_name = sys.argv[2]\n"
            "if sb_id:\n"
            "    try:\n"
            "        sb = modal.Sandbox.from_id(sb_id)\n"
            "        sb.terminate()\n"
            "        print('SANDBOX_TERMINATED')\n"
            "    except Exception as exc:\n"
            "        print('SANDBOX_WARN:', exc)\n"
            "stopped_app = False\n"
            "try:\n"
            "    app_obj = modal.App.lookup(app_name, create_if_missing=False)\n"
            "    stop_fn = getattr(app_obj, 'stop', None)\n"
            "    if callable(stop_fn):\n"
            "        stop_fn()\n"
            "        stopped_app = True\n"
            "        print('APP_STOPPED_SDK')\n"
            "except Exception as exc:\n"
            "    print('APP_SDK_WARN:', exc)\n"
            "if not stopped_app:\n"
            "    res = subprocess.run([sys.executable, '-m', 'modal', 'app', 'stop', app_name], capture_output=True, text=True)\n"
            "    print('APP_CLI_RC:', res.returncode, (res.stdout or res.stderr).strip())\n"
        )
        try:
            res = subprocess.run(
                [get_python_executable(), "-c", code, sb_id or "", target_app],
                cwd=str(BASE_DIR),
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            out = (res.stdout or "").strip()
            err = (res.stderr or "").strip()
            if out:
                self.log_queue.put(("log", f"정리 결과: {out.replace(chr(10), ' | ')}"))
            if res.returncode != 0 and err:
                self.log_queue.put(("log", f"정리 경고: {err}"))
        except Exception as exc:
            self.log_queue.put(("log", f"정리 요청 예외: {exc}"))

    def _stop_worker_process(self, proc: subprocess.Popen | None, sb_id: str | None, app_name: str | None = None, remote_timeout: float = 12.0):
        if proc is not None and proc.poll() is None:
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(proc.pid, signal.SIGINT)
            except Exception:
                pass

            deadline = time.time() + 3.0
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.15)

            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

        self._terminate_remote_sandbox_and_app(sb_id, app_name=app_name, timeout_sec=remote_timeout)

    def stop(self, confirm: bool = True, on_complete=None):
        if not self.running or self.stopping:
            if on_complete:
                on_complete()
            return
        if confirm and not messagebox.askyesno("종료 확인", "실행 중인 Sandbox와 App을 종료할까?"):
            return

        self.stopping = True
        self.stop_button.configure(state="disabled")
        self.set_status("종료 중", YELLOW)
        self.log("Sandbox 및 App 종료 요청 중...", "info")

        proc = self.process
        sb_id = self.sandbox_id
        app_name = self.active_app_name

        def worker():
            self._stop_worker_process(proc, sb_id, app_name=app_name, remote_timeout=12.0)
            self.log_queue.put(("stop_done", on_complete))

        threading.Thread(target=worker, daemon=True).start()

    def _reset_controls(self):
        self.running = False
        self.stopping = False
        self.process = None
        self._set_auth_buttons_enabled(True)
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _poll_logs(self):
        if self.closing:
            return
        try:
            while True:
                kind, value = self.log_queue.get_nowait()
                if kind == "sandbox_id":
                    self.sandbox_id = value
                elif kind == "log":
                    low = value.lower()
                    tag = "error" if any(x in low for x in ("error", "failed", "exception", "traceback", "warn")) else None
                    self.log(value, tag)
                elif kind == "url":
                    self.jupyter_url = value
                    self.url_var.set(value)
                    self.url_entry.configure(fg="#9db6ff")
                    self.open_button.configure(state="normal")
                    self.copy_button.configure(state="normal")
                    self.set_status("실행 중", GREEN)
                    self.log("접속 주소 준비됨.", "success")
                    if self.auto_open_var.get():
                        webbrowser.open(self.jupyter_url)
                elif kind == "exit":
                    self.log(f"프로세스 종료 (code={value})")
                    if not self.stopping:
                        self.set_status("종료됨" if value == 0 else "오류 종료", MUTED if value == 0 else RED)
                        self._reset_controls()
                elif kind == "stop_done":
                    cb = value
                    self.log("종료 완료.", "success")
                    self.set_status("대기", GREEN)
                    self._reset_controls()
                    if callable(cb):
                        cb()
                elif kind == "error":
                    self.log(f"[오류] {value}", "error")
                    self.set_status("실패", RED)
                    self._reset_controls()
                elif kind == "aux_exit":
                    title, code = value
                    self.log(f"{title} {'완료' if code == 0 else f'실패 (code={code})'}.", "success" if code == 0 else "error")
                    self.refresh_auth_status()
                    if not self.running:
                        self._set_auth_buttons_enabled(True)
                        self.start_button.configure(state="normal")
                        self.set_status("대기" if code == 0 else "실패", GREEN if code == 0 else RED)
                elif kind == "aux_error":
                    title, error = value
                    self.log(f"{title} 오류: {error}", "error")
                    self.refresh_auth_status()
                    if not self.running:
                        self._set_auth_buttons_enabled(True)
                        self.start_button.configure(state="normal")
                        self.set_status("실패", RED)
        except queue.Empty:
            pass
        if not self.closing:
            self._poll_after_id = self.root.after(100, self._poll_logs)

    def open_browser(self):
        if self.jupyter_url:
            webbrowser.open(self.jupyter_url)

    def copy_url(self):
        if self.jupyter_url:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.jupyter_url)
            self.root.update()
            self.log("주소를 복사했어.", "success")

    def _force_quit_app(self):
        self.closing = True
        if self._poll_after_id is not None:
            try:
                self.root.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _on_close(self):
        if self.closing:
            return
        if self.running:
            choice = messagebox.askyesnocancel(
                "종료",
                "실행 중인 Sandbox와 App이 있어.\n\n예: Sandbox 및 App 종료 후 닫기\n아니요: 원격 유지하고 앱만 닫기\n취소: 계속 사용",
            )
            if choice is None:
                return
            if choice is True:
                self.closing = True
                self.root.withdraw()
                done_event = threading.Event()
                proc = self.process
                sb_id = self.sandbox_id
                app_name = self.active_app_name

                def cleanup_and_exit():
                    try:
                        self._stop_worker_process(proc, sb_id, app_name=app_name, remote_timeout=6.0)
                    finally:
                        done_event.set()

                threading.Thread(target=cleanup_and_exit, daemon=True).start()

                def wait_done(elapsed: float = 0.0):
                    if done_event.is_set() or elapsed >= 7.0:
                        self._force_quit_app()
                    else:
                        self.root.after(150, lambda: wait_done(elapsed + 0.15))

                self.root.after(100, lambda: wait_done(0.0))
                return

        self._force_quit_app()


def main():
    enable_windows_dpi_awareness()
    root = tk.Tk()
    ModalJupyterUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
