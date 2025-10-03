# training/train_anomaly_detector.py 模型IsolationForest (孤立森林)

import os
import pandas as pd
import joblib
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sklearn.ensemble import IsolationForest

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

# 從資料庫讀取數據
print("Connecting to database to fetch training data...")
engine = create_engine(DB_URL)
df = pd.read_sql(text(f"SELECT {', '.join(FEATURES)} FROM energy_cleaned"), engine)

if df.empty:
    raise RuntimeError("No data found in energy_cleaned table for training.")

# 初始化並訓練模型
print(f"Training IsolationForest model with {len(df)} records...")
model = IsolationForest(contamination='auto', random_state=42)
model.fit(df[FEATURES])

# 儲存模型
out_dir = Path(__file__).resolve().parents[1] / "models"
out_dir.mkdir(exist_ok=True)
model_path = out_dir / "anomaly_detector.pkl"
joblib.dump(model, model_path)

print(f"✅ Anomaly detection model saved to: {model_path}")