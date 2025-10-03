import os
import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, mean_absolute_percentage_error
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense
from tensorflow.keras.callbacks import EarlyStopping
from urllib.parse import urlparse

# ========= 先載入 .env，再取環境變數 =========
load_dotenv()

DB_URL   = os.getenv("DB_URL")  # e.g. postgresql+psycopg2://user:password@localhost:5433/energy
STEP_MIN = int(os.getenv("STEP_MINUTES", "1"))   # 你的資料實際約 1 分鐘一筆，預設設 1
WINDOW   = int(os.getenv("WINDOW", "72"))
EPOCHS   = int(os.getenv("EPOCHS", "20"))
BATCH    = int(os.getenv("BATCH", "128"))

# 防呆：印出 DB_URL（遮擋密碼）
u = urlparse(DB_URL)
print("Using DB_URL:", f"{u.scheme}://{u.username}:******@{u.hostname}:{u.port}{u.path}")

# ========= 欄位名稱（內部統一） =========
TIME_COL = "timestamp"
PWR_COL  = "system_power_watt"

# ========= SQL（直接把 ISO8601 Z 的 timestamp_utc 轉 timestamptz，並取別名 timestamp） =========
SQL = f"""
SELECT
  (timestamp_utc)::timestamptz AS {TIME_COL},
  {PWR_COL}
FROM energy_cleaned
WHERE timestamp_utc IS NOT NULL
  AND {PWR_COL} IS NOT NULL
ORDER BY (timestamp_utc)::timestamptz
"""
print("DEBUG SQL:\n", SQL)

# ========= 連 DB 取資料 =========
engine = create_engine(DB_URL, pool_pre_ping=True)
df = pd.read_sql(text(SQL), engine, parse_dates=[TIME_COL])

if df.empty:
    raise RuntimeError("energy_cleaned 查無資料，請確認資料表或條件。")

# ========= 時間對齊與補值 =========
df = df.set_index(TIME_COL).sort_index()
rule = f"{STEP_MIN}min"
df = df.resample(rule).mean()
df[PWR_COL] = df[PWR_COL].ffill().bfill()
df = df.dropna(subset=[PWR_COL])

# ========= 正規化與建序列 =========
power = df[PWR_COL].astype(float).values.reshape(-1, 1)
scaler = MinMaxScaler()
power_scaled = scaler.fit_transform(power).flatten()

def make_sequences(arr, window):
    X, y = [], []
    for i in range(len(arr) - window - 1):
        X.append(arr[i:i+window])
        y.append(arr[i+window])   # 1-step ahead
    X = np.array(X).reshape(-1, window, 1)
    y = np.array(y).reshape(-1, 1)
    return X, y

X, y = make_sequences(power_scaled, WINDOW)
if len(X) == 0:
    raise RuntimeError("資料不足以形成序列，請減少 WINDOW 或確認資料量。")

split = int(len(X) * 0.8)
X_tr, X_val = X[:split], X[split:]
y_tr, y_val = y[:split], y[split:]

# ========= 建模與訓練 =========
model = Sequential([LSTM(64, input_shape=(WINDOW, 1)), Dense(1)])
model.compile(optimizer="adam", loss="mse")

es = EarlyStopping(patience=3, restore_best_weights=True)
model.fit(
    X_tr, y_tr,
    validation_data=(X_val, y_val),
    epochs=EPOCHS, batch_size=BATCH,
    callbacks=[es], verbose=1
)

# ========= 評估（反標準化） =========
y_pred = model.predict(X_val, verbose=0)
y_val_w = scaler.inverse_transform(y_val)
y_pred_w = scaler.inverse_transform(y_pred)

rmse = np.sqrt(mean_squared_error(y_val_w, y_pred_w))
mape = mean_absolute_percentage_error(y_val_w, y_pred_w) * 100
print(f"RMSE(W): {rmse:.2f} | MAPE(%): {mape:.2f}")

# ========= 輸出模型與 Scaler =========
out_dir = Path(__file__).resolve().parents[1] / "models"
out_dir.mkdir(parents=True, exist_ok=True)
model.save(out_dir / "lstm_carbon_model.keras") 
joblib.dump(scaler, out_dir / "scaler_power.pkl")
print("✅ Saved:", out_dir / "lstm_carbon_model.h5", "and", out_dir / "scaler_power.pkl")
