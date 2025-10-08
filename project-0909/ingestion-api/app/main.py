from fastapi import FastAPI, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from . import models, schemas
from .database import SessionLocal, engine, Base
from .auth import verify_device_auth_compatible, get_db, DeviceAuthenticator
from .utils.mac_manager import MACManager
import requests
import logging
from datetime import datetime
from typing import List
from sqlalchemy import text, func, distinct
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import List

# 台灣電力公司 2023 年的電力碳排放係數 (公斤CO2e/度電)
CARBON_EMISSION_FACTOR = 0.495 
# Agent 的回報間隔 (秒)
COLLECTION_INTERVAL_SECONDS = 60

app = FastAPI(title="Energy Data Ingestion API", version="1.3.0")
templates = Jinja2Templates(directory="templates")

TAIWAN_TZ = ZoneInfo("Asia/Taipei")

def convert_utc_str_to_taiwan_str(utc_str: str) -> str:
    """將 UTC 時間字串轉換為台灣時間字串"""
    if not utc_str:
        return None
    try:
        # 將 ISO 格式字串轉換為有時區資訊的 datetime 物件
        utc_dt = datetime.fromisoformat(utc_str.replace('Z', '+00:00'))
        # 轉換時區
        taiwan_dt = utc_dt.astimezone(TAIWAN_TZ)
        # 格式化為字串
        return taiwan_dt.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return utc_str # 如果格式不對，回傳原字串

# 設定日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 建立所有資料表
logger.info("開始建立資料表...")
Base.metadata.create_all(bind=engine)
logger.info("資料表建立完成")

@app.get("/")
async def root():
    return {
        "message": "Energy Data Ingestion API", 
        "version": "1.2.0",
        "features": ["MAC Authentication", "Device Fingerprint", "Device Management", "Health Monitoring"]
    }

