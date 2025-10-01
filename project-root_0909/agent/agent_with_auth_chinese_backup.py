# agent_with_auth.py (僅功耗計算，移除碳排放計算)
import psutil
import platform
import uuid
import getpass
import time
import json
import csv
import os
import requests
import hashlib
import hmac
from datetime import datetime, timezone, time as dtime
import subprocess
from pynput import mouse, keyboard
import threading
import socket

# ---------- 配置設定 ----------
API_BASE_URL = "http://localhost:8000"  # 您的 ingestion-api 地址
AUTH_SECRET_KEY = "NTCUST-ENERGY-MONITOR"  # 更新與 API 相同的密鑰
FALLBACK_TO_CSV = True  # 如果 API 不可用，是否儲存到 CSV

# ---------- 上課節次時間設定 ----------
class_periods = [
    ("08:10", "09:00"), ("09:10", "10:00"),
    ("10:10", "11:00"), ("11:10", "12:00"),
    ("13:25", "14:15"), ("14:20", "15:10"),
    ("15:20", "16:10"), ("16:15", "17:05")
]

def is_class_time():
    now = datetime.now().time()
    for start_str, end_str in class_periods:
        start = dtime.fromisoformat(start_str)
        end = dtime.fromisoformat(end_str)
        if start <= now <= end:
            return True
    return False

# ---------- MAC 地址和認證功能 ----------
def get_mac_address():
    """取得設備 MAC 地址"""
    try:
        # 方法 1: 使用 uuid.getnode()
        mac = uuid.getnode()
        mac_str = ':'.join(['{:02x}'.format((mac >> elements) & 0xff) 
                           for elements in range(0,2*6,2)][::-1])
        return mac_str.upper()
    except:
        try:
            # 方法 2: 使用網路介面
            import netifaces
            interfaces = netifaces.interfaces()
            for interface in interfaces:
                if interface != 'lo':  # 排除本地回環
                    addrs = netifaces.ifaddresses(interface)
                    if netifaces.AF_LINK in addrs:
                        mac = addrs[netifaces.AF_LINK][0]['addr']
                        return mac.upper().replace('-', ':')
        except:
            pass
        
        # 方法 3: 系統指令 (備用)
        try:
            if platform.system() == "Windows":
                result = subprocess.run(['getmac'], capture_output=True, text=True)
                lines = result.stdout.split('\n')
                for line in lines:
                    if '-' in line and len(line.split('-')) == 6:
                        return line.replace('-', ':').upper().strip()
            else:  # Linux/macOS
                result = subprocess.run(['ifconfig'], capture_output=True, text=True)
                # 簡化版解析，實際可能需要更複雜的正則表達式
                pass
        except:
            pass
    
    return "00:00:00:00:00:00"  # 預設值

def generate_device_certificate(mac_address, secret_key):
    """生成設備憑證"""
    return hmac.new(
        secret_key.encode(), 
        mac_address.encode(), 
        hashlib.sha256
    ).hexdigest()

def get_auth_headers():
    """取得認證 Headers"""
    mac_address = get_mac_address()
    certificate = generate_device_certificate(mac_address, AUTH_SECRET_KEY)
    
    return {
        "Content-Type": "application/json",
        "MAC-Address": mac_address,
        "Device-Certificate": certificate
    }

# ---------- 增強硬體資訊收集（用於指紋生成）----------
def get_enhanced_system_info():
    """收集更詳細的系統資訊用於設備指紋"""
    try:
        system_info = {
            "cpu_model": platform.processor() or "Unknown",
            "cpu_count": psutil.cpu_count(),
            "total_memory": psutil.virtual_memory().total,
            "disk_partitions": len(psutil.disk_partitions()),
            "network_interfaces": len(psutil.net_if_addrs()),
            "platform_machine": platform.machine(),
            "platform_architecture": platform.architecture()[0]
        }
        return system_info
    except:
        return {}

# ---------- 改進的硬體數據擷取與功耗計算 ----------
def get_gpu_model():
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=gpu_name', '--format=csv,noheader'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.stderr:
            return "Unknown"
        return result.stdout.decode('utf-8').strip()
    except:
        return "Unknown"

def get_gpu_usage():
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.stderr:
            return 0
        usage = result.stdout.decode('utf-8').strip()
        return float(usage) if usage else 0
    except:
        return 0

