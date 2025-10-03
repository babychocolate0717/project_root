# training/train_anomaly_detector.py

import os
import pandas as pd
import joblib
from pathlib import Path  # <--- 修正：新增了這一行
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sklearn.ensemble import IsolationForest
from datetime import datetime # <--- 修正：新增了這一行 (為了版本控制)

# 載入環境變數
load_dotenv()
DB_URL = os.getenv("DB_URL")

# 選擇用來判斷異常的特徵欄位
FEATURES = [
    "gpu_power_watt",
    "cpu_power_watt",
    "memory_used_mb",
    "system_power_watt"
]

# 從資料庫讀取數據 (只讀取非異常的資料)
print("Connecting to database to fetch training data...")
engine = create_engine(DB_URL)
df = pd.read_sql(text(f"SELECT {', '.join(FEATURES)} FROM energy_cleaned WHERE is_anomaly = false"), engine)

if df.empty:
    raise RuntimeError("No data found in energy_cleaned table for training.")

# 初始化並訓練模型
print(f"Training IsolationForest model with {len(df)} records...")
model = IsolationForest(contamination='auto', random_state=42)
model.fit(df[FEATURES])

# 儲存模型 (加入版本號)
# 建立 models/anomaly_detection/ 資料夾
out_dir = Path(__file__).resolve().parents[1] / "models" / "anomaly_detection" # <--- 修改
out_dir.mkdir(exist_ok=True)

# 產生帶有日期的版本號
version_tag = datetime.now().strftime('%Y%m%d')
model_filename = f"anomaly_detector_{version_tag}.pkl"
model_path = out_dir / model_filename

joblib.dump(model, model_path)

print(f"✅ Anomaly detection model (version: {version_tag}) saved to: {model_path}")