# Codex Usage Widget

Always-on-top desktop widget for monitoring Codex usage limits through the Codex app-server.

## 繁體中文

### 功能

- 透過 `codex app-server` 讀取 Codex 剩餘用量。
- 以置頂小面板顯示主要與其他用量視窗的剩餘百分比、重置時間與 credits。
- 支援繁體中文與英文，預設跟隨系統語系；非繁中或英文系統會 fallback 到英文。
- 支援跟隨系統、Tiffany Blue 淺色主題、Monokai 深色主題。
- 支援 0% 到 100% 面板不透明度，預設 75%。
- 雙按面板可縮到系統匣。
- 系統匣圖示會顯示主要剩餘用量，滑鼠移上去會顯示剩餘用量 tooltip。
- 雙按系統匣圖示可重新顯示置頂面板；右鍵選單也提供顯示與關閉。

### 需求

- Python 3.13+
- uv
- 可執行的 Codex CLI，且支援 `codex app-server`
- 桌面環境需支援系統匣

### 安裝

```powershell
uv sync
```

### 執行

```powershell
uv run python codex_usage_widget.py
```

### 操作

- 拖曳面板空白區域或用量卡片可移動面板。
- 上方 `◎ / 文 / A` 按鈕切換語系模式：跟隨系統、繁體中文、英文。
- 上方 `◐ / ☀ / ☾` 按鈕切換主題模式：跟隨系統、淺色、深色。
- 上方滑桿調整面板不透明度。
- 右鍵面板可立即更新、重新連線或關閉。
- 雙按面板可縮到系統匣。
- 雙按系統匣圖示可顯示置頂面板。

### 測試

```powershell
uv run python -m unittest discover -s tests
```

## English

### Features

- Reads Codex usage limits through `codex app-server`.
- Shows remaining percentage, reset time, and credits in an always-on-top widget.
- Supports Traditional Chinese and English. The default language follows the system language; unsupported languages fall back to English.
- Supports system theme, Tiffany Blue light theme, and Monokai dark theme.
- Supports panel opacity from 0% to 100%, defaulting to 75%.
- Double-click the panel to minimize it to the system tray.
- The tray icon renders the primary remaining usage, and its tooltip shows the remaining usage.
- Double-click the tray icon to restore the always-on-top panel. The tray context menu also includes show and close actions.

### Requirements

- Python 3.13+
- uv
- Codex CLI with `codex app-server` support
- A desktop environment with system tray support

### Installation

```powershell
uv sync
```

### Run

```powershell
uv run python codex_usage_widget.py
```

### Usage

- Drag empty panel space or usage cards to move the widget.
- Use the top `◎ / 文 / A` button to cycle language modes: system, Traditional Chinese, English.
- Use the top `◐ / ☀ / ☾` button to cycle theme modes: system, light, dark.
- Use the top slider to adjust panel opacity.
- Right-click the panel to refresh now, reconnect, or close.
- Double-click the panel to minimize it to the system tray.
- Double-click the tray icon to restore the always-on-top panel.

### Tests

```powershell
uv run python -m unittest discover -s tests
```
