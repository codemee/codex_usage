from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable

import tkinter as tk
from tkinter import ttk


REQUEST_TIMEOUT_SECONDS = 10.0
REFRESH_INTERVAL_SECONDS = 60
CARD_WIDTH = 128
CARD_HEIGHT = 112


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


class UsageWidget:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.client: AppServerClient | None = None
        self.worker_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.last_snapshots: list[RateLimitSnapshot] = []
        self.next_refresh_job: str | None = None
        self.drag_start: tuple[int, int] | None = None
        self.is_refreshing = False

        self.root.title("Codex Usage")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        self.root.configure(bg="#111827")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._build_ui()
        self._bind_drag(self.frame)
        self._set_status("連線中... 正在啟動 codex app-server")

        self.root.after(100, self.connect)
        self.root.after(200, self._drain_worker_queue)
        self.root.after(500, self._poll_notifications)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Widget.TFrame", background="#111827")
        style.configure("Window.TFrame", background="#1f2937")
        style.configure("Window.TLabel", background="#1f2937", foreground="#9ca3af", font=("Segoe UI", 9))
        style.configure("WindowError.TLabel", background="#1f2937", foreground="#fca5a5", font=("Segoe UI", 9))
        style.configure("Percent.TLabel", background="#1f2937", foreground="#f9fafb", font=("Segoe UI", 28, "bold"))

        self.frame = ttk.Frame(self.root, style="Window.TFrame", padding=(12, 10))
        self.frame.grid(row=0, column=0, sticky="nsew")

        self.limits_frame = ttk.Frame(self.frame, style="Window.TFrame")
        self.limits_frame.grid(row=0, column=0, sticky="nsew")

        self.placeholder_label = ttk.Label(self.limits_frame, text="--%", style="Percent.TLabel")
        self.placeholder_label.grid(row=0, column=0, sticky="w")

        self.status_label = ttk.Label(self.frame, text="", style="Window.TLabel", justify="left")
        self.status_label.grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="立即更新", command=self.refresh_now)
        self.context_menu.add_command(label="重新連線", command=self.reconnect)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="關閉", command=self.close)

        self._bind_context_menu(self.root)

    def _bind_drag(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-1>", self._on_drag_start)
        widget.bind("<B1-Motion>", self._on_drag_move)
        for child in widget.winfo_children():
            child.bind("<ButtonPress-1>", self._on_drag_start)
            child.bind("<B1-Motion>", self._on_drag_move)

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
        self._set_status("重新連線中... 正在重啟 app-server")
        self.connect()

    def refresh_now(self) -> None:
        self._cancel_scheduled_refresh()
        self._run_worker(self._refresh_worker)

    def close(self) -> None:
        if self.client is not None:
            self.client.stop()
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
            self._show_error("沒有收到用量資料。")
            return

        self.last_snapshots = snapshots
        for child in self.limits_frame.winfo_children():
            child.destroy()

        for column, snapshot in enumerate(snapshots):
            limit_frame = tk.Frame(
                self.limits_frame,
                bg="#111827",
                width=CARD_WIDTH,
                height=CARD_HEIGHT,
                highlightbackground="#475569",
                highlightcolor="#64748b",
                highlightthickness=1,
                padx=10,
                pady=9,
            )
            limit_frame.grid(row=0, column=column, sticky="n", padx=(0 if column == 0 else 4, 0 if column == len(snapshots) - 1 else 4))
            limit_frame.grid_propagate(False)

            window_label = tk.Label(
                limit_frame,
                text=snapshot.window_label,
                bg="#111827",
                fg="#cbd5e1",
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            )
            window_label.grid(row=0, column=0, sticky="ew")

            percent_label = tk.Label(
                limit_frame,
                text=f"{snapshot.remaining_percent:.0f}%",
                bg="#111827",
                fg="#f9fafb",
                font=("Segoe UI", 26, "bold"),
                anchor="w",
            )
            percent_label.grid(row=1, column=0, sticky="ew")

            reset_label = tk.Label(
                limit_frame,
                text=f"reset {snapshot.resets_at_text}",
                bg="#111827",
                fg="#9ca3af",
                font=("Segoe UI", 8),
                anchor="w",
            )
            reset_label.grid(row=2, column=0, sticky="ew")
            limit_frame.columnconfigure(0, weight=1)

            self._bind_drag(limit_frame)
            self._bind_context_menu(limit_frame)

        status_parts = [f"最後更新 {datetime.now().strftime('%H:%M:%S')}"]
        reset_credits = next((snapshot.reset_credits for snapshot in snapshots if snapshot.reset_credits is not None), None)
        if reset_credits is not None:
            status_parts.append(f"credits {reset_credits}")
        reached = [snapshot.reached_type for snapshot in snapshots if snapshot.reached_type]
        if reached:
            status_parts.append(f"限制狀態 {'/'.join(reached)}")
        self._set_status(" | ".join(status_parts))

    def _show_error(self, message: str) -> None:
        if not self.last_snapshots:
            for child in self.limits_frame.winfo_children():
                child.destroy()
            self.placeholder_label = ttk.Label(self.limits_frame, text="--%", style="Percent.TLabel")
            self.placeholder_label.grid(row=0, column=0, sticky="w")
        self._set_status(f"錯誤: {message} | 最後更新失敗 {datetime.now().strftime('%H:%M:%S')}", error=True)

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_label.configure(text=text, style="WindowError.TLabel" if error else "Window.TLabel")

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
