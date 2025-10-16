# main.py  ← 直接覆蓋這份

import os, asyncio, datetime as dt
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import joblib
from dotenv import load_dotenv
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from tensorflow.keras.models import load_model

# ---------- env & config ----------
load_dotenv()  # 先讀 .env

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL / DB_URL 未設定，請在 prediction-api/.env 或環境變數補上。")

LOOKBACK_MIN = int(os.getenv("BATCH_LOOKBACK_MINUTES", "720"))  # 往回抓幾分鐘的歷史（預設 12 小時）
STEP_MIN     = int(os.getenv("STEP_MINUTES", "1"))              # 你的資料頻率 ~1 分鐘，預設 1
MODEL_VERSION= os.getenv("MODEL_VERSION", "lstm_v1")
RUN_INTERVAL = int(os.getenv("RUN_INTERVAL_SECONDS", "3600"))   # 背景任務執行間隔（秒）
EF           = float(os.getenv("EF", "0.502"))                  # 台電 2023 係數 (kgCO2e/kWh)
WINDOW       = int(os.getenv("WINDOW", "72"))                   # 觀測窗長度（以 STEP_MIN 為單位）
DELTA_HR     = STEP_MIN / 60.0                                  # 每步等於幾小時

u = urlparse(DATABASE_URL)
print("Using DATABASE_URL:", f"{u.scheme}://{u.username}:******@{u.hostname}:{u.port}{u.path}")
print(f"STEP_MIN={STEP_MIN}, WINDOW={WINDOW}, LOOKBACK_MIN={LOOKBACK_MIN}, EF={EF}, MODEL_VERSION={MODEL_VERSION}")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# ---------- ensure pred table ----------
def ensure_pred_table(engine):
    sql = text("""
    CREATE TABLE IF NOT EXISTS carbon_emissions_pred (
      timestamp_from     timestamptz NOT NULL,
      timestamp_to       timestamptz NOT NULL,
      horizon_steps      integer      NOT NULL,
      predicted_power_w  double precision NOT NULL,
      predicted_co2_kg   double precision NOT NULL,
      model_version      text         NOT NULL,
      created_at         timestamptz  NOT NULL DEFAULT now(),
      PRIMARY KEY (timestamp_to, model_version)
    );
    """)
    with engine.begin() as conn:
        conn.execute(sql)

ensure_pred_table(engine)

# ---------- load model & scaler ----------
models_dir = Path(__file__).resolve().parents[1] / "models"
keras_path = models_dir / "lstm_carbon_model.keras"
h5_path    = models_dir / "lstm_carbon_model.h5"
scaler_path= models_dir / "scaler_power.pkl"

model_path = keras_path if keras_path.exists() else h5_path
if not model_path.exists():
    raise FileNotFoundError(f"找不到模型檔：{keras_path} 或 {h5_path}")

if not scaler_path.exists():
    raise FileNotFoundError(f"找不到 scaler 檔：{scaler_path}")

print(f"Loading model: {model_path.name}")
model  = load_model(model_path, compile=False)
scaler = joblib.load(scaler_path)

app = FastAPI(title="Prediction API (LSTM → Carbon)")

# ---------- data access & preprocessing ----------
def fetch_power_series(end_ts: dt.datetime, minutes: int) -> pd.DataFrame:
    """
    從 energy_cleaned 取回 [end_ts - minutes, end_ts] 的資料，
    將 timestamp_utc 轉成 timestamptz，重採樣成 STEP_MIN 粒度並補值。
    回傳欄位：timestamp (datetime), system_power_watt (float)
    """
    start_ts = end_ts - dt.timedelta(minutes=minutes)

    # 直接在 SQL 將 varchar → timestamptz 並命名為 ts
    sql = text("""
        SELECT
          (timestamp_utc)::timestamptz AS ts,
          system_power_watt
        FROM energy_cleaned
        WHERE timestamp_utc IS NOT NULL
          AND system_power_watt IS NOT NULL
          AND (timestamp_utc)::timestamptz >  :start
          AND (timestamp_utc)::timestamptz <= :end
        ORDER BY (timestamp_utc)::timestamptz
    """)

    with engine.connect() as conn:
        raw = pd.read_sql(sql, conn, params={"start": start_ts, "end": end_ts}, parse_dates=["ts"])

    if raw.empty:
        return raw.rename(columns={"ts": "timestamp"})

    # 設索引、重採樣為 STEP_MIN 分鐘均值（若單點/不等頻，重採樣能對齊網格）
    df = raw.rename(columns={"ts": "timestamp"}).set_index("timestamp").sort_index()
    rule = f"{STEP_MIN}min"
    df = df.resample(rule).mean()

    # 前向/後向補值，避免短暫缺漏
    df["system_power_watt"] = df["system_power_watt"].ffill().bfill()

    # 乾淨輸出（還原索引做成欄位）
    df = df.reset_index()
    return df

