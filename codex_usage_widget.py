from __future__ import annotations

import json
import locale
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable

import tkinter as tk
from tkinter import ttk

import pystray
from PIL import Image, ImageDraw, ImageFont


REQUEST_TIMEOUT_SECONDS = 10.0
REFRESH_INTERVAL_SECONDS = 60
CARD_WIDTH = 128
CARD_HEIGHT = 112
DEFAULT_OPACITY_PERCENT = 75
LANGUAGE_MODES = ("system", "zh", "en")
THEME_MODES = ("system", "light", "dark")


TRANSLATIONS = {
    "zh": {
        "title": "Codex 用量",
        "connecting": "連線中... 正在啟動 codex app-server",
        "reconnecting": "重新連線中... 正在重啟 app-server",
        "refresh_now": "立即更新",
        "reconnect": "重新連線",
        "close": "關閉",
        "show_panel": "顯示面板",
        "not_found": "找不到 codex CLI / app-server。",
        "not_connected": "app-server 未連線或已結束。",
        "no_bucket": "No Codex rate-limit bucket was returned.",
        "unknown": "未知",
        "reset": "重置",
        "last_updated": "最後更新",
        "credits": "額度",
        "limit_status": "限制狀態",
        "error": "錯誤",
        "last_failed": "最後更新失敗",
        "no_usage": "沒有收到用量資料。",
        "language_tip": "切換語言",
        "theme_tip": "切換主題",
        "opacity_tip": "透明度",
        "mode_system": "跟隨系統",
        "mode_zh": "繁體中文",
        "mode_en": "English",
        "mode_light": "淺色",
        "mode_dark": "深色",
        "language_status": "語言：{mode}",
        "theme_status": "主題：{mode}",
        "minutes": "{value:g} 分",
        "hours": "{value:g} 小時",
        "days": "{value:g} 天",
    },
    "en": {
        "title": "Codex Usage",
        "connecting": "Connecting... starting codex app-server",
        "reconnecting": "Reconnecting... restarting app-server",
        "refresh_now": "Refresh now",
        "reconnect": "Reconnect",
        "close": "Close",
        "show_panel": "Show panel",
        "not_found": "codex CLI / app-server was not found.",
        "not_connected": "app-server is not connected or has exited.",
        "no_bucket": "No Codex rate-limit bucket was returned.",
        "unknown": "unknown",
        "reset": "reset",
        "last_updated": "Updated",
        "credits": "credits",
        "limit_status": "limit status",
        "error": "Error",
        "last_failed": "last update failed",
        "no_usage": "No usage data was returned.",
        "language_tip": "Switch language",
        "theme_tip": "Switch theme",
        "opacity_tip": "Opacity",
        "mode_system": "System",
        "mode_zh": "Traditional Chinese",
        "mode_en": "English",
        "mode_light": "Light",
        "mode_dark": "Dark",
        "language_status": "Language: {mode}",
        "theme_status": "Theme: {mode}",
        "minutes": "{value:g} min",
        "hours": "{value:g} hr",
        "days": "{value:g} day",
    },
}


THEMES = {
    "light": {
        "window_bg": "#e7fffb",
        "surface": "#d2f7f1",
        "card_bg": "#ffffff",
        "card_border": "#81d8d0",
        "card_active": "#4fbeb6",
        "text": "#123331",
        "muted": "#35706a",
        "error": "#b91c1c",
        "button_bg": "#b6eee7",
        "button_hover": "#96e2da",
        "scale_trough": "#abe5de",
        "accent": "#0f766e",
    },
    "dark": {
        "window_bg": "#272822",
        "surface": "#1e1f1c",
        "card_bg": "#272822",
        "card_border": "#75715e",
        "card_active": "#a6e22e",
        "text": "#f8f8f2",
        "muted": "#a59f85",
        "error": "#f92672",
        "button_bg": "#3e3d32",
        "button_hover": "#49483e",
        "scale_trough": "#49483e",
        "accent": "#66d9ef",
    },
}


class AppServerError(Exception):
    pass


class JsonRpcError(AppServerError):
    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class JsonRpcTimeout(AppServerError):
    pass


@dataclass(frozen=True)
class RateLimitSnapshot:
    limit_id: str
    limit_kind: str
    limit_name: str | None
    used_percent: float
    remaining_percent: float
    window_duration_mins: int | None
    resets_at: int | None
    resets_at_text: str
    window_label: str
    reset_credits: int | None
    reached_type: str | None


def _clamp_percent(value: float) -> float:
    return max(0.0, min(100.0, value))