def get_gpu_power_watt():
    """
    獲取 GPU 功耗（W）
    優先使用 nvidia-smi，若失敗則根據使用率估算
    """
    try:
        # 方法 1：直接從 nvidia-smi 獲取實際功耗
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=power.draw', '--format=csv,noheader,nounits'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.returncode == 0 and not result.stderr:
            power = result.stdout.decode('utf-8').strip()
            if power and power != "N/A":
                return float(power)
    except:
        pass
    
    # 方法 2：根據 GPU 使用率估算功耗
    gpu_usage = get_gpu_usage()
    gpu_model = get_gpu_model().lower()
    
    # 根據 GPU 型號設定功耗範圍
    if "mx250" in gpu_model:
        # MX250: 基礎 5W，滿載 25W
        base_power = 5.0
        max_power = 25.0
    elif "rtx" in gpu_model:
        # RTX 系列: 基礎 20W，滿載依型號而定
        if "4090" in gpu_model:
            base_power, max_power = 50.0, 450.0
        elif "4080" in gpu_model:
            base_power, max_power = 40.0, 320.0
        elif "4070" in gpu_model:
            base_power, max_power = 30.0, 200.0
        else:
            base_power, max_power = 25.0, 250.0  # 一般 RTX
    elif "gtx" in gpu_model:
        # GTX 系列
        base_power, max_power = 15.0, 180.0
    else:
        # 未知 GPU，保守估計
        base_power, max_power = 10.0, 75.0
    
    # 根據使用率計算功耗
    additional_power = (gpu_usage / 100.0) * (max_power - base_power)
    estimated_power = base_power + additional_power
    
    return round(estimated_power, 2)

def get_cpu_power():
    """
    改進的 CPU 功耗計算
    基於 CPU 使用率和處理器類型估算功耗
    """
    cpu_percent = psutil.cpu_percent(interval=1)
    
    # 獲取 CPU 資訊用於功耗估算
    try:
        cpu_info = platform.processor().lower()
        cpu_count = psutil.cpu_count()
    except:
        cpu_info = ""
        cpu_count = 4  # 預設值
    
    # 根據 CPU 類型和核心數估算功耗範圍
    if "intel" in cpu_info:
        if "i9" in cpu_info or "xeon" in cpu_info:
            # 高階 Intel CPU
            base_power = 15.0 + (cpu_count * 2)  # 每核心約 2W 基礎
            max_additional = 50.0 + (cpu_count * 5)  # 每核心約 5W 額外
        elif "i7" in cpu_info:
            # 中高階 Intel CPU
            base_power = 12.0 + (cpu_count * 1.5)
            max_additional = 35.0 + (cpu_count * 4)
        elif "i5" in cpu_info:
            # 中階 Intel CPU
            base_power = 10.0 + (cpu_count * 1.2)
            max_additional = 25.0 + (cpu_count * 3)
        else:
            # 一般 Intel CPU
            base_power = 8.0 + (cpu_count * 1)
            max_additional = 20.0 + (cpu_count * 2.5)
    elif "amd" in cpu_info:
        if "ryzen 9" in cpu_info or "threadripper" in cpu_info:
            # 高階 AMD CPU
            base_power = 15.0 + (cpu_count * 1.8)
            max_additional = 45.0 + (cpu_count * 4.5)
        elif "ryzen 7" in cpu_info:
            # 中高階 AMD CPU
            base_power = 12.0 + (cpu_count * 1.4)
            max_additional = 30.0 + (cpu_count * 3.5)
        elif "ryzen 5" in cpu_info:
            # 中階 AMD CPU
            base_power = 10.0 + (cpu_count * 1.2)
            max_additional = 25.0 + (cpu_count * 3)
        else:
            # 一般 AMD CPU
            base_power = 8.0 + (cpu_count * 1)
            max_additional = 20.0 + (cpu_count * 2.5)
    else:
        # 未知 CPU，根據核心數保守估計
        base_power = 10.0 + (cpu_count * 1)
        max_additional = 25.0 + (cpu_count * 3)
    
    # 根據使用率計算額外功耗
    additional_power = (cpu_percent / 100.0) * max_additional
    
    total_power = base_power + additional_power
    
    return round(total_power, 2)

def get_memory_usage():
    memory = psutil.virtual_memory()
    return memory.used / (1024 * 1024)

