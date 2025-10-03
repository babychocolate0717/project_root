import os, asyncio, datetime as dt
import json
from pathlib import Path
from typing import Dict, Any, List

import numpy as np
import pandas as pd
import joblib
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text, event 
from sqlalchemy.exc import SQLAlchemyError
from tensorflow.keras.models import load_model

# --------------------
# 環境變數 (Environment Variables)
# --------------------
load_dotenv()
# 核心設定
DATABASE_URL            = os.getenv("DATABASE_URL")
STEP_MIN                = int(os.getenv("STEP_MINUTES", "1"))
WINDOW                  = int(os.getenv("WINDOW", "72"))
LOOKBACK_MIN            = int(os.getenv("BATCH_LOOKBACK_MINUTES", "720"))
RUN_INTERVAL_SECONDS    = int(os.getenv("RUN_INTERVAL_SECONDS", "60"))
EF                      = float(os.getenv("EF", "0.502"))
MODEL_VERSION           = os.getenv("MODEL_VERSION", "lstm_v1")

# 資料表設定
TABLE_ENERGY            = os.getenv("TABLE_ENERGY", "energy_cleaned")
TIME_COL_SOURCE         = os.getenv("TIME_COL_SOURCE", "timestamp_utc")
PWR_COL                 = os.getenv("PWR_COL", "system_power_watt")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 未設定")

print(f"Using DATABASE_URL: {DATABASE_URL.split('@')[-1]}")
print(f"STEP_MIN={STEP_MIN}, WINDOW={WINDOW}, LOOKBACK_MIN={LOOKBACK_MIN}, EF={EF}, MODEL_VERSION={MODEL_VERSION}")

# --------------------
# DB 連線 (Database Connection)
# --------------------
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)

# --------------------
# 載入模型與 scaler (Load Model and Scaler)
# --------------------
models_dir = Path(__file__).resolve().parents[1] / "models"
model_path = models_dir / "lstm_carbon_model.keras"
scaler_path= models_dir / "scaler_power.pkl"

if not model_path.exists() or not scaler_path.exists():
    raise FileNotFoundError("找不到模型檔或 scaler 檔")

print(f"Loading model: {model_path.name}")
model  = load_model(model_path, compile=False)
scaler = joblib.load(scaler_path)

# --------------------
# FastAPI app
# --------------------
app = FastAPI(title="Prediction API (LSTM → Carbon)")


# --------------------
# Pydantic 模型定義 (Pydantic Models) - 新增 kWh 和誤差指標
# --------------------

class KPIMetric(BaseModel):
    timestamp: dt.datetime
    actual_power_w: float | None = None
    predicted_power_w: float | None = None
    actual_co2_kg: float | None = None
    predicted_co2_kg: float | None = None
    actual_kwh: float | None = None
    predicted_kwh: float | None = None
    # ---> 新增誤差指標欄位 <---
    avg_mae_w: float | None = None
    avg_rmse_w: float | None = None
    avg_mape_w: float | None = None


class AggregatedSegment(BaseModel):
    band: str
    MAE_W: float
    RMSE_W: float
    MAPE_W: float
    pred_co2_sum: float
    actual_co2_sum: float

class SegmentComparison(BaseModel):
    segments: List[AggregatedSegment]
    thresholds: Dict[str, float]

class TimeseriesData(BaseModel):
    data: List[KPIMetric]

# --------------------
# 輔助函式 (Utility Functions)
# --------------------

