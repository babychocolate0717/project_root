# cleaning-api/app/cleaning.py

import joblib
from pathlib import Path

# --- vvv 新增以下區塊 vvv ---
# 載入 AI 模型
# 注意：在 Docker 環境中，路徑可能需要調整
# 這裡假設模型檔案會被複製到 cleaning-api 的根目錄下
MODEL_PATH = Path(__file__).parent.parent / "anomaly_detector.pkl"
anomaly_detector = None

if MODEL_PATH.exists():
    print(f"Loading anomaly detection model from {MODEL_PATH}...")
    anomaly_detector = joblib.load(MODEL_PATH)
    print("✅ Model loaded successfully.")
else:
    print(f"⚠️ Warning: Anomaly detection model not found at {MODEL_PATH}.")
# --- ^^^ 新增以上區塊 ^^^ ---


def clean_energy_data(data: dict) -> dict:
    cleaned = data.copy()

    # (您原有的清洗邏輯保持不變)
    if cleaned.get("gpu_model", "").lower() == "unknown":
        cleaned["gpu_model"] = "Generic GPU"

    float_fields = [
        "gpu_usage_percent", "gpu_power_watt", "cpu_power_watt",
        "memory_used_mb", "disk_read_mb_s", "disk_write_mb_s", "system_power_watt"
    ]
    for field in float_fields:
        try:
            cleaned[field] = float(cleaned.get(field, 0))
        except (ValueError, TypeError):
            cleaned[field] = 0.0

    # --- vvv 新增 AI 判斷區塊 vvv ---
    if anomaly_detector:
        try:
            # 準備模型需要的特徵
            features = [
                cleaned.get("gpu_power_watt", 0),
                cleaned.get("cpu_power_watt", 0),
                cleaned.get("memory_used_mb", 0),
                cleaned.get("system_power_watt", 0)
            ]

            # 模型預測 (-1 表示異常, 1 表示正常)
            prediction = anomaly_detector.predict([features])[0]

            if prediction == -1:
                cleaned["is_anomaly"] = True
                cleaned["anomaly_reason"] = "AI model detected an unusual power consumption pattern."
                print(f"🔍 AI detected an anomaly for device {cleaned.get('device_id')}")
            else:
                cleaned["is_anomaly"] = False
                cleaned["anomaly_reason"] = None

        except Exception as e:
            print(f"Error during anomaly prediction: {e}")
            cleaned["is_anomaly"] = False
            cleaned["anomaly_reason"] = "AI prediction failed."
    # --- ^^^ 新增 AI 判斷區塊 ^^^ ---

    return cleaned