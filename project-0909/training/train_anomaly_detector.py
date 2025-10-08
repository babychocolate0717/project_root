# training/train_anomaly_detector.py (版本 3 - 加入台灣時間版本號)

import os
import pandas as pd
import joblib
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sklearn.ensemble import IsolationForest
from datetime import datetime
from zoneinfo import ZoneInfo  # <--- 新增：用於處理時區

# --- 設定 ---
load_dotenv()
DB_URL = os.getenv("DB_URL")

# 定義台灣時區
TAIWAN_TZ = ZoneInfo("Asia/Taipei")

# 用來訓練和判斷的特徵
FEATURES = [
    "gpu_power_watt",
    "cpu_power_watt",
    "memory_used_mb",
    "system_power_watt"
]

# --- 1. 讀取資料 ---
print("Connecting to database to fetch training data...")
engine = create_engine(DB_URL)
df = pd.read_sql(text(f"SELECT {', '.join(FEATURES)}, is_anomaly FROM energy_cleaned"), engine)

if df.empty:
    raise RuntimeError("No data found in energy_cleaned table for training.")

# 準備訓練用的乾淨資料 (過濾掉已知的異常)
df_train = df[df['is_anomaly'] == False][FEATURES]

# --- 2. 訓練模型 ---
print(f"Training IsolationForest model with {len(df_train)} records...")
model = IsolationForest(contamination=0.05, random_state=42)
model.fit(df_train)

# --- 3. 評估模型 ---
print("\n--- Model Evaluation ---")
predictions = model.predict(df[FEATURES])
df['anomaly_prediction'] = predictions
anomalies = df[df['anomaly_prediction'] == -1]
anomaly_rate = len(anomalies) / len(df) * 100

print(f"Total data points analyzed: {len(df)}")
print(f"Anomalies detected by model: {len(anomalies)} ({anomaly_rate:.2f}%)")

if not anomalies.empty:
    print("\n--- Examples of Detected Anomalies ---")
    print(anomalies.head())
else:
    print("\nNo anomalies were detected in the dataset.")

print("--------------------------\n")

# --- 4. 儲存模型 (使用包含台灣時間的版本號) ---
out_dir = Path(__file__).resolve().parents[1] / "models" / "anomaly_detection"
out_dir.mkdir(parents=True, exist_ok=True)

# 產生帶有日期和時間的版本號，例如：20251003_143055
version_tag = datetime.now(TAIWAN_TZ).strftime('%Y%m%d_%H%M%S') # <--- 修改

# 新的檔名
model_filename = f"anomaly_detector_{version_tag}.pkl"
model_path = out_dir / model_filename

joblib.dump(model, model_path)

print(f"✅ Anomaly detection model (version: {version_tag}) saved to: {model_path}")