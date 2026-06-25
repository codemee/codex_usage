# Codex Usage Widget

[繁體中文](README.zh-TW.md)

Always-on-top desktop widget for monitoring Codex usage limits through the Codex app-server.

## Features

- Reads Codex usage limits through `codex app-server`.
- Shows remaining percentage, reset time, and credits in an always-on-top widget.
- Supports Traditional Chinese and English. The default language follows the system language; unsupported languages fall back to English.
- Supports system theme, Tiffany Blue light theme, and Monokai dark theme.
- Supports panel opacity from 0% to 100%, defaulting to 75%.
- Double-click the panel to minimize it to the system tray.
- The tray icon renders the primary remaining usage, and its tooltip shows the remaining usage.
- Double-click the tray icon to restore the always-on-top panel. The tray context menu also includes show and close actions.
- Shuts down cleanly by cancelling pending Tk callbacks before destroying the window.

## Requirements

- Python 3.13+
- uv
- Codex CLI with `codex app-server` support
- A desktop environment with system tray support

## Installation

Install from GitHub:

```powershell
uv tool install git+https://github.com/codemee/codex_usage.git
```

Install from a local checkout:

```powershell
uv tool install .
```

## Run

```powershell
codex-usage-widget
```

## Usage

- Drag empty panel space or usage cards to move the widget.
- Use the top `◎ / 文 / A` button to cycle language modes: system, Traditional Chinese, English.
- Use the top `◐ / ☀ / ☾` button to cycle theme modes: system, light, dark.
- Use the top slider to adjust panel opacity.
- Right-click the panel to refresh now, reconnect, or close.
- Double-click the panel to minimize it to the system tray.
- Double-click the tray icon to restore the always-on-top panel.

## Tests

```powershell
uv run python -m unittest discover -s tests
```