def db_ok() -> bool:
    """檢查資料庫連線是否正常"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False

def floor_to_step(dt_obj: dt.datetime, step_min: int) -> dt.datetime:
    """將 datetime 物件向下取整到 STEP_MIN 的倍數分鐘 (精確對齊)"""
    if step_min <= 0: return dt_obj
        
    delta = dt.timedelta(minutes=dt_obj.minute % step_min, 
                         seconds=dt_obj.second, 
                         microseconds=dt_obj.microsecond)
    return dt_obj - delta

def get_power_thresholds() -> Dict[str, float]:
    """從資料庫計算用電量 P20, P80 門檻 (使用過去 30 天數據)"""
    sql = text(f"""
        SELECT
            percentile_cont(0.2) WITHIN GROUP (ORDER BY {PWR_COL}) AS p20,
            percentile_cont(0.8) WITHIN GROUP (ORDER BY {PWR_COL}) AS p80
        FROM {TABLE_ENERGY}
        WHERE ({TIME_COL_SOURCE})::timestamptz >= NOW() - INTERVAL '30 days'
          AND {PWR_COL} IS NOT NULL;
    """)
    try:
        with engine.connect() as conn:
            result = conn.execute(sql).first()
            if result and result.p20 is not None and result.p80 is not None:
                return {"p20": float(result.p20), "p80": float(result.p80)}
            return {"p20": 100.0, "p80": 400.0}
    except SQLAlchemyError as e:
        print(f"Error fetching thresholds: {e}")
        return {"p20": 100.0, "p80": 400.0}


def recommend_strategy(pred_w: float, band_thresholds: dict) -> Dict[str, Any]:
    """根據預測功耗回傳結構化的策略建議 (字典/JSON)"""
    p80 = band_thresholds.get("p80", 400.0)
    p20 = band_thresholds.get("p20", 100.0)

    if pred_w >= p80:
        return {
            "load_level": "HIGH",
            "summary": "高負載預測：建議立即採取節能措施。",
            "recommendations": [
                "限制 GPU 的功耗上限",
                "將批次計算任務重新排程至離峰時段",
                "終止非必要的背景程式"
            ]
        }
    elif pred_w <= p20:
        return {
            "load_level": "LOW",
            "summary": "低功耗預測：適合執行耗時任務。",
            "recommendations": [
                "可以開始執行資料備份或模型訓練等批次任務",
                "執行系統更新與維護"
            ]
        }
    else:
        return {
            "load_level": "MID",
            "summary": "中等功耗預測：持續監控即可。",
            "recommendations": [
                "無需特別操作，維持正常監控"
            ]
        }
        
def to_series(df: pd.DataFrame, gran: str) -> pd.DataFrame:
    """將聯合數據 (joined data) 依時間粒度聚合：minute/hour/day"""
    if df.empty:
        return pd.DataFrame()
        
    rule = {"minute":"T","hour":"H","day":"D"}[gran]
    
    # *** 修正 1: 新增聚合誤差指標 ***
    agg_funcs = {
        "actual_power_w":"mean",
        "predicted_power_w":"mean",
        "actual_kwh":"sum",
        "predicted_kwh":"sum",
        "actual_co2_kg":"sum",
        "predicted_co2_kg":"sum",
        "abs_err":"mean",        # MAE
        "sq_err": lambda s: (s.mean())**0.5, # RMSE (使用自定義 lambda 函式)
        "ape":"mean"             # MAPE
    }

    g = (df.set_index("ts")
            .resample(rule)
            .agg(agg_funcs)
            .reset_index())
            
    g = g.rename(columns={"ts": "timestamp",
                          "abs_err": "avg_mae_w",
                          "sq_err": "avg_rmse_w",
                          "ape": "avg_mape_w"}) # 重命名為最終輸出的欄位名稱
    
    # 確保最終輸出包含所有新的欄位
    output_cols = ["timestamp", "actual_power_w", "predicted_power_w", 
                   "actual_co2_kg", "predicted_co2_kg", 
                   "actual_kwh", "predicted_kwh",
                   "avg_mae_w", "avg_rmse_w", "avg_mape_w"]
    
    return g[output_cols]


def load_joined_range(start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    """
    從 DB 載入指定時間範圍內，實際測量與模型預測的聯合數據。
    *** 關鍵修正：確保 Pandas 正確處理時區，防止數據丟失 ***
    """
    sql = text("""
      WITH meas AS (
        SELECT (timestamp_utc)::timestamptz AS ts,
               system_power_watt AS actual_power_w
        FROM energy_cleaned
        WHERE (timestamp_utc)::timestamptz >= :s AND (timestamp_utc)::timestamptz < :e
      ),
      pred AS (
        SELECT timestamp_to AS ts,
               predicted_power_w,
               predicted_co2_kg
        FROM carbon_emissions
        WHERE timestamp_to >= :s AND timestamp_to < :e
        AND model_version = :mv
      )
      SELECT coalesce(m.ts, p.ts) AS ts,
             m.actual_power_w,
             p.predicted_power_w,
             -- 換算 kWh 與 CO2
             (m.actual_power_w/1000.0)*(:step/60.0) AS actual_kwh,
             (p.predicted_power_w/1000.0)*(:step/60.0) AS predicted_kwh,
             (m.actual_power_w/1000.0)*(:step/60.0)*:ef AS actual_co2_kg,
             p.predicted_co2_kg
      FROM meas m
      FULL OUTER JOIN pred p ON m.ts = p.ts
      ORDER BY 1
    """)
    try:
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"s": start, "e": end, "ef": EF, "step": STEP_MIN, "mv": MODEL_VERSION})
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
        
    # --- 關鍵修復區域 ---
    if not df.empty and 'ts' in df.columns:
        # 1. 確保 ts 是 datetime 類型
        df['ts'] = pd.to_datetime(df['ts'])
        # 2. 如果帶有時區資訊 (coalesce 輸出可能帶)，則轉為 UTC 並去除時區資訊，確保對齊
        if df['ts'].dt.tz is not None:
             df['ts'] = df['ts'].dt.tz_convert('UTC').dt.tz_localize(None)
    # --- 關鍵修復結束 ---
    
    # --- Pandas 計算單點誤差 ---
    df = df.dropna(subset=['actual_power_w', 'predicted_power_w']).copy()
    
    # 避免除以零的 MAPE
    actual_power = df["actual_power_w"].replace(0, pd.NA) 

    df["abs_err"] = (df["predicted_power_w"] - df["actual_power_w"]).abs()
    df["sq_err"]  = (df["predicted_power_w"] - df["actual_power_w"])**2
    df["ape"]     = (df["abs_err"] / actual_power) * 100

    return df

# --------------------
# 數據處理與模型預測 (Data Processing & Prediction)
# --------------------

def fetch_power_series(end_ts_utc: dt.datetime, minutes: int) -> pd.DataFrame:
    """從 energy_cleaned 取回數據並重採樣"""
    start_ts = end_ts_utc - dt.timedelta(minutes=minutes)
    sql = text(f"""
        SELECT
          ({TIME_COL_SOURCE})::timestamptz AS ts,
          {PWR_COL} AS power_w
        FROM {TABLE_ENERGY}
        WHERE {TIME_COL_SOURCE} IS NOT NULL
          AND {PWR_COL} IS NOT NULL
          AND ({TIME_COL_SOURCE})::timestamptz > :start
          AND ({TIME_COL_SOURCE})::timestamptz <= :end
        ORDER BY ts
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"start": start_ts, "end": end_ts_utc}, parse_dates=["ts"])

    if df.empty:
        return df

    df = df.set_index("ts").sort_index()
    # 等間距重採樣 (Re-sample)
    rule = f"{STEP_MIN}min"
    df = df.resample(rule).mean()
    # 缺值補齊 (Handle missing data)
    df["power_w"] = df["power_w"].ffill().bfill()
    df = df.dropna(subset=["power_w"])
    return df