def _format_reset_time(resets_at: int | None, window_duration_mins: int | None) -> str:
    if not resets_at:
        return "unknown"
    reset_time = datetime.fromtimestamp(resets_at)
    if window_duration_mins is not None and window_duration_mins <= 24 * 60:
        return reset_time.strftime("%H:%M")
    return reset_time.strftime("%m-%d %H:%M")


def _format_window_duration(window_duration_mins: int | None) -> str:
    if not window_duration_mins:
        return "unknown"
    if window_duration_mins < 60:
        return f"{window_duration_mins} min"
    if window_duration_mins < 24 * 60:
        hours = window_duration_mins / 60
        return f"{hours:g} hr"
    days = window_duration_mins / (24 * 60)
    return f"{days:g} day"


def _normalize_language_code(language_code: str | None) -> str:
    normalized = (language_code or "").lower().replace("-", "_")
    if normalized.startswith(("zh_tw", "zh_hk", "zh_mo", "zh_hant", "chinese_traditional")):
        return "zh"
    if normalized.startswith("en"):
        return "en"
    return "en"


def read_windows_user_locale() -> str | None:
    if sys.platform != "win32":
        return None

    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(85)
        length = ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer))
        if length:
            return buffer.value
    except Exception:
        return None
    return None


def read_macos_user_language() -> str | None:
    if sys.platform != "darwin":
        return None

    try:
        completed = subprocess.run(
            ["defaults", "read", "-g", "AppleLanguages"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except Exception:
        return None

    if completed.returncode != 0:
        return None

    for raw_line in completed.stdout.splitlines():
        language = raw_line.strip().strip('",')
        if language and language not in {"(", ")"}:
            return language
    return None


def detect_system_language() -> str:
    language_code = (
        read_windows_user_locale()
        or read_macos_user_language()
        or locale.getlocale()[0]
        or locale.getlocale(locale.LC_CTYPE)[0]
        or ""
    )
    return _normalize_language_code(language_code)


def detect_system_theme() -> str:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            apps_use_light_theme, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if apps_use_light_theme else "dark"
    except Exception:
        return "light"


def select_rate_limit_buckets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    by_limit_id = payload.get("rateLimitsByLimitId")
    if isinstance(by_limit_id, dict):
        buckets = [bucket for bucket in by_limit_id.values() if isinstance(bucket, dict)]
        if buckets:
            return sorted(buckets, key=_bucket_sort_key)

    legacy_bucket = payload.get("rateLimits")
    if isinstance(legacy_bucket, dict):
        return [legacy_bucket]

    raise ValueError("No Codex rate-limit bucket was returned.")


def _bucket_sort_key(bucket: dict[str, Any]) -> tuple[int, str]:
    primary = bucket.get("primary")
    duration = None
    if isinstance(primary, dict):
        duration = primary.get("windowDurationMins")
    if not isinstance(duration, int):
        duration = 10**9
    return duration, str(bucket.get("limitId") or "")


def parse_rate_limit_snapshots(payload: dict[str, Any]) -> list[RateLimitSnapshot]:
    snapshots: list[RateLimitSnapshot] = []
    for bucket in select_rate_limit_buckets(payload):
        snapshots.extend(_parse_rate_limit_bucket(bucket, payload))
    return sorted(snapshots, key=lambda snapshot: (snapshot.window_duration_mins or 10**9, snapshot.limit_kind))


def parse_rate_limits(payload: dict[str, Any]) -> RateLimitSnapshot:
    return parse_rate_limit_snapshots(payload)[0]


def main_remaining_percent(snapshots: Iterable[RateLimitSnapshot]) -> float | None:
    for snapshot in snapshots:
        return snapshot.remaining_percent
    return None


def format_tray_tooltip(title: str, remaining_percent: float | None) -> str:
    if remaining_percent is None:
        return f"{title}: --%"
    return f"{title}: {remaining_percent:.0f}%"


def _load_tray_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "Arial Bold.ttf" if bold else "Arial.ttf",
        "Helvetica.ttc",
        "segoeuib.ttf" if bold else "segoeui.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_tray_font(draw: ImageDraw.ImageDraw, text: str, size: int) -> ImageFont.ImageFont:
    max_width = int(size * 0.78)
    max_height = int(size * 0.62)
    for font_size in range(int(size * 0.76), int(size * 0.28), -1):
        font = _load_tray_font(font_size, bold=True)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width and bbox[3] - bbox[1] <= max_height:
            return font
    return _load_tray_font(max(18, size // 2), bold=True)


def create_tray_icon_image(remaining_percent: float | None, theme_name: str = "dark", size: int = 64) -> Image.Image:
    theme = THEMES.get(theme_name, THEMES["dark"])
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset = max(2, size // 24)
    draw.rounded_rectangle(
        (inset, inset, size - inset, size - inset),
        radius=max(7, size // 6),
        fill=theme["card_bg"],
        outline=theme["card_border"],
        width=max(1, size // 24),
    )

    text = "--" if remaining_percent is None else f"{remaining_percent:.0f}"
    font = _fit_tray_font(draw, text, size)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    draw.text(
        ((size - text_width) / 2 - bbox[0], (size - text_height) / 2 - bbox[1] - size * 0.02),
        text,
        font=font,
        fill=theme["text"],
    )

    return image


def _iter_limit_windows(bucket: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    windows: list[tuple[str, dict[str, Any]]] = []
    seen: set[int] = set()

    for key in ("primary", "secondary"):
        value = bucket.get(key)
        if isinstance(value, dict):
            windows.append((key, value))
            seen.add(id(value))

    for key, value in bucket.items():
        if key in {"primary", "secondary"} or not isinstance(value, dict) or id(value) in seen:
            continue
        if "usedPercent" in value or "windowDurationMins" in value or "resetsAt" in value:
            windows.append((str(key), value))

    if not windows:
        windows.append(("primary", {}))
    return windows


def _parse_rate_limit_bucket(bucket: dict[str, Any], payload: dict[str, Any]) -> list[RateLimitSnapshot]:
    reset_credits = None
    credits = payload.get("rateLimitResetCredits")
    if isinstance(credits, dict):
        available = credits.get("availableCount")
        if isinstance(available, int):
            reset_credits = available

    limit_name = bucket.get("limitName")
    if not isinstance(limit_name, str):
        limit_name = None

    reached_type = bucket.get("rateLimitReachedType")
    if not isinstance(reached_type, str):
        reached_type = None

    snapshots: list[RateLimitSnapshot] = []
    for limit_kind, limit_window in _iter_limit_windows(bucket):
        used_raw = limit_window.get("usedPercent", 0)
        try:
            used_percent = _clamp_percent(float(used_raw))
        except (TypeError, ValueError):
            used_percent = 0.0

        window_duration = limit_window.get("windowDurationMins")
        if not isinstance(window_duration, int):
            window_duration = None

        resets_at = limit_window.get("resetsAt")
        if not isinstance(resets_at, int):
            resets_at = None

        snapshots.append(
            RateLimitSnapshot(
                limit_id=str(bucket.get("limitId") or "codex"),
                limit_kind=limit_kind,
                limit_name=limit_name,
                used_percent=used_percent,
                remaining_percent=_clamp_percent(100.0 - used_percent),
                window_duration_mins=window_duration,
                resets_at=resets_at,
                resets_at_text=_format_reset_time(resets_at, window_duration),
                window_label=_format_window_duration(window_duration),
                reset_credits=reset_credits,
                reached_type=reached_type,
            )
        )
    return snapshots


PopenFactory = Callable[..., Any]


class AppServerClient:
    def __init__(
        self,
        command: list[str] | None = None,
        popen_factory: PopenFactory = subprocess.Popen,
    ) -> None:
        self.command = command or ["codex", "app-server"]
        self.popen_factory = popen_factory
        self.process: Any | None = None
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self.notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self.sent_messages: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._closed = threading.Event()

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return

        try:
            self.process = self.popen_factory(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise AppServerError("找不到 codex CLI / app-server。") from exc

        self._closed.clear()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex_usage_widget",
                    "title": "Codex Usage Widget",
                    "version": "0.1.0",
                }
            },
        )
        self.notify("initialized")

    def stop(self) -> None:
        self._closed.set()
        process = self.process
        self.process = None
        if process is None:
            return

        try:
            if process.poll() is None:
                process.terminate()
        except Exception:
            pass

        reader_thread = self._reader_thread
        if reader_thread is not None and reader_thread.is_alive():
            reader_thread.join(timeout=1)

        try:
            if process.poll() is None:
                process.kill()
        except Exception:
            pass

        if reader_thread is not None and reader_thread.is_alive():
            reader_thread.join(timeout=1)
        self._reader_thread = None

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        message_id = self._allocate_id()
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self._pending[message_id] = response_queue
        self._send({"method": method, "id": message_id, "params": params or {}})

        try:
            response = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            self._pending.pop(message_id, None)
            raise JsonRpcTimeout(f"{method} timed out after {timeout:.0f} seconds.") from exc

        if "error" in response:
            error = response.get("error") or {}
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise JsonRpcError(code, str(message))

        result = response.get("result")
        if isinstance(result, dict):
            return result
        return {}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message = {"method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def read_rate_limits(self) -> list[RateLimitSnapshot]:
        if self.process is None or self.process.poll() is not None:
            raise AppServerError("app-server 未連線或已結束。")
        return parse_rate_limit_snapshots(self.request("account/rateLimits/read"))

    def _allocate_id(self) -> int:
        with self._lock:
            message_id = self._next_id
            self._next_id += 1
            return message_id

    def _send(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise AppServerError("app-server 未連線或已結束。")

        self.sent_messages.append(message)
        line = json.dumps(message, separators=(",", ":")) + "\n"
        process.stdin.write(line)
        process.stdin.flush()

    def _read_loop(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return

        for line in process.stdout:
            if self._closed.is_set():
                return
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue

            message_id = message.get("id")
            if isinstance(message_id, int):
                response_queue = self._pending.pop(message_id, None)
                if response_queue is not None:
                    response_queue.put(message)
                continue

            method = message.get("method")
            if isinstance(method, str):
                self.notifications.put(message)


class ToolTip:
    def __init__(self, widget: tk.Widget, text_factory: Callable[[], str]) -> None:
        self.widget = widget
        self.text_factory = text_factory
        self.tip_window: tk.Toplevel | None = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event: tk.Event | None = None) -> None:
        if self.tip_window is not None:
            return
        text = self.text_factory()
        if not text:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.overrideredirect(True)
        self.tip_window.geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tip_window,
            text=text,
            bg="#111827",
            fg="#f9fafb",
            padx=8,
            pady=4,
            font=("Segoe UI", 8),
        )
        label.pack()

    def hide(self, event: tk.Event | None = None) -> None:
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


class SystemTrayController:
    def __init__(
        self,
        title_factory: Callable[[], str],
        show_label_factory: Callable[[], str],
        close_label_factory: Callable[[], str],
        restore_callback: Callable[[], None],
        close_callback: Callable[[], None],
    ) -> None:
        self.title_factory = title_factory
        self.show_label_factory = show_label_factory
        self.close_label_factory = close_label_factory
        self.restore_callback = restore_callback
        self.close_callback = close_callback
        self.icon: pystray.Icon | None = None
        self.remaining_percent: float | None = None
        self.theme_name = "dark"
        self._lock = threading.Lock()

    def start(self) -> None:
        if self.icon is not None:
            return

        self.icon = pystray.Icon(
            "codex_usage_widget",
            create_tray_icon_image(self.remaining_percent, self.theme_name),
            format_tray_tooltip(self.title_factory(), self.remaining_percent),
            menu=pystray.Menu(
                pystray.MenuItem(self.show_label_factory, self._restore_from_tray, default=True, visible=False),
                pystray.MenuItem(self.show_label_factory, self._restore_from_tray),
                pystray.MenuItem(self.close_label_factory, self._quit_from_tray),
            ),
        )

        try:
            self.icon.run_detached()
        except Exception:
            threading.Thread(target=self.icon.run, daemon=True).start()

    def update(self, remaining_percent: float | None, theme_name: str) -> None:
        with self._lock:
            self.remaining_percent = remaining_percent
            self.theme_name = theme_name
            icon = self.icon
            if icon is None:
                return
            icon.icon = create_tray_icon_image(remaining_percent, theme_name)
            icon.title = format_tray_tooltip(self.title_factory(), remaining_percent)

    def stop(self) -> None:
        icon = self.icon
        self.icon = None
        if icon is not None:
            icon.stop()

    def _restore_from_tray(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.restore_callback()

    def _quit_from_tray(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.close_callback()
        self.stop()


def _is_interactive_control(widget: tk.Widget) -> bool:
    return isinstance(widget, (tk.Button, tk.Scale, ttk.Scale)) or bool(getattr(widget, "_usage_widget_control", False))


class UsageWidget:
    def __init__(self, root: tk.Tk, enable_tray: bool = True) -> None:
        self.root = root
        self.show_on_start = root.state() != "withdrawn"
        if self.show_on_start:
            self.root.withdraw()
        self.client: AppServerClient | None = None
        self.worker_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.tray_action_queue: queue.Queue[str] = queue.Queue()
        self.last_snapshots: list[RateLimitSnapshot] = []
        self.next_refresh_job: str | None = None
        self.drag_start: tuple[int, int] | None = None
        self.is_refreshing = False
        self.language_mode = "system"
        self.theme_mode = "system"
        self.language = self._resolve_language()
        self.theme_name = self._resolve_theme()
        self.opacity_percent = DEFAULT_OPACITY_PERCENT
        self.opacity_var = tk.IntVar(value=self.opacity_percent)
        self.status_text_key = "connecting"
        self.status_text_kwargs: dict[str, Any] = {}
        self.status_is_error = False
        self.card_frames: list[tk.Frame] = []
        self.enable_tray = enable_tray
        self.tray_controller = SystemTrayController(
            title_factory=lambda: self._t("title"),
            show_label_factory=lambda item: self._t("show_panel"),
            close_label_factory=lambda item: self._t("close"),
            restore_callback=lambda: self.tray_action_queue.put("show"),
            close_callback=lambda: self.tray_action_queue.put("close"),
        )

        self.root.title(self._t("title"))
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", self.opacity_percent / 100)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._build_ui()
        self._bind_drag(self.frame)
        self._bind_double_click_to_tray(self.frame)
        self._apply_theme()
        self._set_status_key("connecting")
        if self.enable_tray:
            self.tray_controller.start()
        self._sync_tray()

        if self.show_on_start:
            self.root.after_idle(self._show_initial_panel)
        self.root.after(100, self.connect)
        self.root.after(200, self._drain_worker_queue)
        self.root.after(200, self._drain_tray_action_queue)
        self.root.after(500, self._poll_notifications)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Window.TFrame", borderwidth=0)
        style.configure("Toolbar.TFrame", borderwidth=0)
        style.configure("Window.TLabel", font=("Segoe UI", 9))
        style.configure("WindowError.TLabel", font=("Segoe UI", 9))
        style.configure("Percent.TLabel", font=("Segoe UI", 28, "bold"))
        style.configure("Flat.Horizontal.TScale", borderwidth=0, sliderlength=14, troughcolor="#49483e")

        self.frame = ttk.Frame(self.root, style="Window.TFrame", padding=(12, 10))
        self.frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.frame.columnconfigure(0, weight=1, minsize=CARD_WIDTH)

        self.toolbar = ttk.Frame(self.frame, style="Toolbar.TFrame")
        self.toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.toolbar.columnconfigure(3, weight=1)

        self.language_button = self._make_icon_button(self.toolbar, "◎", self.toggle_language)
        self.language_button.grid(row=0, column=0, sticky="w", padx=(0, 6))
        ToolTip(self.language_button, self._language_tooltip)

        self.theme_button = self._make_icon_button(self.toolbar, "◐", self.toggle_theme)
        self.theme_button.grid(row=0, column=1, sticky="w", padx=(0, 10))
        ToolTip(self.theme_button, self._theme_tooltip)

        self.opacity_icon = ttk.Label(self.toolbar, text="◌", style="Window.TLabel")
        self.opacity_icon.grid(row=0, column=2, sticky="w", padx=(0, 4))
        ToolTip(self.opacity_icon, lambda: self._t("opacity_tip"))

        self.opacity_scale = ttk.Scale(
            self.toolbar,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.opacity_var,
            command=self._on_opacity_changed,
            style="Flat.Horizontal.TScale",
        )
        self.opacity_scale.grid(row=0, column=3, sticky="ew", padx=(0, 6))

        self.opacity_label = ttk.Label(self.toolbar, text=f"{self.opacity_percent}%", style="Window.TLabel", width=4)
        self.opacity_label.grid(row=0, column=4, sticky="e")

        self.limits_frame = ttk.Frame(self.frame, style="Window.TFrame")
        self.limits_frame.grid(row=1, column=0, sticky="nsew")

        self.placeholder_label = ttk.Label(self.limits_frame, text="--%", style="Percent.TLabel")
        self.placeholder_label.grid(row=0, column=0, sticky="w")

        self.status_label = ttk.Label(self.frame, text="", style="Window.TLabel", justify="left")
        self.status_label.grid(row=2, column=0, sticky="w", pady=(6, 0))

        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label=self._t("refresh_now"), command=self.refresh_now)
        self.context_menu.add_command(label=self._t("reconnect"), command=self.reconnect)
        self.context_menu.add_separator()
        self.context_menu.add_command(label=self._t("close"), command=self.close)

        self._bind_context_menu(self.root)

    def _make_icon_button(self, parent: tk.Widget, text: str, command: Callable[[], None]) -> tk.Label:
        theme = THEMES[self.theme_name]
        button = tk.Label(
            parent,
            text=text,
            width=3,
            height=1,
            borderwidth=1,
            relief="solid",
            cursor="hand2",
            font=("Segoe UI", 11, "bold"),
            bg=theme["button_bg"],
            fg=theme["text"],
            activebackground=theme["button_hover"],
            activeforeground=theme["text"],
            highlightthickness=0,
        )
        button._usage_widget_control = True
        button.bind("<Enter>", lambda event: button.configure(bg=THEMES[self.theme_name]["button_hover"]))
        button.bind("<Leave>", lambda event: button.configure(bg=THEMES[self.theme_name]["button_bg"]))
        button.bind("<ButtonRelease-1>", lambda event: command())
        return button

    def _t(self, key: str, **kwargs: Any) -> str:
        text = TRANSLATIONS.get(self.language, TRANSLATIONS["en"]).get(key, TRANSLATIONS["en"].get(key, key))
        if kwargs:
            return text.format(**kwargs)
        return text

    def _resolve_language(self) -> str:
        if self.language_mode == "system":
            return detect_system_language()
        return self.language_mode

    def _resolve_theme(self) -> str:
        if self.theme_mode == "system":
            return detect_system_theme()
        return self.theme_mode

    def _language_mode_label(self) -> str:
        if self.language_mode == "system":
            resolved = self._t("mode_zh" if self.language == "zh" else "mode_en")
            return f"{self._t('mode_system')} ({resolved})"
        return self._t(f"mode_{self.language_mode}")

    def _theme_mode_label(self) -> str:
        if self.theme_mode == "system":
            resolved = self._t(f"mode_{self.theme_name}")
            return f"{self._t('mode_system')} ({resolved})"
        return self._t(f"mode_{self.theme_mode}")

    def _language_tooltip(self) -> str:
        return f"{self._t('language_tip')} | {self._t('language_status', mode=self._language_mode_label())}"

    def _theme_tooltip(self) -> str:
        return f"{self._t('theme_tip')} | {self._t('theme_status', mode=self._theme_mode_label())}"

    def _language_button_text(self) -> str:
        if self.language_mode == "system":
            return "◎"
        return "文" if self.language_mode == "zh" else "A"

    def _theme_button_text(self) -> str:
        if self.theme_mode == "system":
            return "◐"
        return "☀" if self.theme_mode == "light" else "☾"

    def _apply_theme(self) -> None:
        self.theme_name = self._resolve_theme()
        theme = THEMES[self.theme_name]
        self.root.configure(bg=theme["surface"])

        style = ttk.Style()
        style.configure("Window.TFrame", background=theme["surface"])
        style.configure("Toolbar.TFrame", background=theme["surface"])
        style.configure("Window.TLabel", background=theme["surface"], foreground=theme["muted"])
        style.configure("WindowError.TLabel", background=theme["surface"], foreground=theme["error"])
        style.configure("Percent.TLabel", background=theme["surface"], foreground=theme["text"])
        style.configure(
            "Flat.Horizontal.TScale",
            background=theme["surface"],
            troughcolor=theme["scale_trough"],
            bordercolor=theme["surface"],
            lightcolor=theme["surface"],
            darkcolor=theme["surface"],
        )

        for button in (self.language_button, self.theme_button):
            button.configure(
                bg=theme["button_bg"],
                fg=theme["text"],
                highlightbackground=theme["card_border"],
                highlightcolor=theme["card_active"],
            )

        self.theme_button.configure(text=self._theme_button_text())
        self.language_button.configure(text=self._language_button_text())
        self.opacity_icon.configure(foreground=theme["muted"])
        self.opacity_label.configure(foreground=theme["muted"])

        for frame in self.card_frames:
            frame.configure(bg=theme["card_bg"], highlightbackground=theme["card_border"], highlightcolor=theme["card_active"])
            for child in frame.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=theme["card_bg"])
                    role = getattr(child, "_color_role", "muted")
                    child.configure(fg=theme["text"] if role == "text" else theme["muted"])
        self._sync_tray()

    def _refresh_labels(self) -> None:
        self.language = self._resolve_language()
        self.root.title(self._t("title"))
        self.context_menu.entryconfigure(0, label=self._t("refresh_now"))
        self.context_menu.entryconfigure(1, label=self._t("reconnect"))
        self.context_menu.entryconfigure(3, label=self._t("close"))
        self.language_button.configure(text=self._language_button_text())
        self._render_status()
        if self.last_snapshots:
            self._show_snapshots(self.last_snapshots)
        self._sync_tray()

    def toggle_language(self) -> None:
        current_index = LANGUAGE_MODES.index(self.language_mode)
        self.language_mode = LANGUAGE_MODES[(current_index + 1) % len(LANGUAGE_MODES)]
        self.language = self._resolve_language()
        self._refresh_labels()

    def toggle_theme(self) -> None:
        current_index = THEME_MODES.index(self.theme_mode)
        self.theme_mode = THEME_MODES[(current_index + 1) % len(THEME_MODES)]
        self._apply_theme()

    def _on_opacity_changed(self, value: str) -> None:
        try:
            opacity = int(float(value))
        except ValueError:
            return
        self.opacity_percent = max(0, min(100, opacity))
        self.opacity_label.configure(text=f"{self.opacity_percent}%")
        self.root.attributes("-alpha", self.opacity_percent / 100)

    def _localized_window_label(self, snapshot: RateLimitSnapshot) -> str:
        duration = snapshot.window_duration_mins
        if not duration:
            return self._t("unknown")
        if duration < 60:
            return self._t("minutes", value=duration)
        if duration < 24 * 60:
            return self._t("hours", value=duration / 60)
        return self._t("days", value=duration / (24 * 60))

    def _localized_reset_time(self, snapshot: RateLimitSnapshot) -> str:
        if snapshot.resets_at is None:
            return self._t("unknown")
        return snapshot.resets_at_text

    def _sync_tray(self) -> None:
        self.tray_controller.update(main_remaining_percent(self.last_snapshots), self.theme_name)

    def _bind_drag(self, widget: tk.Widget) -> None:
        if _is_interactive_control(widget):
            return
        widget.bind("<ButtonPress-1>", self._on_drag_start)
        widget.bind("<B1-Motion>", self._on_drag_move)
        for child in widget.winfo_children():
            self._bind_drag(child)

    def _bind_double_click_to_tray(self, widget: tk.Widget) -> None:
        if _is_interactive_control(widget):
            return
        widget.bind("<Double-Button-1>", self.hide_to_tray, add="+")
        for child in widget.winfo_children():
            self._bind_double_click_to_tray(child)

    def _bind_context_menu(self, widget: tk.Widget) -> None:
        widget.bind("<Button-3>", self._show_context_menu)
        for child in widget.winfo_children():
            self._bind_context_menu(child)

    def _show_context_menu(self, event: tk.Event) -> None:
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def _on_drag_start(self, event: tk.Event) -> None:
        self.drag_start = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _on_drag_move(self, event: tk.Event) -> None:
        if self.drag_start is None:
            return
        x_offset, y_offset = self.drag_start
        self.root.geometry(f"+{event.x_root - x_offset}+{event.y_root - y_offset}")

    def connect(self) -> None:
        self._cancel_scheduled_refresh()
        self._run_worker(self._connect_and_refresh)

    def reconnect(self) -> None:
        if self.client is not None:
            self.client.stop()
        self.client = None
        self._set_status_key("reconnecting")
        self.connect()

    def refresh_now(self) -> None:
        self._cancel_scheduled_refresh()
        self._run_worker(self._refresh_worker)

    def hide_to_tray(self, event: tk.Event | None = None) -> None:
        self.root.withdraw()
        self._sync_tray()

    def _show_initial_panel(self) -> None:
        self.root.update_idletasks()
        self.root.overrideredirect(True)
        self.show_panel()

    def show_panel(self) -> None:
        self.root.overrideredirect(True)
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.focus_force()

    def close(self) -> None:
        if self.client is not None:
            self.client.stop()
        self.tray_controller.stop()
        self.root.destroy()

    def _connect_and_refresh(self) -> None:
        try:
            self.client = AppServerClient()
            self.client.start()
            snapshots = self.client.read_rate_limits()
            self.worker_queue.put(("snapshot", snapshots))
        except Exception as exc:
            self.worker_queue.put(("error", str(exc)))

    def _refresh_worker(self) -> None:
        try:
            if self.client is None:
                self.client = AppServerClient()
                self.client.start()
            snapshots = self.client.read_rate_limits()
            self.worker_queue.put(("snapshot", snapshots))
        except Exception as exc:
            self.worker_queue.put(("error", str(exc)))

    def _run_worker(self, target: Callable[[], None]) -> None:
        if self.is_refreshing:
            return
        self.is_refreshing = True
        threading.Thread(target=target, daemon=True).start()

    def _drain_worker_queue(self) -> None:
        while True:
            try:
                kind, payload = self.worker_queue.get_nowait()
            except queue.Empty:
                break

            self.is_refreshing = False
            if kind == "snapshot":
                self._show_snapshots(payload)
            else:
                self._show_error(str(payload))
            self._schedule_refresh()

        self.root.after(200, self._drain_worker_queue)

    def _drain_tray_action_queue(self) -> None:
        while True:
            try:
                action = self.tray_action_queue.get_nowait()
            except queue.Empty:
                break

            if action == "show":
                self.show_panel()
            elif action == "close":
                self.close()
                return

        self.root.after(200, self._drain_tray_action_queue)

    def _poll_notifications(self) -> None:
        client = self.client
        if client is not None:
            while True:
                try:
                    notification = client.notifications.get_nowait()
                except queue.Empty:
                    break
                if notification.get("method") == "account/rateLimits/updated":
                    params = notification.get("params")
                    if isinstance(params, dict):
                        try:
                            self._show_snapshots(parse_rate_limit_snapshots(params))
                        except ValueError:
                            pass
        self.root.after(500, self._poll_notifications)

    def _show_snapshots(self, snapshots: Iterable[RateLimitSnapshot]) -> None:
        snapshots = list(snapshots)
        if not snapshots:
            self._show_error(self._t("no_usage"))
            return

        self.last_snapshots = snapshots
        self.card_frames = []
        theme = THEMES[self.theme_name]
        total_cards_width = len(snapshots) * CARD_WIDTH + max(0, len(snapshots) - 1) * 8
        self.frame.columnconfigure(0, minsize=total_cards_width)
        for child in self.limits_frame.winfo_children():
            child.destroy()

        for column, snapshot in enumerate(snapshots):
            limit_frame = tk.Frame(
                self.limits_frame,
                bg=theme["card_bg"],
                width=CARD_WIDTH,
                height=CARD_HEIGHT,
                highlightbackground=theme["card_border"],
                highlightcolor=theme["card_active"],
                highlightthickness=1,
                padx=10,
                pady=9,
            )
            limit_frame.grid(row=0, column=column, sticky="n", padx=(0 if column == 0 else 4, 0 if column == len(snapshots) - 1 else 4))
            limit_frame.grid_propagate(False)
            self.card_frames.append(limit_frame)

            window_label = tk.Label(
                limit_frame,
                text=self._localized_window_label(snapshot),
                bg=theme["card_bg"],
                fg=theme["muted"],
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            )
            window_label._color_role = "muted"
            window_label.grid(row=0, column=0, sticky="ew")

            percent_label = tk.Label(
                limit_frame,
                text=f"{snapshot.remaining_percent:.0f}%",
                bg=theme["card_bg"],
                fg=theme["text"],
                font=("Segoe UI", 26, "bold"),
                anchor="w",
            )
            percent_label._color_role = "text"
            percent_label.grid(row=1, column=0, sticky="ew")

            reset_label = tk.Label(
                limit_frame,
                text=f"{self._t('reset')} {self._localized_reset_time(snapshot)}",
                bg=theme["card_bg"],
                fg=theme["muted"],
                font=("Segoe UI", 8),
                anchor="w",
            )
            reset_label._color_role = "muted"
            reset_label.grid(row=2, column=0, sticky="ew")
            limit_frame.columnconfigure(0, weight=1)

            self._bind_drag(limit_frame)
            self._bind_double_click_to_tray(limit_frame)
            self._bind_context_menu(limit_frame)

        status_parts = [f"{self._t('last_updated')} {datetime.now().strftime('%H:%M:%S')}"]
        reset_credits = next((snapshot.reset_credits for snapshot in snapshots if snapshot.reset_credits is not None), None)
        if reset_credits is not None:
            status_parts.append(f"{self._t('credits')} {reset_credits}")
        reached = [snapshot.reached_type for snapshot in snapshots if snapshot.reached_type]
        if reached:
            status_parts.append(f"{self._t('limit_status')} {'/'.join(reached)}")
        self._set_status_text(" | ".join(status_parts))
        self._sync_tray()

    def _show_error(self, message: str) -> None:
        if not self.last_snapshots:
            for child in self.limits_frame.winfo_children():
                child.destroy()
            self.placeholder_label = ttk.Label(self.limits_frame, text="--%", style="Percent.TLabel")
            self.placeholder_label.grid(row=0, column=0, sticky="w")
        self._set_status_text(
            f"{self._t('error')}: {message} | {self._t('last_failed')} {datetime.now().strftime('%H:%M:%S')}",
            error=True,
        )
        self._sync_tray()

    def _set_status_key(self, key: str, error: bool = False, **kwargs: Any) -> None:
        self.status_text_key = key
        self.status_text_kwargs = kwargs
        self.status_is_error = error
        self._render_status()

    def _set_status_text(self, text: str, error: bool = False) -> None:
        self.status_text_key = ""
        self.status_text_kwargs = {"text": text}
        self.status_is_error = error
        self._render_status()

    def _render_status(self) -> None:
        if self.status_text_key:
            text = self._t(self.status_text_key, **self.status_text_kwargs)
        else:
            text = str(self.status_text_kwargs.get("text", ""))
        self.status_label.configure(text=text, style="WindowError.TLabel" if self.status_is_error else "Window.TLabel")

    def _schedule_refresh(self) -> None:
        self._cancel_scheduled_refresh()
        self.next_refresh_job = self.root.after(REFRESH_INTERVAL_SECONDS * 1000, self.refresh_now)

    def _cancel_scheduled_refresh(self) -> None:
        if self.next_refresh_job is not None:
            self.root.after_cancel(self.next_refresh_job)
            self.next_refresh_job = None


def main() -> None:
    root = tk.Tk()
    UsageWidget(root)
    root.mainloop()


if __name__ == "__main__":
    main()