def predict_next_power_w(df: pd.DataFrame) -> float:
    """
    取最後 WINDOW 步的 system_power_watt，經 scaler → LSTM → inverse_transform，得到下一步功率(W)。
    """
    power = df["system_power_watt"].astype(float).values
    if len(power) < WINDOW:
        raise ValueError(f"需要至少 {WINDOW} 筆資料（當前 {len(power)}），請增加 LOOKBACK_MIN 或降低 WINDOW。")

    last_window = power[-WINDOW:].reshape(-1, 1)             # (WINDOW, 1)
    last_scaled = scaler.transform(last_window).reshape(1, WINDOW, 1)
    y_scaled    = model.predict(last_scaled, verbose=0)      # (1, 1)
    y_watt      = scaler.inverse_transform(y_scaled).flatten()[0]
    return float(y_watt)

def upsert_carbon_emission(ts_from, ts_to, steps, pw, co2):
    """
    將預測結果寫入 carbon_emissions_pred（若同一 timestamp_to+model_version 已存在則更新）。
    """
    sql = text("""
        INSERT INTO carbon_emissions_pred
        (timestamp_from, timestamp_to, horizon_steps, predicted_power_w, predicted_co2_kg, model_version)
        VALUES (:from, :to, :h, :pw, :co2, :mv)
        ON CONFLICT (timestamp_to, model_version) DO UPDATE
        SET predicted_power_w = EXCLUDED.predicted_power_w,
            predicted_co2_kg  = EXCLUDED.predicted_co2_kg,
            timestamp_from    = EXCLUDED.timestamp_from,
            horizon_steps     = EXCLUDED.horizon_steps;
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "from": ts_from, "to": ts_to, "h": steps,
            "pw": pw, "co2": co2, "mv": MODEL_VERSION
        })

# ---------- background loop ----------
async def loop_job():
    while True:
        now = dt.datetime.utcnow()  # 使用 UTC 與 timestamptz 對齊
        try:
            df = fetch_power_series(now, LOOKBACK_MIN)
            if df.empty:
                print(f"[{now.isoformat()}Z] No data in lookback window ({LOOKBACK_MIN} min).")
            else:
                pred_power_w = predict_next_power_w(df)
                kWh = (pred_power_w / 1000.0) * DELTA_HR
                co2 = kWh * EF

                ts_from = now
                ts_to   = now + dt.timedelta(minutes=STEP_MIN)
                upsert_carbon_emission(ts_from, ts_to, 1, pred_power_w, co2)

                print(f"[{now.isoformat()}Z] Pred={pred_power_w:.2f} W → kWh={kWh:.6f} → CO2={co2:.6f} kg")
        except Exception as e:
            print(f"[{now.isoformat()}Z] Job error:", repr(e))

        await asyncio.sleep(RUN_INTERVAL)

# ---------- FastAPI endpoints ----------
app = FastAPI(title="Prediction API (LSTM → Carbon)")

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(loop_job())

@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "step_minutes": STEP_MIN,
        "window": WINDOW,
        "lookback_min": LOOKBACK_MIN
    }

@app.post("/run-once")
def run_once():
    now = dt.datetime.utcnow()
    df = fetch_power_series(now, LOOKBACK_MIN)
    if df.empty:
        return {"ok": False, "msg": f"no data in last {LOOKBACK_MIN} minutes"}

    pred_power_w = predict_next_power_w(df)
    kWh = (pred_power_w / 1000.0) * DELTA_HR
    co2 = kWh * EF

    ts_from = now
    ts_to   = now + dt.timedelta(minutes=STEP_MIN)
    upsert_carbon_emission(ts_from, ts_to, 1, pred_power_w, co2)

    return {
        "ok": True,
        "predicted_power_w": pred_power_w,
        "step_minutes": STEP_MIN,
        "predicted_kWh": kWh,
        "predicted_co2_kg": co2,
        "model_version": MODEL_VERSION,
        "window": WINDOW
    }
