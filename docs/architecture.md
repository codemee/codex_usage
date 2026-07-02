# Codex Usage Widget Architecture

[繁體中文](architecture.zh-TW.md) | [Back to README](../README.md)

## Overview

Codex Usage Widget is a Python desktop application. It starts a local `codex app-server` subprocess, reads account usage over newline-delimited JSON-RPC, renders an always-on-top Tkinter panel, and exposes tray controls through pystray.

The implementation currently remains in one module, `codex_usage_widget.py`. The components below are logical boundaries inside that module, not separate Python packages.

```text
Tkinter main thread
  UsageWidget ──────── SystemTrayController ── pystray thread
      │                         │
      │ worker_queue            └─ tray_action_queue ─┐
      ▼                                               │
worker threads ── AppServerClient ── stdin/stdout ── codex app-server
                         │
                         └─ reader thread ── notifications / request queues
```

## Technology stack

| Area | Technology | Purpose |
| --- | --- | --- |
| Runtime | Python 3.13+ | Application runtime |
| Environment and dependencies | uv | Sync dependencies, run commands/tests, install the tool |
| Desktop UI | Tkinter / ttk | Panel, controls, and event scheduling |
| System tray | pystray | Tray icon, menu, and callbacks |
| Imaging | Pillow | Dynamic remaining-usage icon |
| Codex integration | `codex app-server` | Usage data over stdin/stdout JSON-RPC |
| Tests | unittest / unittest.mock | Parser, client, layout, and lifecycle tests |
| Packaging | setuptools | `codex-usage-widget` console script |

Pillow and pystray are the only third-party runtime dependencies. The Python distribution must provide Tkinter.

## Components

### `AppServerClient`

The client starts `codex app-server`, sends an `initialize` request followed by an `initialized` notification, and calls `account/rateLimits/read`. A daemon reader thread routes responses by numeric request ID and places server notifications in a separate queue. Requests time out after 10 seconds by default. Shutdown terminates the process, kills it if necessary, and joins the reader thread.

The protocol uses one JSON object per line. stdout is the protocol channel and must not contain unrelated output; stderr is currently not surfaced in the UI.

### Usage model and parsing

`RateLimitSnapshot` is the immutable boundary consumed by the UI. Parsing supports multi-bucket `rateLimitsByLimitId` responses, legacy `rateLimits`, and primary, secondary, or additional windows in each bucket. Results are ordered by window duration, percentages are clamped to 0–100, and remaining usage is derived during parsing.

Protocol compatibility belongs in the parser; UI and tray code should not interpret raw app-server payloads.

### `UsageWidget`

`UsageWidget` owns the Tk root, visual state, app-server client, and all Tk `after` jobs. It builds the draggable panel, applies localization/theme/opacity, starts connection and refresh work off the UI thread, refreshes every 60 seconds, polls server notifications, and drains worker results on the Tk thread.

Tk widgets must only be accessed from the main thread. New background operations must return their results through a queue and an existing or explicitly managed Tk drain loop. Every new `after()` job must be retained and cancelled during shutdown.

### `SystemTrayController`

pystray runs in a daemon thread. Tray callbacks enqueue `show` or `close` actions in `tray_action_queue`; the Tk main thread consumes them. Pillow generates the icon from the primary remaining percentage and active theme.

The Windows helpers at the top of the module prevent ghost artifacts from layered windows and native popup menus. Changes to shutdown or context-menu behavior require validation on Windows hardware.

## Runtime data flow

1. `main()` creates the Tk root and `UsageWidget`.
2. Tk schedules `connect()`; a worker starts app-server and completes the initialize handshake.
3. The client requests `account/rateLimits/read`; its reader thread matches the response by ID.
4. The parser converts the payload into `RateLimitSnapshot` values.
5. The worker places snapshots in `worker_queue`.
6. The Tk drain loop updates cards, status, the tray icon, and the next refresh job.
7. An `account/rateLimits/updated` notification triggers an additional refresh.

## Repository layout and tests

```text
.
├── codex_usage_widget.py
├── pyproject.toml
├── README.md
├── README.zh-TW.md
├── prd.md
├── docs/
│   ├── architecture.md
│   └── architecture.zh-TW.md
└── tests/
    ├── test_jsonrpc_client.py
    ├── test_layout_constants.py
    └── test_rate_limits.py
```

- `test_jsonrpc_client.py` covers the handshake, response routing, and RPC errors with a fake process.
- `test_rate_limits.py` covers payload variants, windows, ordering, percentages, and time formatting.
- `test_layout_constants.py` covers UI constants, locale/theme selection, shutdown cleanup, and tray images. Some tests create a Tk root, so CI needs a display server.

## Development workflow

```powershell
uv sync
uv run codex-usage-widget
uv run python -m unittest discover -s tests
```

Use `uv add <package>` and `uv remove <package>` for dependencies. The installed command is declared under `[project.scripts]` in `pyproject.toml` and targets `codex_usage_widget:main`.

## Change guidelines

- App-server payload changes: update the parser and fixture-style tests first; retain `RateLimitSnapshot` as the UI boundary.
- UI features: keep Tk access on the main thread and add lifecycle or layout coverage.
- Tray features: route callbacks through `tray_action_queue`.
- Periodic work: retain the `after()` ID and cancel it in `_cancel_all_after_jobs()`.
- If the module continues to grow, the existing boundaries can become `app_server.py`, `models.py`, `tray.py`, and `ui.py`; preserve tested behavior during that refactor.

## Known boundaries

- The app-server methods and payload are a Codex CLI integration surface and should be compatibility-tested after CLI upgrades.
- System theme detection is Windows-focused; unsupported environments fall back to light mode.
- Windows has additional ghost-window workarounds; cross-platform changes should verify close, context-menu, and tray-restore behavior.
- Preferences are process-local and reset to system language, system theme, and 75% opacity after restart.