def get_disk_read_write_rate(interval=1):
    before = psutil.disk_io_counters()
    time.sleep(interval)
    after = psutil.disk_io_counters()

    read_rate = (after.read_bytes - before.read_bytes) / (1024 * 1024) / interval
    write_rate = (after.write_bytes - before.write_bytes) / (1024 * 1024) / interval
    return round(read_rate, 2), round(write_rate, 2)

def get_system_power(cpu, gpu, memory):
    """
    改進的系統總功耗計算
    基於實際硬體功耗模型
    """
    # 記憶體功耗：DDR4/DDR5 每 GB 約 3-4W
    memory_gb = memory / 1024.0  # 轉換為 GB
    memory_power = memory_gb * 3.5  # 每 GB 3.5W
    
    # 基礎系統功耗（主機板、風扇、SSD、網卡等）
    motherboard_power = 15.0  # 主機板
    cooling_power = 5.0  # 風扇
    storage_power = 5.0  # SSD/HDD
    other_power = 10.0  # 其他（網卡、USB設備等）
    
    base_system_power = motherboard_power + cooling_power + storage_power + other_power
    
    # 計算總功耗
    total_power = cpu + gpu + memory_power + base_system_power
    
    # 電源效率損耗（80 Plus 認證約 85-95% 效率）
    # 假設 90% 效率，所以實際消耗要除以 0.9
    efficiency_factor = 1.11  # 1/0.9 ≈ 1.11
    
    final_power = total_power * efficiency_factor
    
    return round(final_power, 2)

def validate_power_readings(data):
    """
    驗證功耗讀數的合理性，防止異常值
    根據實際硬體規格設定上限
    """
    # 設定合理上限
    limits = {
        'cpu': 125.0,     # 高階桌機 CPU 上限
        'gpu': 500.0,     # 高階 GPU 上限（如 RTX 4090）
        'system_power': 800.0  # 高階工作站合理上限
    }
    
    warnings = []
    
    # 檢查並修正異常值
    for key, limit in limits.items():
        if key in data and data[key] > limit:
            warnings.append(f"{key}: {data[key]}W -> {limit}W")
            data[key] = limit
    
    # 邏輯性檢查：系統功耗不應小於 CPU + GPU 功耗
    min_system_power = data.get('cpu', 0) + data.get('gpu', 0) + 20  # 至少多 20W
    if 'system_power' in data and data['system_power'] < min_system_power:
        warnings.append(f"system_power: {data['system_power']}W -> {min_system_power}W (邏輯調整)")
        data['system_power'] = min_system_power
    
    # 如果有警告，顯示修正資訊
    if warnings:
        print(f"功耗數值修正: {', '.join(warnings)}")
    
    return data

def get_timestamp():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

def get_device_info():
    return (
        str(uuid.getnode()),
        getpass.getuser(),
        "v1.4.0",  # 升級版本號（功耗優化版）
        platform.system(),
        platform.version(),
        "Taipei, Taiwan"
    )

