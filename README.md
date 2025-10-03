# project_root
## 最新備份

### 看資料
* [API 文件 (Docs)](http://172.21.208.1:8000/docs)
* [儀表板總覽 (Dashboard)](http://172.21.208.1:8000/admin/dashboard)
* [所有設備列表 (All Devices)](http://172.21.208.1:8000/admin/devices-simple)
* **個別設備範例**: [設備 233497587702774](http://172.21.208.1:8000/admin/device/233497587702774)

# 能源監控專案檔案結構說明

## 總覽

此專案為一個完整的能源監控系統，其架構主要包含以下幾個部分：

1.  **Agent (代理程式)**：安裝於受監控電腦，負責收集硬體功耗數據並回報。
2.  **Ingestion API (資料接收 API)**：後端服務，接收 Agent 數據，進行驗證並存入資料庫。
3.  **Cleaning API (資料清洗 API)**：一個獨立的微服務，專門處理資料清洗與格式化。
4.  **Prediction API (預測 API)**：利用機器學習模型預測未來功耗與碳排放。
5.  **Training (模型訓練)**：用於訓練預測模型。
6.  **Docker & 部署設定**: 整合並部署整個系統的相關設定檔。

---

## `agent/` - 資料收集代理程式

在使用者電腦上執行的程式，用來收集功耗數據。

* `integrated_agent.py`: **核心 Agent 程式**。整合了資料收集 (CPU, GPU, 記憶體)、功耗計算、資料清洗、配額管理、使用者活動偵測以及將數據傳送到 Ingestion API 的所有功能。
* `agent_with_auth.py`: 舊版的 Agent 程式，具備身分驗證功能。
* `agent_with_auth_chinese_backup.py`: `agent_with_auth.py` 的中文註解備份版本。
* `config.yaml`: **重要設定檔**。用於配置 API 位址、認證金鑰、資料收集間隔等參數。
* `build_agent_exe.bat`: Windows 批次檔，用於將 Python 腳本 (`integrated_agent.py`) 打包成獨立的 `.exe` 執行檔。
* `EnergyAgent.bat` / `start_agent.bat`: 用於啟動 Agent 程式的批次檔。
* `EnergyAgent.spec` / `EnergyMonitorAgent.spec`: PyInstaller 的設定檔，定義了如何打包 `.exe` 執行檔的詳細規則。
* `setup_energy_agent_service.ps1`: PowerShell 腳本，用於將 Agent 安裝成 Windows 系統服務，使其能在背景自動運行。
* `nssm_diagnostics.ps1`: 用於診斷 Windows 服務 (使用 NSSM 工具) 狀態的 PowerShell 腳本。
* `test_agent.py`: 一個簡化的測試腳本，用於快速驗證 Agent 的基本功能是否正常。

---

## `ingestion-api/` - 資料接收 API

後端伺服器的主要入口，負責接收、處理並儲存 Agent 傳來的數據。

* `app/main.py`: **API 核心檔案**。使用 FastAPI 框架，定義了所有 API 端點 (endpoint)，例如：
    * `/ingest`: 接收 Agent 傳來的能耗資料。
    * `/admin/*`: 提供後台管理功能，如查看設備列表、儀表板等。
    * `/health`: 健康檢查端點。
* `app/auth.py`: **身分驗證與安全模組**。處理設備的認證與授權，包含 MAC 位址白名單驗證、設備憑證檢查以及設備指紋辨識。
* `app/models.py`: **資料庫模型定義**。使用 SQLAlchemy 定義資料庫中的資料表結構，包括 `EnergyRaw` (原始資料)、`EnergyCleaned` (清洗後資料)、`AuthorizedDevice` (授權設備) 等。
* `app/schemas.py`: **資料格式定義**。使用 Pydantic 定義 API 請求與回應的資料結構，確保資料的一致性與有效性。
* `app/database.py`: 設定與 PostgreSQL 資料庫的連線。
* `app/utils/mac_manager.py`: 提供管理設備 MAC 位址白名單的功能模組。
* `Dockerfile`: Docker 設定檔，定義如何建構此 API 的 Docker image。
* `requirements.txt`: 列出此 API 運作所需的 Python 套件。
* `init.sql`: **資料庫初始化腳本**。在首次啟動時，自動建立所有必要的資料表與索引。

---

## `cleaning-api/` - 資料清洗 API

一個獨立的微服務，專門負責資料的清洗與標準化。

* `app/main.py`: API 核心檔案，定義 `/clean` 端點，接收原始資料並回傳清洗後的結果。
* `app/cleaning.py`: **主要清洗邏輯**。包含實際的資料清洗函式，例如處理遺失值、轉換資料型態、將 "unknown" GPU 型號標準化為 "Generic GPU"。
* `app/schemas.py`: 定義此 API 所接收的原始資料格式。
* `Dockerfile`: Docker 設定檔，定義如何建構此 API 的 Docker image。
* `requirements.txt`: 列出此 API 所需的 Python 套件。

---

## `prediction-api/` - 功耗預測 API

使用機器學習模型，根據歷史資料預測未來的功耗與碳排放。

* `main.py`: **API 核心檔案**。定義了多個 API 端點，例如：
    * `/health`: 檢查服務狀態。
    * `/metrics/latest`: 取得最新的預測與實際數據。
    * `/emissions/range`: 取得指定時間範圍內的歷史預測資料。
    * `/compare/segments`: 依功耗高低分段比較，分析模型誤差。
    * 此服務會在背景定期執行一個循環任務 (`loop_job`)，持續從資料庫抓取最新資料並產生新的預測值。
* `requirements.txt`: 列出此 API 所需的 Python 套件，包含 `tensorflow`, `pandas` 等。

---

## 模型訓練相關

* `training/train_lstm_from_db.py`: **模型訓練腳本**。此腳本會從資料庫 (`energy_cleaned` 表) 讀取歷史功耗資料，訓練一個 LSTM (長短期記憶) 神經網路模型。
* `models/lstm_carbon_model.keras`: 訓練完成後產生的**模型檔案**。
* `models/scaler_power.pkl`: 訓練過程中產生的**資料正規化工具**，用於將功耗數據縮放到 0-1 之間，是模型預測的關鍵前置步驟。

---

## 根目錄與部署檔案

* `docker-compose.yml`: **核心部署檔案**。使用 Docker Compose 來定義並一次性啟動整個專案的所有服務 (資料庫 `db`、`ingestion` API、`cleaner` API)。它管理了各服務之間的相依性、網路、環境變數等。
* `README.md`: 專案的說明文件，提供 API 文件、儀表板等快速連結。