def predict_next_power_w(df_aligned: pd.DataFrame) -> float:
    """用最後 WINDOW 筆做單步預測 (包含資料量檢查)"""
    arr = df_aligned["power_w"].values.astype(float)
    if len(arr) < WINDOW:
        raise ValueError(f"資料不足：需要至少 {WINDOW} 筆數據 (當前 {len(arr)})")
        
    last_window = arr[-WINDOW:].reshape(-1, 1)
    # 預處理、預測、反向轉換
    last_scaled = scaler.transform(last_window).reshape(1, WINDOW, 1)
    y_scaled    = model.predict(last_scaled, verbose=0)
    y_watt      = scaler.inverse_transform(y_scaled).flatten()[0]
    return float(y_watt)


def upsert_carbon_emission(ts_from, ts_to, steps, pw, co2, strategy: Dict[str, Any]):
    """
    寫入/更新 carbon_emissions，包含 recommended_strategy (JSONB)。
    *** 採用手動 JSON 序列化來修復 psycopg2 錯誤 ***
    """
    strategy_json = json.dumps(strategy)
    
    sql = text("""
        INSERT INTO carbon_emissions
        (timestamp_from, timestamp_to, horizon_steps, predicted_power_w, predicted_co2_kg, model_version, recommended_strategy)
        VALUES (:from, :to, :h, :pw, :co2, :mv, :strategy)
        ON CONFLICT (timestamp_to, model_version) DO UPDATE
        SET predicted_power_w = EXCLUDED.predicted_power_w,
            predicted_co2_kg  = EXCLUDED.predicted_co2_kg,
            timestamp_from    = EXCLUDED.timestamp_from,
            horizon_steps     = EXCLUDED.horizon_steps,
            recommended_strategy = EXCLUDED.recommended_strategy;
    """)
    try:
        with engine.begin() as conn:
            conn.execute(sql, {
                "from": ts_from, "to": ts_to, "h": steps,
                "pw": pw, "co2": co2, "mv": MODEL_VERSION,
                "strategy": strategy_json # 傳遞 JSON 字串
            })
    except SQLAlchemyError as e:
        raise RuntimeError(f"DB Write Error: {e}")


