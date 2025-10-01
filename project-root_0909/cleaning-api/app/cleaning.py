def clean_energy_data(data: dict) -> dict:
    cleaned = data.copy()

    # 處理 unknown GPU 型號
    if cleaned.get("gpu_model", "").lower() == "unknown":
        cleaned["gpu_model"] = "Generic GPU"

    # 欄位型別轉換與預設補值
    float_fields = [
        "gpu_usage_percent", "gpu_power_watt", "cpu_power_watt",
        "memory_used_mb", "disk_read_mb_s", "disk_write_mb_s", "system_power_watt"
    ]
    for field in float_fields:
        try:
            cleaned[field] = float(cleaned.get(field, 0))
        except (ValueError, TypeError):
            cleaned[field] = 0.0

    # 未來可能捨棄欄位的可切換點（目前保留）
    return cleaned
