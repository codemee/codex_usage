# Codex Usage Widget 系統架構

[English](architecture.md) | [回到 README](../README.zh-TW.md)

## 系統概觀

Codex Usage Widget 是 Python 桌面應用程式。它啟動本機 `codex app-server` 子程序，以逐行 JSON-RPC 訊息取得帳號用量，再透過 Tkinter 顯示置頂面板，並以 pystray 提供系統匣操作。

目前程式刻意維持為單一模組 `codex_usage_widget.py`。下列元件是程式內的邏輯邊界，不代表已拆成個別 Python package。

```text
Tkinter 主執行緒
  UsageWidget ──────── SystemTrayController ── pystray 執行緒
      │                         │
      │ worker_queue            └─ tray_action_queue ─┐
      ▼                                               │
背景工作執行緒 ── AppServerClient ── stdin/stdout ── codex app-server
                         │
                         └─ reader 執行緒 ── notifications / request queues
```

## 技術棧

| 項目 | 技術 | 用途 |
| --- | --- | --- |
| 執行環境 | Python 3.13+ | 應用程式與套件執行環境 |
| 環境／套件管理 | uv | 同步相依套件、執行程式與測試、安裝 CLI tool |
| 桌面 UI | Python 標準庫 Tkinter / ttk | 置頂面板、控制項與排程 |
| 系統匣 | pystray | 系統匣圖示、選單與事件 |
| 圖像 | Pillow | 動態繪製剩餘用量圖示 |
| Codex 整合 | `codex app-server` | 透過標準輸入／輸出的 JSON-RPC 提供用量資料 |
| 測試 | unittest / unittest.mock | parser、client、版面與生命週期測試 |
| 打包 | setuptools | 產生 `codex-usage-widget` console script |

執行期第三方相依只有 Pillow 與 pystray；Tkinter 必須由 Python 發行版提供。

## 核心元件

### `AppServerClient`

- 以 `codex app-server` 啟動子程序。
- 先送出 `initialize` request，再送出 `initialized` notification。
- 以遞增 request ID 和每個 request 專屬的 queue 配對回應，預設 timeout 為 10 秒。
- `read_rate_limits()` 呼叫 `account/rateLimits/read`。
- reader daemon thread 持續讀取 stdout：有 ID 的訊息送回 request queue，只有 method 的訊息進入 notification queue。
- `stop()` 先 terminate，必要時 kill，並等待 reader thread 結束。

app-server 使用換行分隔的 JSON。stdout 是協定通道，不應加入其他輸出；stderr 目前未呈現在 UI。

### 用量資料模型與解析

`RateLimitSnapshot` 是 UI 使用的不可變資料模型。parser 同時支援：

- 新格式 `rateLimitsByLimitId` 的多 bucket 回應；
- 舊格式 `rateLimits`；
- 同一 bucket 內的 `primary`、`secondary` 與其他用量視窗。

解析結果依視窗時間由短至長排序，`usedPercent` 會限制在 0–100，並計算 `remaining_percent`。UI 與系統匣不應直接解析 app-server payload；協定相容處理應留在 parser。

### `UsageWidget`

`UsageWidget` 擁有 Tk root、畫面狀態、client 與所有 Tk `after` 排程。主要責任包括：

- 建立無邊框、置頂、可拖曳的用量面板；
- 套用語系、主題與透明度；
- 在背景執行連線及更新，避免阻塞 Tk event loop；
- 每 60 秒主動更新，並監聽 `account/rateLimits/updated` notification；
- 將背景結果從 `worker_queue` 搬回 Tk 主執行緒更新 UI；
- 關閉時取消所有排程並釋放 client、系統匣與視窗資源。

Tk widget 只能在主執行緒操作。新增背景工作時，結果必須經由 queue 回到既有 drain loop，不能直接修改 Tk 元件。

### `SystemTrayController`

pystray 在自己的 daemon thread 執行。系統匣 callback 不直接呼叫 Tk，而是把 `show` 或 `close` 動作放進 `tray_action_queue`，再由 Tk 主執行緒處理。圖示由 Pillow 依主要剩餘百分比與目前主題動態產生。

檔案開頭的 Windows helper 用來修正透明 layered window 與 native popup menu 可能留下的殘影；修改關閉或右鍵選單流程時須在 Windows 實機驗證。

## 資料流

1. `main()` 建立 Tk root 與 `UsageWidget`。
2. Tk 排程 `connect()`；背景 thread 啟動 app-server 並完成 initialize handshake。
3. client 呼叫 `account/rateLimits/read`，reader thread 依 request ID 配對回應。
4. parser 將 payload 轉成 `RateLimitSnapshot` 清單。
5. 背景 thread 將 snapshot 放入 `worker_queue`。
6. Tk drain loop 更新卡片、狀態文字、系統匣圖示與下一次更新排程。
7. app-server 若送出 `account/rateLimits/updated`，notification poll loop 觸發額外更新。

## 專案與測試結構

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

- `test_jsonrpc_client.py`：以 fake process 驗證 handshake、request/response 配對與 RPC error。
- `test_rate_limits.py`：驗證新舊 payload、多視窗、排序、百分比與時間格式。
- `test_layout_constants.py`：驗證 UI 常數、語系／主題、關閉清理及系統匣圖示。部分測試會建立 Tk root，因此 CI 需要可用的 display server。

## 開發工作流程

```powershell
uv sync
uv run codex-usage-widget
uv run python -m unittest discover -s tests
```

新增相依套件使用 `uv add <package>`，移除使用 `uv remove <package>`。安裝後的 CLI 進入點由 `pyproject.toml` 的 `[project.scripts]` 定義，指向 `codex_usage_widget:main`。

## 修改準則

- app-server payload 變更：先調整 parser 與 fixture 型測試，維持 `RateLimitSnapshot` 作為 UI 邊界。
- UI 功能：所有 Tk 狀態留在主執行緒，並補充生命週期或版面測試。
- 系統匣功能：callback 經 `tray_action_queue` 傳遞，不跨執行緒操作 Tk。
- 新增週期工作：保存 `after()` 回傳 ID，並納入 `_cancel_all_after_jobs()`。
- 若單一模組持續成長，可依目前邏輯邊界拆成 `app_server.py`、`models.py`、`tray.py` 與 `ui.py`；拆分前應先保留現有公開函式及測試行為。

## 已知邊界

- app-server method 與 payload 屬於 Codex CLI 整合面，升級 Codex CLI 後應執行相容性測試。
- 系統主題偵測目前以 Windows 實作為主，其他平台在無可用資訊時採淺色。
- Windows 有額外的視窗殘影 workaround；跨平台修改需至少分別驗證視窗關閉、右鍵選單與系統匣還原。
- 使用者偏好只保存在目前 process 記憶體中，重新啟動後回到系統語系、系統主題與 75% 不透明度。