# --------------------
# 背景排程 (Background Job)
# --------------------

async def loop_job():
    # 確保首次運行前等待到整點 STEP_MIN 間隔
    now_raw = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    ts_aligned = floor_to_step(now_raw, STEP_MIN)
    next_run_time = ts_aligned + dt.timedelta(minutes=STEP_MIN)
    initial_wait = (next_run_time - now_raw).total_seconds()
    
    if initial_wait < 0:
         initial_wait = (next_run_time + dt.timedelta(minutes=STEP_MIN) - now_raw).total_seconds()
         
    await asyncio.sleep(max(1, initial_wait))

    while True:
        now_raw = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        
        # 1. 計算預測時間窗口 (確保時間戳記對齊)
        ts_to = floor_to_step(now_raw + dt.timedelta(minutes=STEP_MIN), STEP_MIN)
        ts_from = ts_to - dt.timedelta(minutes=STEP_MIN)
        fetch_end_ts = ts_from 
        
        try:
            # 2. 抓取數據
            df = fetch_power_series(fetch_end_ts, LOOKBACK_MIN)
            
            if df.empty:
                raise ValueError(f"No data in lookback window ({LOOKBACK_MIN} min).")
            
            # 3. 預測
            pred_power_w = predict_next_power_w(df)
            kWh = (pred_power_w / 1000.0) * (STEP_MIN / 60.0)
            co2 = kWh * EF

            # 4. 生成策略
            thresholds = get_power_thresholds()
            strategy = recommend_strategy(pred_power_w, thresholds)

            # 5. 寫入數據
            upsert_carbon_emission(ts_from, ts_to, 1, pred_power_w, co2, strategy)

            # 6. 記錄日誌
            print(f"[{ts_to.isoformat()}] Pred={pred_power_w:.2f} W | kWh={kWh:.6f} | CO2={co2:.6f} kg | Strategy: {strategy['summary']}")
            
        except ValueError as e:
            print(f"[{now_raw.isoformat()}] Job error (Data/Predict): {e}")
        except Exception as e:
            print(f"[{now_raw.isoformat()}] Job error (General): {repr(e)}")
            
        await asyncio.sleep(RUN_INTERVAL_SECONDS)


# --------------------
# FastAPI 路由 (FastAPI Routes)
# --------------------

@app.on_event("startup")
async def on_startup():
    await asyncio.sleep(1)
    asyncio.create_task(loop_job())

@app.get("/health")
def health():
    status = "ok" if db_ok() else "down"
    return {
        "status": status,
        "database": "connected" if status == "ok" else "disconnected",
        "model_version": MODEL_VERSION,
        "step_minutes": STEP_MIN,
        "window": WINDOW,
        "lookback_minutes": LOOKBACK_MIN,
        "timestamp": dt.datetime.utcnow().isoformat() + "Z",
    }

@app.post("/run-once")
def run_once():
    now_raw = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    ts_to = floor_to_step(now_raw + dt.timedelta(minutes=STEP_MIN), STEP_MIN)
    ts_from = ts_to - dt.timedelta(minutes=STEP_MIN)
    fetch_end_ts = ts_from 
    
    try:
        df = fetch_power_series(fetch_end_ts, LOOKBACK_MIN)
        if df.empty:
            raise HTTPException(400, f"no data in last {LOOKBACK_MIN} minutes")
            
        pred_power_w = predict_next_power_w(df)
        kWh = (pred_power_w / 1000.0) * (STEP_MIN / 60.0)
        co2 = kWh * EF

        thresholds = get_power_thresholds()
        strategy = recommend_strategy(pred_power_w, thresholds)
        
        upsert_carbon_emission(ts_from, ts_to, 1, pred_power_w, co2, strategy)

        return {
            "ok": True,
            "model_version": MODEL_VERSION,
            "timestamp_from": ts_from.isoformat(),
            "timestamp_to": ts_to.isoformat(),
            "predicted_power_w": pred_power_w,
            "predicted_co2_kg": co2,
            "strategy": strategy
        }
    except ValueError as e:
        raise HTTPException(400, f"Predict Error: {e}")
    except Exception as e:
        raise HTTPException(500, f"Internal Error: {e}")


