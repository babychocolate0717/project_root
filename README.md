# project_root
## 最新備份

### 看資料
* [API 文件 (Docs)](http://172.21.208.1:8000/docs)
* [儀表板總覽 (Dashboard)](http://172.21.208.1:8000/admin/dashboard)
* [所有設備列表 (All Devices)](http://172.21.208.1:8000/admin/devices-simple)
* **個別設備範例**: [設備 233497587702774](http://172.21.208.1:8000/admin/device/233497587702774)

### **根目錄**

* `README.md`: 專案的總體說明文件，提供了整個專案的架構概覽，包含各個服務 (Agent, API) 的介紹、檔案結構說明以及快速連結。
* `docker-compose.yml`: 核心部署檔案，使用 Docker Compose 來定義並一次性啟動整個專案的所有服務，包括資料庫 (`db`)、資料接收 API (`ingestion`) 和資料清洗 API (`cleaner`)。

### **`agent/` - 資料收集代理程式**

Agent 是在受監控的電腦上執行的程式，負責收集功耗數據並傳送到後端。

* `integrated_agent.py`: **核心 Agent 程式**。整合了資料收集 (CPU, GPU, 記憶體)、功耗計算、資料清洗、配額管理、使用者活動偵測以及將數據傳送到 Ingestion API 的所有功能。
* `config.yaml`: **重要設定檔**。用於配置 API 的位址、認證金鑰、資料收集間隔等參數。
* `build_agent_exe.bat`: Windows 批次檔，用於將 Python 腳本 (`integrated_agent.py`) 打包成獨立的 `.exe` 執行檔。
* `EnergyMonitorAgent.spec`: PyInstaller 的設定檔，定義了如何打包 `.exe` 執行檔的詳細規則。
* `agent_error.log` / `logs/agent_error.log`: 記錄 Agent 執行時發生的錯誤，主要是 `UnicodeEncodeError`。
* `logs/agent_output.log`: 記錄 Agent 執行時的標準輸出訊息。
* `logs/startup.log`: 記錄 Agent 啟動的時間。
* `build/EnergyMonitorAgent/warn-EnergyMonitorAgent.txt`: PyInstaller 打包過程中，記錄找不到的模組警告。
* `build/EnergyMonitorAgent/xref-EnergyMonitorAgent.html`: PyInstaller 產生的交叉參照檔案，顯示模組間的依賴關係。

### **`ingestion-api/` - 資料接收 API**

後端伺服器的主要入口，負責接收、驗證、儲存從 Agent 傳來的數據。

* `app/main.py`: **API 核心檔案**。使用 FastAPI 框架，定義了所有 API 端點 (endpoint)，例如 `/ingest` (接收能耗資料) 和後台管理相關的端點。
* `app/auth.py`: **身分驗證與安全模組**。處理設備的認證與授權，包含 MAC 位址白名單驗證、設備憑證檢查以及設備指紋辨識。
* `app/models.py`: **資料庫模型定義**。使用 SQLAlchemy 定義資料庫中的資料表結構，包括 `EnergyRaw` (原始資料)、`EnergyCleaned` (清洗後資料)、`AuthorizedDevice` (授權設備) 等。
* `app/schemas.py`: **資料格式定義**。使用 Pydantic 定義 API 請求與回應的資料結構。
* `app/database.py`: 設定與 PostgreSQL 資料庫的連線。
* `app/utils/mac_manager.py`: 提供管理設備 MAC 位址白名單的功能模組。
* `Dockerfile`: Docker 設定檔，定義如何建構此 API 的 Docker image。
* `requirements.txt`: 列出此 API 運作所需的 Python 套件。
* `init.sql`: **資料庫初始化腳本**。在首次啟動時，自動建立所有必要的資料表與索引。

### **`cleaning-api/` - 資料清洗 API**

一個獨立的微服務，專門負責資料的清洗與標準化。

* `app/main.py`: API 核心檔案，定義 `/clean` 端點，接收原始資料並回傳清洗後的結果。
* `app/cleaning.py`: **主要清洗邏輯**。包含實際的資料清洗函式，例如處理遺失值、轉換資料型態、將 "unknown" GPU 型號標準化為 "Generic GPU"。
* `app/schemas.py`: 定義此 API 所接收的原始資料格式。
* `Dockerfile`: Docker 設定檔，定義如何建構此 API 的 Docker image。
* `requirements.txt`: 列出此 API 所需的 Python 套件。

### **`prediction-api/` - 功耗預測 API**

使用機器學習模型，根據歷史資料預測未來的功耗與碳排放。

* `main.py`: **API 核心檔案**。定義了多個 API 端點，例如 `/metrics/latest` (取得最新的預測與實際數據)、`/emissions/range` (取得指定時間範圍內的歷史預測資料) 等，並會在背景定期執行預測任務。
* `requirements.txt`: 列出此 API 所需的 Python 套件，包含 `tensorflow`, `pandas` 等。

### **模型訓練相關 (`training/` & `models/`)**

* `training/train_lstm_from_db.py`: **模型訓練腳本**。此腳本會從資料庫 (`energy_cleaned` 表) 讀取歷史功耗資料，訓練一個 LSTM (長短期記憶) 神經網路模型。
* `models/lstm_carbon_model.keras`: 訓練完成後產生的**模型檔案**。
* `models/scaler_power.pkl`: 訓練過程中產生的**資料正規化工具**，用於將功耗數據縮放到 0-1 之間，是模型預測的關鍵前置步驟。

### **`data/` - 範例資料**

* `data_log.json`: 一個 JSON 格式的日誌檔案，包含多筆從 Agent 收集到的能耗數據範例。
