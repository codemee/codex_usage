# Codex 用量小工具

[English](README.md)

透過 Codex app-server 監控 Codex 用量限制的置頂桌面小工具。

## 功能

- 透過 `codex app-server` 讀取 Codex 剩餘用量。
- 以置頂小面板顯示主要與其他用量視窗的剩餘百分比、重置時間與 credits。
- 支援繁體中文與英文，預設跟隨系統語系；非繁中或英文系統會 fallback 到英文。
- 支援跟隨系統、Tiffany Blue 淺色主題、Monokai 深色主題。
- 支援 0% 到 100% 面板不透明度，預設 75%。
- 雙按面板可縮到系統匣。
- 系統匣圖示會顯示主要剩餘用量，滑鼠移上去會顯示剩餘用量 tooltip。
- 雙按系統匣圖示可重新顯示置頂面板；右鍵選單也提供顯示與關閉。
- 關閉時會先取消待執行的 Tk callback，避免視窗銷毀後仍觸發排程錯誤。

## 需求

- Python 3.13+
- uv
- 可執行的 Codex CLI，且支援 `codex app-server`
- 桌面環境需支援系統匣

## 安裝

從 GitHub 安裝：

```powershell
uv tool install git+https://github.com/codemee/codex_usage.git
```

從本機 checkout 安裝：

```powershell
uv tool install .
```

## 執行

```powershell
codex-usage-widget
```

## 操作

- 拖曳面板空白區域或用量卡片可移動面板。
- 上方 `◎ / 文 / A` 按鈕切換語系模式：跟隨系統、繁體中文、英文。
- 上方 `◐ / ☀ / ☾` 按鈕切換主題模式：跟隨系統、淺色、深色。
- 上方滑桿調整面板不透明度。
- 右鍵面板可立即更新、重新連線或關閉。
- 雙按面板可縮到系統匣。
- 雙按系統匣圖示可顯示置頂面板。

## 測試

```powershell
uv run python -m unittest discover -s tests
```