# --------------------
# 端點：最新 KPI (Latest KPI Endpoint)
# --------------------
@app.get("/metrics/latest", response_model=KPIMetric)
def metrics_latest():
    """取得最新一筆的預測與實際測量數據 (KPI)"""
    sql = text("""
      SELECT
        c.timestamp_to AS ts,
        c.predicted_power_w,
        c.predicted_co2_kg,
        m.system_power_watt AS actual_power_w
      FROM carbon_emissions c
      LEFT JOIN energy_cleaned m
        ON m.timestamp_utc::timestamptz = c.timestamp_to
      ORDER BY c.timestamp_to DESC
      LIMIT 1
    """)
    try:
        with engine.connect() as conn:
            row = conn.execute(sql).mappings().first()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
        
    if not row:
        return KPIMetric(timestamp=dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc))
        
    actual_co2 = None
    actual_kwh = None
    predicted_kwh = None

    if row["actual_power_w"] is not None:
        actual_kwh = (row["actual_power_w"]/1000.0)*(STEP_MIN/60.0)
        actual_co2 = actual_kwh * EF

    if row["predicted_power_w"] is not None:
        predicted_kwh = (row["predicted_power_w"]/1000.0)*(STEP_MIN/60.0)

    return KPIMetric(
        timestamp=row["ts"],
        actual_power_w=row["actual_power_w"],
        predicted_power_w=row["predicted_power_w"],
        actual_co2_kg=actual_co2,
        predicted_co2_kg=row["predicted_co2_kg"],
        actual_kwh=actual_kwh,
        predicted_kwh=predicted_kwh
    )

# --------------------
# 端點：時間序列 (Timeseries Endpoint)
# --------------------

@app.get("/emissions/range", response_model=TimeseriesData)
def emissions_range(
    start: str = Query(..., description="ISO8601, e.g. 2025-09-28T00:00:00Z"),
    end: str    = Query(..., description="ISO8601, e.g. 2025-09-29T00:00:00Z"),
    gran: str  = Query("hour", pattern="^(minute|hour|day)$", description="聚合粒度: minute, hour, or day")
):
    """取得指定時間範圍內的功耗與碳排放數據，並依粒度聚合，包含誤差指標平均值。"""
    try:
        s = dt.datetime.fromisoformat(start.replace("Z","+00:00"))
        e = dt.datetime.fromisoformat(end.replace("Z","+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use ISO8601 (e.g., 2025-09-28T00:00:00Z).")

    df = load_joined_range(s, e)
    
    if df.empty:
        return {"data":[]}
        
    out = to_series(df, gran)
    
    records = out.to_dict(orient="records")
    return {"data": records}

# --------------------
# 端點：分段比較 (Segment Comparison Endpoint)
# --------------------

@app.get("/compare/segments", response_model=SegmentComparison)
def compare_segments(
    start: str = Query(..., description="ISO8601, e.g. 2025-09-28T00:00:00Z"),
    end: str = Query(..., description="ISO8601, e.g. 2025-09-29T00:00:00Z")
):
    """將數據依功耗分為高/中/低三段，並計算預測誤差指標 (MAE, RMSE, MAPE) 與 CO2 總和。"""
    try:
        s = dt.datetime.fromisoformat(start.replace("Z","+00:00"))
        e = dt.datetime.fromisoformat(end.replace("Z","+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use ISO8601.")
        
    df = load_joined_range(s, e)
    df = df.dropna(subset=["actual_power_w", "predicted_power_w"])
    
    if df.empty:
        return {"segments":[], "thresholds":{"p20":0,"p80":0}}
        
    # 1. 計算分位數門檻
    p20 = float(df["actual_power_w"].quantile(0.2))
    p80 = float(df["actual_power_w"].quantile(0.8))
    
    # 2. 數據分段函式
    def band(x):
        if x >= p80: return "HIGH"
        if x <= p20: return "LOW"
        return "MID"
        
    df["band"] = df["actual_power_w"].map(band)
    
    # 3. 分組聚合
    out = (df.groupby("band", dropna=True)
              .agg(
                MAE_W=("abs_err","mean"),
                RMSE_W=("sq_err", lambda s: (s.mean())**0.5),
                MAPE_W=("ape","mean"),
                pred_co2_sum=("predicted_co2_kg","sum"),
                actual_co2_sum=("actual_co2_kg","sum")
              )
              .reset_index())
              
    return {"segments": out.to_dict(orient="records"),
            "thresholds":{"p20":p20,"p80":p80}}