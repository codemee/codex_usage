# 專案規範

## 使用 uv 管理 Python 環境

專案中需要使用 Python 時：

- 一律使用 uv 建置 Python 環境，不要使用其他工具
- 一律將 Python 安裝到 uv 的全域環境中，不要裝到專案內
- 若未指明，請使用 Python 3.13
- 必要時使用 `uv init` 初始化專案
- 使用 `uv add/remove` 管理套件
- 使用 `uv run` 執行 Python 腳本檔