@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """健康檢查端點"""
    try:
        # 檢查資料庫連接
        db.execute(text("SELECT 1"))
        
        # 檢查清洗服務
        try:
            response = requests.get("http://cleaner:8100/health", timeout=5)
            cleaner_healthy = response.status_code == 200
        except:
            cleaner_healthy = False
        
        return {
            "status": "healthy" if cleaner_healthy else "partial",
            "database": "connected",
            "cleaner_service": "connected" if cleaner_healthy else "disconnected",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(status_code=503, detail="Service unhealthy")

@app.post("/ingest")
def ingest(
    request: Request,
    data: schemas.EnergyData, 
    db: Session = Depends(get_db),
    auth: dict = Depends(verify_device_auth_compatible)
):
    """接收能耗資料並進行處理"""
    logger.info(f"Received data from device: {auth['mac_address']} (method: {auth['method']})")
    
    try:
        # 準備原始數據
        raw_data = data.dict()
        
        # 移除不支援的欄位
        unsupported_fields = ['device_fingerprint', 'fingerprint_hash', 'risk_score']
        for field in unsupported_fields:
            raw_data.pop(field, None)
        
        # EnergyRaw 支援的欄位
        raw_supported_fields = {
            "timestamp_utc", "gpu_model", "gpu_usage_percent", "gpu_power_watt",
            "cpu_power_watt", "memory_used_mb", "disk_read_mb_s", "disk_write_mb_s",
            "system_power_watt", "device_id", "user_id", "agent_version", 
            "os_type", "os_version", "location",
            "cpu_model", "cpu_count", "total_memory", "disk_partitions",
            "network_interfaces", "platform_machine", "platform_architecture"
        }
        
        raw_filtered = {k: v for k, v in raw_data.items() 
                       if k in raw_supported_fields and v is not None}

        # 1️⃣ 寫入原始資料
        raw_record = models.EnergyRaw(**raw_filtered)
        db.add(raw_record)
        db.flush()

        # 2️⃣ 呼叫 cleaning-api
        try:
            response = requests.post("http://cleaner:8100/clean", json=raw_filtered, timeout=10)
            response.raise_for_status()
            cleaned_data = response.json()["cleaned_data"]
            # 計算碳排放
            system_power_watt = cleaned_data.get("system_power_watt", 0)
            if system_power_watt > 0:
                # 功率(W) -> 千瓦(kW) -> 度電(kWh) -> 碳排(kgCO2e)
                kwh_consumed = (system_power_watt / 1000) * (COLLECTION_INTERVAL_SECONDS / 3600)
                carbon_kgco2e = kwh_consumed * CARBON_EMISSION_FACTOR
                cleaned_data["carbon_kgco2e"] = carbon_kgco2e

            energy_cleaned_fields = {
                "timestamp_utc", "gpu_model", "gpu_usage_percent", "gpu_power_watt",
                "cpu_power_watt", "memory_used_mb", "disk_read_mb_s", "disk_write_mb_s",
                "system_power_watt", "device_id", "user_id", "agent_version", 
                "os_type", "os_version", "location", "is_anomaly", "anomaly_reason",
                "carbon_kgco2e" # 新增碳排欄位
            }
            
            cleaned_filtered = {k: v for k, v in cleaned_data.items() if k in energy_cleaned_fields}
            
            cleaned_record = models.EnergyCleaned(**cleaned_filtered)
            db.add(cleaned_record)
            
            db.commit()
            # 🔧 根據實際資料表結構過濾清洗後的資料
            energy_cleaned_fields = {
                "timestamp_utc", "gpu_model", "gpu_usage_percent", "gpu_power_watt",
                "cpu_power_watt", "memory_used_mb", "disk_read_mb_s", "disk_write_mb_s",
                "system_power_watt", "device_id", "user_id", "agent_version", 
                "os_type", "os_version", "location", "is_anomaly", "anomaly_reason"
                # 注意：故意排除 confidence_score
            }
            
            # 過濾清洗後的資料，只保留表中存在的欄位
            cleaned_filtered = {}
            for k, v in cleaned_data.items():
                if k in energy_cleaned_fields:
                    cleaned_filtered[k] = v
            
            # 確保必要的欄位存在
            if "is_anomaly" not in cleaned_filtered:
                cleaned_filtered["is_anomaly"] = False
            if "anomaly_reason" not in cleaned_filtered:
                cleaned_filtered["anomaly_reason"] = None
            
            cleaned_record = models.EnergyCleaned(**cleaned_filtered)
            db.add(cleaned_record)
            
            db.commit()
            logger.info(f"✅ Successfully processed data from {data.device_id}")
            
        except Exception as cleaning_error:
            # 清洗失敗，只保存原始資料
            db.commit()
            logger.warning(f"⚠️ Cleaning failed for {data.device_id}: {str(cleaning_error)}")
        
        # 準備回應
        response_data = {
            "status": "success", 
            "device": data.device_id, 
            "auth_method": auth['method']
        }
        
        if 'fingerprint_check' in auth:
            response_data["fingerprint_check"] = auth['fingerprint_check']
        
        return response_data
            
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Failed to process data from {data.device_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

# ==========================================================================
# 管理端點 - 安全存取版本
# ==========================================================================

@app.get("/admin/dashboard")
async def get_dashboard(db: Session = Depends(get_db)):
    """取得後台總覽資訊"""
    try:
        # 基本統計
        total_records = db.query(models.EnergyRaw).count()
        unique_devices = db.query(func.count(distinct(models.EnergyRaw.device_id))).scalar()
        
        # 今日統計
        today = datetime.now().date()
        today_records = db.query(models.EnergyRaw).filter(
            models.EnergyRaw.timestamp_utc.like(f"{today}%")
        ).count()
        
        # 風險等級統計（安全檢查）
        try:
            risk_stats = db.query(
                models.EnergyRaw.risk_level,
                func.count(models.EnergyRaw.risk_level)
            ).filter(
                models.EnergyRaw.risk_level.isnot(None)
            ).group_by(models.EnergyRaw.risk_level).all()
            
            risk_summary = {level: count for level, count in risk_stats}
        except:
            risk_summary = {}
        
        # 白名單設備統計
        try:
            whitelisted_devices = db.query(models.AuthorizedDevice).filter(
                models.AuthorizedDevice.is_active == True
            ).count()
        except:
            whitelisted_devices = 0
        
        return {
            "total_records": total_records,
            "unique_devices": unique_devices,
            "records_today": today_records,
            "risk_summary": risk_summary,
            "whitelisted_devices": whitelisted_devices,
            "last_updated": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Dashboard query failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Dashboard error: {str(e)}")

@app.get("/admin/device-ids")
async def get_device_ids(db: Session = Depends(get_db)):
    """取得所有設備ID列表"""
    try:
        # 取得所有不同的設備ID及其最新記錄
        device_ids = db.query(distinct(models.EnergyRaw.device_id)).all()
        
        id_list = []
        for row in device_ids:
            device_id = row[0]
            
            # 取得該設備的最新記錄
            latest_record = db.query(models.EnergyRaw).filter(
                models.EnergyRaw.device_id == device_id
            ).order_by(models.EnergyRaw.timestamp_utc.desc()).first()
            
            if latest_record:
                id_list.append({
                    "device_id": device_id,
                    "user_id": getattr(latest_record, 'user_id', 'Unknown'),
                    "last_seen": latest_record.timestamp_utc,
                    "risk_level": getattr(latest_record, 'risk_level', 'unknown'),
                    "gpu_model": getattr(latest_record, 'gpu_model', 'Unknown'),
                    "os_type": getattr(latest_record, 'os_type', 'Unknown'),
                    "similarity_score": getattr(latest_record, 'similarity_score', 0.0)
                })
        
        return {
            "device_ids": id_list,
            "total_count": len(id_list)
        }
    except Exception as e:
        logger.error(f"Device IDs query failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Query error: {str(e)}")

@app.get("/admin/devices-simple")
async def get_devices_simple(db: Session = Depends(get_db)):
    """取得所有設備的簡化列表"""
    try:
        # 取得最近的記錄並去重
        devices = db.query(models.EnergyRaw).order_by(
            models.EnergyRaw.timestamp_utc.desc()
        ).limit(200).all()
        
        # 去重並取得每個設備的最新記錄
        device_dict = {}
        for device in devices:
            if device.device_id not in device_dict:
                device_dict[device.device_id] = device
        
        device_list = []
        for device_id, device in device_dict.items():
            device_info = {
                "device_id": device.device_id,
                "user_id": getattr(device, 'user_id', 'Unknown'),
                "gpu_model": getattr(device, 'gpu_model', 'Unknown'),
                "os_type": getattr(device, 'os_type', 'Unknown'),
                "os_version": getattr(device, 'os_version', 'Unknown'),
                "agent_version": getattr(device, 'agent_version', 'Unknown'),
                "location": getattr(device, 'location', 'Unknown'),
                "last_seen": device.timestamp_utc,
                "risk_level": getattr(device, 'risk_level', 'unknown'),
                "device_fingerprint": getattr(device, 'device_fingerprint', 'N/A'),
                "similarity_score": getattr(device, 'similarity_score', 0.0),
                "cpu_power": getattr(device, 'cpu_power_watt', 0.0),
                "gpu_power": getattr(device, 'gpu_power_watt', 0.0),
                "system_power": getattr(device, 'system_power_watt', 0.0)
            }
            device_list.append(device_info)
        
        return {
            "devices": device_list,
            "total_count": len(device_list)
        }
    except Exception as e:
        logger.error(f"Devices query failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Query error: {str(e)}")

@app.get("/admin/device/{device_id}")
async def get_device_simple_details(device_id: str, db: Session = Depends(get_db)):
    """取得特定設備的詳細記錄（簡化版）"""
    try:
        # 取得設備最近10筆記錄
        records = db.query(models.EnergyRaw).filter(
            models.EnergyRaw.device_id == device_id
        ).order_by(models.EnergyRaw.timestamp_utc.desc()).limit(10).all()
        
        if not records:
            raise HTTPException(status_code=404, detail="Device not found")
        
        # 統計資訊
        total_records = db.query(models.EnergyRaw).filter(
            models.EnergyRaw.device_id == device_id
        ).count()
        
        latest_record = records[0]
        
        return {
            "device_info": {
                "device_id": device_id,
                "user_id": getattr(latest_record, 'user_id', 'Unknown'),
                "gpu_model": getattr(latest_record, 'gpu_model', 'Unknown'),
                "os_type": getattr(latest_record, 'os_type', 'Unknown'),
                "os_version": getattr(latest_record, 'os_version', 'Unknown'),
                "agent_version": getattr(latest_record, 'agent_version', 'Unknown'),
                "location": getattr(latest_record, 'location', 'Unknown'),
                # 轉換時間
                "first_seen": convert_utc_str_to_taiwan_str(records[-1].timestamp_utc),
                "last_seen": convert_utc_str_to_taiwan_str(latest_record.timestamp_utc)
            },
            "statistics": {
                "total_records": total_records
            },
            "fingerprint_history": [
                {
                    # 轉換時間
                    "timestamp": convert_utc_str_to_taiwan_str(r.timestamp_utc),
                    "fingerprint": getattr(r, 'device_fingerprint', 'N/A'),
                    "risk_level": getattr(r, 'risk_level', 'unknown'),
                    "similarity_score": getattr(r, 'similarity_score', 0.0)
                } for r in records if getattr(r, 'device_fingerprint', None)
            ],
            "recent_records": [
                {
                    # 轉換時間
                    "timestamp": convert_utc_str_to_taiwan_str(r.timestamp_utc),
                    "cpu_power": getattr(r, 'cpu_power_watt', 0.0),
                    "gpu_power": getattr(r, 'gpu_power_watt', 0.0),
                    "system_power": getattr(r, 'system_power_watt', 0.0),
                    "risk_level": getattr(r, 'risk_level', 'unknown'),
                    "similarity_score": getattr(r, 'similarity_score', 0.0)
                } for r in records
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Device details query failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Query error: {str(e)}")

@app.get("/admin/high-risk")
async def get_high_risk_simple(db: Session = Depends(get_db)):
    """取得高風險設備列表（簡化版）"""
    try:
        high_risk_devices = db.query(models.EnergyRaw).filter(
            models.EnergyRaw.risk_level == "high"
        ).order_by(models.EnergyRaw.timestamp_utc.desc()).limit(20).all()
        
        devices = []
        for device in high_risk_devices:
            devices.append({
                "device_id": device.device_id,
                "user_id": getattr(device, 'user_id', 'Unknown'),
                "timestamp": device.timestamp_utc,
                "risk_level": getattr(device, 'risk_level', 'unknown'),
                "similarity_score": getattr(device, 'similarity_score', 0.0),
                "device_fingerprint": getattr(device, 'device_fingerprint', 'N/A'),
                "gpu_model": getattr(device, 'gpu_model', 'Unknown')
            })
        
        return {
            "high_risk_devices": devices,
            "count": len(devices)
        }
    except Exception as e:
        logger.error(f"High risk devices query failed: {str(e)}")
        return {
            "high_risk_devices": [],
            "count": 0,
            "error": str(e)
        }

# ==========================================================================
# 原有的設備管理端點（白名單相關）
# ==========================================================================

@app.get("/admin/devices", response_model=List[schemas.DeviceResponse])
async def list_devices(db: Session = Depends(get_db)):
    """列出所有授權設備"""
    manager = MACManager(db)
    return manager.list_devices()

@app.post("/admin/devices")
async def add_device(device_data: schemas.DeviceCreate, db: Session = Depends(get_db)):
    """新增設備到白名單"""
    manager = MACManager(db)
    success = manager.add_device(
        device_data.mac_address,
        device_data.device_name,
        device_data.user_name,
        device_data.notes
    )
    
    if success:
        return {"status": "success", "message": "Device added to whitelist"}
    else:
        raise HTTPException(status_code=400, detail="Failed to add device or device already exists")

@app.delete("/admin/devices/{mac_address}")
async def remove_device(mac_address: str, db: Session = Depends(get_db)):
    """從白名單移除設備"""
    manager = MACManager(db)
    success = manager.remove_device(mac_address)
    
    if success:
        return {"status": "success", "message": "Device removed from whitelist"}
    else:
        raise HTTPException(status_code=404, detail="Device not found")

@app.get("/admin/devices/{mac_address}", response_model=schemas.DeviceResponse)
async def get_device_info(mac_address: str, db: Session = Depends(get_db)):
    """取得設備詳細資訊"""
    manager = MACManager(db)
    device = manager.get_device(mac_address)
    
    if device:
        return device
    else:
        raise HTTPException(status_code=404, detail="Device not found")

# ==========================================================================
# 系統監控端點
# ==========================================================================

@app.get("/metrics")
async def get_metrics(db: Session = Depends(get_db)):
    """取得系統指標"""
    try:
        today = datetime.now().date()
        
        raw_count = db.query(models.EnergyRaw).filter(
            models.EnergyRaw.timestamp_utc.like(f"{today}%")
        ).count()
        
        cleaned_count = db.query(models.EnergyCleaned).filter(
            models.EnergyCleaned.timestamp_utc.like(f"{today}%")
        ).count()
        
        try:
            active_devices = db.query(models.AuthorizedDevice).filter(
                models.AuthorizedDevice.is_active == True
            ).count()
        except:
            active_devices = 0
        
        # 異常設備統計
        try:
            high_risk_count = db.query(models.EnergyRaw).filter(
                models.EnergyRaw.timestamp_utc.like(f"{today}%"),
                models.EnergyRaw.risk_level == "high"
            ).count()
            
            medium_risk_count = db.query(models.EnergyRaw).filter(
                models.EnergyRaw.timestamp_utc.like(f"{today}%"),
                models.EnergyRaw.risk_level == "medium"
            ).count()
        except:
            high_risk_count = 0
            medium_risk_count = 0
        
        return {
            "records_today": {
                "raw": raw_count,
                "cleaned": cleaned_count,
                "success_rate": f"{(cleaned_count/raw_count*100):.1f}%" if raw_count > 0 else "0%"
            },
            "active_devices": active_devices,
            "security_status": {
                "high_risk_devices": high_risk_count,
                "medium_risk_devices": medium_risk_count,
                "total_anomalies": high_risk_count + medium_risk_count
            },
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Metrics collection failed: {str(e)}")
        return {"error": "Unable to collect metrics"}
@app.get("/admin/dashboard-ui", response_class=HTMLResponse)
async def get_dashboard_ui(request: Request, device_id: str = "all", db: Session = Depends(get_db)):
    """
    顯示整合儀表板 (企業總覽 vs. 個別設備)。
    這是使用者會看到的主要儀表板頁面。
    """
    
    # 1. 從資料庫取得所有已授權的設備列表，用於產生頁面上的下拉選單
    all_devices = db.query(models.AuthorizedDevice).filter(models.AuthorizedDevice.is_active == True).all()

    dashboard_data = {}
    view_mode = "all"

    try:
        if device_id == "all":
            # 模式一：顯示「企業總覽」儀表板
            view_mode = "all"
            # 透過內部 Docker 網路，向 prediction-api 請求企業總覽數據
            response = requests.get("http://prediction:8080/enterprise/allowance", timeout=5)
            if response.status_code == 200:
                dashboard_data = response.json()
            else:
                dashboard_data["error"] = f"無法獲取企業總覽數據 (狀態碼: {response.status_code}) - 請先設定本月預算"
        else:
            # 模式二：顯示「個別設備」儀表板
            view_mode = "device"
            # 向 prediction-api 請求特定設備過去24小時的數據
            response = requests.get(f"http://prediction:8080/metrics/device/{device_id}", timeout=5)
            if response.status_code == 200:
                dashboard_data = response.json()
            else:
                dashboard_data["error"] = f"無法獲取設備 {device_id} 的數據 (狀態碼: {response.status_code})"

    except requests.exceptions.RequestException as e:
        dashboard_data["error"] = f"無法連接到 Prediction API: {e}"

    # 3. 將所有需要的資料傳遞給 HTML 範本來產生最終的網頁
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "all_devices": all_devices,
        "selected_device_id": device_id,
        "view_mode": view_mode,
        "dashboard": dashboard_data
    })


@app.post("/admin/budgets-form")
async def set_budget_from_form(
    request: Request,
    year_month: str = Form(...),
    monthly_budget_kgco2e: float = Form(...),
    db: Session = Depends(get_db)
):
    """
    處理從網頁表單提交過來的預算設定請求。
    這個端點不會顯示頁面，而是處理完資料後「重新導向」回儀表板。
    """
    try:
        # 使用 Pydantic 模型來驗證表單數據
        budget_data = schemas.BudgetCreate(year_month=year_month, monthly_budget_kgco2e=monthly_budget_kgco2e)
        
        # 查詢資料庫看是否已存在該月份的預算
        db_budget = db.query(models.EnterpriseBudget).filter(
            models.EnterpriseBudget.year_month == budget_data.year_month
        ).first()

        if db_budget:
            # 如果存在，就更新數值
            db_budget.monthly_budget_kgco2e = budget_data.monthly_budget_kgco2e
            message = f"成功更新 {year_month} 的預算為 {monthly_budget_kgco2e} kgCO2e"
        else:
            # 如果不存在，就新增一筆紀錄
            db_budget = models.EnterpriseBudget(**budget_data.dict())
            db.add(db_budget)
            message = f"成功新增 {year_month} 的預算為 {monthly_budget_kgco2e} kgCO2e"
        
        db.commit()
    
    except Exception as e:
        db.rollback()
        # 如果出錯，也重新導向，但帶上錯誤訊息
        message = f"設定失敗: {e}"

    # 操作成功或失敗後，都重新導向回儀表板頁面，並在網址中附上提示訊息
    # status_code=303 是一種標準的 POST-Redirect-GET 模式，可以防止使用者重新整理頁面時重複提交表單
    return RedirectResponse(
        url=f"/admin/dashboard-ui?message={message}",
        status_code=303
    )