# ---------- 資料傳送 (新增 API 功能) ----------
def send_to_api(data):
    """發送資料到 ingestion-api（包含設備指紋用於安全檢測）"""
    try:
        headers = get_auth_headers()
        
        # 完整的資料傳送（包含設備指紋用於安全檢測）
        api_data = {
            # 基本能耗數據
            "timestamp_utc": data["timestamp"],
            "gpu_model": data["gpu_model"],
            "gpu_usage_percent": data["gpu_usage"],
            "gpu_power_watt": data["gpu"],
            "cpu_power_watt": data["cpu"],
            "memory_used_mb": data["memory"],
            "disk_read_mb_s": data["disk_read"],
            "disk_write_mb_s": data["disk_write"],
            "system_power_watt": data["system_power"],
            "device_id": data["device_id"],
            "user_id": data["user_id"],
            "agent_version": data["agent_version"],
            "os_type": data["os_type"],
            "os_version": data["os_version"],
            "location": data["location"],
            
            # 設備指紋（用於安全檢測）
            "cpu_model": data.get("cpu_model"),
            "cpu_count": data.get("cpu_count"),
            "total_memory": data.get("total_memory"),
            "disk_partitions": data.get("disk_partitions"),
            "network_interfaces": data.get("network_interfaces"),
            "platform_machine": data.get("platform_machine"),
            "platform_architecture": data.get("platform_architecture")
        }
        
        print(f"傳送數據（含設備指紋）到 API...")
        print(f"基本數據: CPU={data['cpu']}W, GPU={data['gpu']}W, 系統={data['system_power']}W")
        print(f"設備指紋: {data.get('cpu_model', 'Unknown')} ({data.get('cpu_count', 'Unknown')} cores)")
        
        response = requests.post(
            f"{API_BASE_URL}/ingest",
            json=api_data,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            
            # 顯示指紋檢查結果
            if "fingerprint_check" in result:
                fp_result = result["fingerprint_check"]
                risk_level = fp_result.get("risk_level", "unknown")
                message = fp_result.get("message", "")
                similarity = fp_result.get("similarity_score", 0)
                
                if risk_level == "high":
                    print(f"高風險設備警告: {message} (相似度: {similarity:.2f})")
                elif risk_level == "medium":
                    print(f"中風險提醒: {message} (相似度: {similarity:.2f})")
                else:
                    print(f"設備正常: {message} (相似度: {similarity:.2f})")
            
            print(f"資料已成功傳送到 API: {result.get('status', 'unknown')}")
            return True
            
        elif response.status_code == 401:
            print(f"認證失敗: {response.json().get('detail', 'Unknown auth error')}")
            return False
        elif response.status_code == 403:
            print(f"設備未授權: {response.json().get('detail', 'Device not authorized')}")
            print(f"   您的 MAC 地址: {get_mac_address()}")
            print(f"   請聯繫管理員將此設備加入白名單")
            return False
        else:
            print(f"API 回應錯誤: {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.ConnectionError:
        print(f"無法連接到 API: {API_BASE_URL}")
        return False
    except requests.exceptions.Timeout:
        print("API 請求逾時")
        return False
    except Exception as e:
        print(f"發送資料失敗: {str(e)}")
        return False

# ---------- CSV 備援儲存 (保持原有邏輯) ----------
data_buffer = []
file_count = 0
output_dir = "agent_logs"
os.makedirs(output_dir, exist_ok=True)

def save_to_csv(row):
    global data_buffer, file_count
    data_buffer.append(row)
    if len(data_buffer) >= 50:
        filename = os.path.join(output_dir, f"agent_data_{file_count}.csv")
        with open(filename, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            writer.writeheader()
            writer.writerows(data_buffer)
        print(f"CSV 備份已儲存：{filename}")
        data_buffer = []
        file_count += 1

# ---------- 優化的資料處理和儲存 ----------
def process_and_send_data():
    """處理和發送資料（優化功耗計算）"""
    device_id, user_id, agent_version, os_type, os_version, location = get_device_info()
    timestamp = get_timestamp()

    # 收集硬體數據
    gpu_model = get_gpu_model()
    gpu_usage = get_gpu_usage()
    gpu_power = get_gpu_power_watt()  # 改進的 GPU 功耗計算
    cpu_power = get_cpu_power()      # 改進的 CPU 功耗計算
    memory_used = get_memory_usage()
    disk_read, disk_write = get_disk_read_write_rate(interval=1)
    system_power = get_system_power(cpu_power, gpu_power, memory_used)  # 改進的系統功耗計算

    # 收集增強的系統資訊（指紋相關）
    enhanced_info = get_enhanced_system_info()

    # 準備數據
    data = {
        "timestamp": timestamp,
        "cpu": cpu_power,
        "gpu": gpu_power,
        "memory": memory_used,
        "disk_read": disk_read,
        "disk_write": disk_write,
        "gpu_usage": gpu_usage,
        "gpu_model": gpu_model,
        "system_power": system_power,
        "device_id": device_id,
        "user_id": user_id,
        "agent_version": agent_version,
        "os_type": os_type,
        "os_version": os_version,
        "location": location,
        
        # 增強系統資訊
        **enhanced_info
    }

    # 驗證並修正功耗數據
    data = validate_power_readings(data)

    # 顯示改進的功耗資訊
    print(f"\n功耗監控 - CPU: {data['cpu']}W | GPU: {data['gpu']}W | 系統: {data['system_power']}W")
    print(f"記憶體: {data['memory']:.1f}MB ({data['memory']/1024:.1f}GB)")
    print(f"GPU: {data['gpu_model']} ({data['gpu_usage']}%)")

    print("\n========== 完整資料輸出 ==========")
    for k, v in data.items():
        if isinstance(v, float):
            print(f"{k}: {v:.2f}")
        else:
            print(f"{k}: {v}")
    
    # 嘗試發送到 API
    api_success = send_to_api(data)
    
    # 如果 API 失敗且啟用備援，則儲存到 CSV
    if not api_success and FALLBACK_TO_CSV:
        print("API 發送失敗，使用 CSV 備援儲存")
        save_to_csv(data)
    
    return api_success

# ---------- 差異判斷 (保持原有邏輯) ----------
previous_data = {"cpu": 0, "gpu": 0, "memory": 0, "disk_read": 0, "disk_write": 0}
CHANGE_THRESHOLD = 5

def has_significant_change(new, old):
    changes = [k for k in new if abs(new[k] - old[k]) > CHANGE_THRESHOLD]
    if changes:
        print(f"資料變動超過閾值：{', '.join(changes)}")
        return True
    return False

# ---------- 使用者操作偵測 (保持原有邏輯) ----------
user_active = False

def on_event(x):
    global user_active
    user_active = True

def monitor_input():
    try:
        with mouse.Listener(on_click=on_event), keyboard.Listener(on_press=on_event):
            while True:
                time.sleep(1)
    except Exception as e:
        print(f"輸入監控啟動失敗: {e}")

threading.Thread(target=monitor_input, daemon=True).start()

# ---------- 初始化和健康檢查 ----------
def check_api_connection():
    """檢查 API 連接並驗證設備註冊狀態"""
    try:
        # 檢查 API 健康狀態
        response = requests.get(f"{API_BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            print("API 服務運行正常")
        else:
            print(f"API 健康檢查異常: {response.status_code}")
    except:
        print(f"無法連接到 API: {API_BASE_URL}")
        if FALLBACK_TO_CSV:
            print("將使用 CSV 備援模式")
        return False
    
    # 檢查設備是否已註冊
    mac_address = get_mac_address()
    print(f"設備 MAC 地址: {mac_address}")
    print(f"設備指紋功能: 已啟用")
    print(f"功耗計算: 已優化 (智能估算)")
    
    try:
        headers = get_auth_headers()
        response = requests.get(f"{API_BASE_URL}/admin/devices/{mac_address}", headers=headers, timeout=5)
        
        if response.status_code == 200:
            device_info = response.json()
            print(f"設備已註冊: {device_info['device_name']}")
            return True
        elif response.status_code == 404:
            print("設備尚未註冊到白名單，但指紋功能仍可運作")
            return True
        else:
            print(f"檢查設備註冊狀態失敗: {response.status_code}")
            return False
    except Exception as e:
        print(f"檢查設備註冊失敗: {e}")
        return False

# ---------- 主迴圈 ----------
def main():
    global user_active, previous_data 
    
    print("Agent 啟動中...")
    print(f"API 地址: {API_BASE_URL}")
    print(f"MAC 地址: {get_mac_address()}")
    print(f"版本: v1.4.0 (智能功耗計算)")
    
    # 初始化檢查
    api_available = check_api_connection()
    
    if not api_available and not FALLBACK_TO_CSV:
        print("API 不可用且未啟用 CSV 備援，程式結束")
        return
    
    print("開始監控...")
    
    while True:
        try:
            in_class = is_class_time()
            should_grab = False

            if in_class:
                should_grab = True
                print("上課時間，持續監控")
            elif user_active:
                should_grab = True
                print("偵測到使用者活動")
                user_active = False

            if should_grab:
                cpu_power = get_cpu_power()
                gpu_power = get_gpu_power_watt()
                memory_used = get_memory_usage()
                disk_read, disk_write = get_disk_read_write_rate(interval=1)

                new_data = {
                    "cpu": cpu_power,
                    "gpu": gpu_power,
                    "memory": memory_used,
                    "disk_read": disk_read,
                    "disk_write": disk_write,
                }

                if has_significant_change(new_data, previous_data):
                    success = process_and_send_data()
                    previous_data = new_data

            time.sleep(60)
            
        except KeyboardInterrupt:
            print("\nAgent 停止運行")
            break
        except Exception as e:
            print(f"運行時錯誤: {e}")
            time.sleep(60)  # 等待後重試

# ---------- 啟動 ----------
if __name__ == "__main__":
    main()