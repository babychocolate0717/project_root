# agent_with_auth_english.py
# English version to avoid encoding issues
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
import yaml
from typing import Dict, Any
import wmi
import sys

def get_resource_path(relative_path):
    """
    取得資源文件的絕對路徑
    - 開發環境：返回腳本目錄下的路徑
    - 打包後：返回 exe 目錄下的路徑（不是臨時解壓目錄）
    """
    if getattr(sys, 'frozen', False):
        # 打包後：exe 所在目錄
        base_path = os.path.dirname(sys.executable)
    else:
        # 開發環境
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    return os.path.join(base_path, relative_path)

def get_bundled_file(relative_path):
    """
    取得打包進 exe 內部的文件路徑（臨時解壓目錄）
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 解壓的臨時目錄
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    return os.path.join(base_path, relative_path)

try:
    import clr
    
    # DLL 從打包內部載入
    dll_path = get_bundled_file("LibreHardwareMonitorLib.dll")
    
    if os.path.exists(dll_path):
        clr.AddReference(dll_path)
        from LibreHardwareMonitor import Hardware
        print(f"✅ 成功載入 LibreHardwareMonitor: {dll_path}")
        LHM_AVAILABLE = True
    else:
        raise FileNotFoundError(f"找不到 DLL: {dll_path}")
        
except Exception as e:
    print(f"⚠️ LibreHardwareMonitor 載入失敗: {e}")
    LHM_AVAILABLE = False

# ---------- Configuration ----------
API_BASE_URL = "http://localhost:8000"
AUTH_SECRET_KEY = "NTCUST-ENERGY-MONITOR"
FALLBACK_TO_CSV = True

# config.yaml - 修正版
def load_config():
    """
    優先從 exe 同目錄讀取 config.yaml
    如果不存在，則從打包內部讀取（首次執行時複製出來）
    """
    # exe 同目錄的 config.yaml
    external_config = get_resource_path('config.yaml')
    # 打包內部的 config.yaml
    bundled_config = get_bundled_file('config.yaml')
    
    # 優先使用外部配置文件
    config_path = external_config if os.path.exists(external_config) else bundled_config
    
    try:
        print(f"🔍 嘗試載入設定檔: {config_path}")
        with open(config_path, 'r', encoding='utf-8') as f:
            loaded_config = yaml.safe_load(f)
            print(f"✅ 成功載入設定:")
            print(f"   API URL: {loaded_config.get('api_base_url', 'Not found')}")
            print(f"   收集間隔: {loaded_config.get('collection_interval', 60)}秒")
            
            # 首次執行：如果外部沒有 config.yaml，複製一份出來方便用戶修改
            if not os.path.exists(external_config) and getattr(sys, 'frozen', False):
                try:
                    import shutil
                    shutil.copy(bundled_config, external_config)
                    print(f"📋 已複製配置文件到: {external_config}")
                    print(f"   你可以編輯此文件來修改設定")
                except Exception as e:
                    print(f"⚠️ 無法複製配置文件: {e}")
            
            return loaded_config
            
    except FileNotFoundError:
        print(f"⚠️ 警告: config.yaml 不存在於 {config_path}")
        print(f"   將使用預設設定")
        return get_default_config()
    except Exception as e:
        print(f"❌ 載入設定檔時發生錯誤: {e}")
        return get_default_config()

def get_default_config():
    """返回預設配置"""
    return {
        'api_base_url': 'http://localhost:8000',
        'auth_secret_key': 'NTCUST-ENERGY-MONITOR',
        'collection_interval': 60,
        'fallback_to_csv': True,
        'quota': {'daily_limit_kwh': 100}
    }

config = load_config()
API_BASE_URL = config.get('api_base_url', API_BASE_URL)
AUTH_SECRET_KEY = config.get('auth_secret_key', AUTH_SECRET_KEY)
FALLBACK_TO_CSV = config.get('fallback_to_csv', FALLBACK_TO_CSV)

# ---------- Hardware Monitor Initialization ----------
computer_handle = None

def initialize_hardware_monitor():
    """初始化 LibreHardwareMonitor，只執行一次。"""
    global computer_handle
    if not LHM_AVAILABLE or computer_handle is not None:
        return

    try:
        print("🔍 正在初始化硬體監控器 (LibreHardwareMonitor)...")
        computer_handle = Hardware.Computer()
        computer_handle.IsCpuEnabled = True
        computer_handle.IsGpuEnabled = True
        computer_handle.Open()
        print("✅ 硬體監控器初始化完成。")
    except Exception as e:
        print(f"❌ 硬體監控器初始化失敗: {e}")
        computer_handle = None


# ---------- Class Schedule ----------
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

# ---------- MAC Address and Authentication ----------
def get_mac_address():
    """Get device MAC address"""
    try:
        mac = uuid.getnode()
        mac_str = ':'.join(['{:02x}'.format((mac >> elements) & 0xff) 
                           for elements in range(0,2*6,2)][::-1])
        return mac_str.upper()
    except:
        try:
            import netifaces
            interfaces = netifaces.interfaces()
            for interface in interfaces:
                if interface != 'lo':
                    addrs = netifaces.ifaddresses(interface)
                    if netifaces.AF_LINK in addrs:
                        mac = addrs[netifaces.AF_LINK][0]['addr']
                        return mac.upper().replace('-', ':')
        except:
            pass
        
        try:
            if platform.system() == "Windows":
                result = subprocess.run(['getmac'], capture_output=True, text=True)
                lines = result.stdout.split('\n')
                for line in lines:
                    if '-' in line and len(line.split('-')) == 6:
                        return line.replace('-', ':').upper().strip()
            else:
                result = subprocess.run(['ifconfig'], capture_output=True, text=True)
        except:
            pass
    
    return "00:00:00:00:00:00"

def generate_device_certificate(mac_address, secret_key):
    """Generate device certificate"""
    return hmac.new(
        secret_key.encode(), 
        mac_address.encode(), 
        hashlib.sha256
    ).hexdigest()

def get_auth_headers():
    """Get authentication headers"""
    mac_address = get_mac_address()
    certificate = generate_device_certificate(mac_address, AUTH_SECRET_KEY)
    
    return {
        "Content-Type": "application/json",
        "MAC-Address": mac_address,
        "Device-Certificate": certificate
    }

class DataCleaner:
    """資料清洗模組（簡化版）"""
    def __init__(self):
        self.window_size = 50
        self.data_windows = {}
        self.z_score_threshold = 3
        
    def clean(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """清洗資料"""
        cleaned = data.copy()
        
        # 範圍檢查
        if cleaned.get('cpu', 0) < 0:
            cleaned['cpu'] = 0
        elif cleaned.get('cpu', 0) > 100:
            cleaned['cpu'] = 100
            
        if cleaned.get('gpu', 0) < 0:
            cleaned['gpu'] = 0
        elif cleaned.get('gpu', 0) > 500:
            cleaned['gpu'] = 500
            
        # 添加標準化資料
        cleaned['system_power_kw'] = cleaned.get('system_power', 0) / 1000
        
        # 使用模式判斷
        total_util = cleaned.get('cpu', 0) * 0.4 + cleaned.get('gpu_usage', 0) * 0.4
        if total_util < 20:
            cleaned['usage_pattern'] = 'idle'
        elif total_util < 50:
            cleaned['usage_pattern'] = 'normal'
        elif total_util < 80:
            cleaned['usage_pattern'] = 'intensive'
        else:
            cleaned['usage_pattern'] = 'peak'
            
        return cleaned

# ---------- Enhanced System Info Collection ----------
def get_enhanced_system_info():
    """Collect detailed system info for device fingerprinting"""
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

# ---------- Hardware Data Collection ----------
def get_gpu_model():
    """優先使用 nvidia-smi，若失敗則改用 WMI 查詢"""
    # --- 方法一：NVIDIA ---
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=gpu_name', '--format=csv,noheader'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        )
        if result.stdout:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        # nvidia-smi 不可用，嘗試下一個方法
        pass

    # --- 方法二：WMI (適用於大多數 Windows 裝置) ---
    try:
        c = wmi.WMI()
        gpus = c.Win32_VideoController()
        if gpus:
            # 通常第一張是主要的顯示卡
            return gpus[0].Name
    except Exception:
        # WMI 查詢失敗
        pass

    return "Unknown"

def get_gpu_usage():
    """優先使用 nvidia-smi，若失敗則回傳 0 (WMI 不易取得即時使用率)"""
    # --- 方法一：NVIDIA ---
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        )
        if result.stdout:
            return float(result.stdout.strip())
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    # --- WMI 很難直接取得 GPU "使用率" ---
    # WMI 可以取得驅動版本、記憶體大小等靜態資訊，但即時的百分比使用率
    # 通常需要透過更底層的 API (如 NVAPI for NVIDIA, AGS for AMD)，
    # 這會讓程式碼變得非常複雜。

    # 因此，當 nvidia-smi 失敗時，我們暫時回傳 0 作為備援。
    return 0


def get_gpu_power_watt():
    """
    取得 GPU 功耗。
    優先順序：nvidia-smi > LibreHardwareMonitor > 估算（Intel內顯/其他）
    """
    # 方法一：NVIDIA GPU (最精確)
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=power.draw', '--format=csv,noheader,nounits'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        )
        if result.stdout:
            power_str = result.stdout.strip()
            if power_str and "N/A" not in power_str:
                power = float(power_str)
                print(f"[NVIDIA] GPU 功耗: {power}W")
                return power
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    # 方法二：LibreHardwareMonitor (適用於 AMD, Intel, NVIDIA)
    if computer_handle:
        try:
            for hardware in computer_handle.Hardware:
                if hardware.HardwareType in [Hardware.HardwareType.GpuIntel, 
                                            Hardware.HardwareType.GpuAmd, 
                                            Hardware.HardwareType.GpuNvidia]:
                    hardware.Update()
                    time.sleep(0.1)
                    
                    for sensor in hardware.Sensors:
                        if sensor.SensorType == Hardware.SensorType.Power:
                            if sensor.Value is not None and sensor.Value > 0:
                                power = round(float(sensor.Value), 2)
                                print(f"[LHM] GPU 功耗 ({sensor.Name}): {power}W")
                                return power
        except Exception as e:
            print(f"透過 LHM 取得 GPU 功耗時出錯: {e}")

    # 方法三：估算（當硬體感測器不可用時）
    gpu_usage = get_gpu_usage()
    gpu_model = get_gpu_model().lower()
    
    # Intel 內顯
    if "uhd" in gpu_model or "iris" in gpu_model or ("intel" in gpu_model and "arc" not in gpu_model):
        idle_power = 2.0
        max_power = 15.0
        
        if gpu_usage > 0:
            estimated_power = idle_power + (max_power - idle_power) * (gpu_usage / 100.0)
            print(f"[估算] Intel 內顯功耗: {estimated_power:.1f}W (使用率: {gpu_usage}%)")
            return round(estimated_power, 2)
        else:
            return idle_power
    
    # Intel Arc 獨顯
    elif "arc" in gpu_model:
        if "a770" in gpu_model:
            tdp = 225
        elif "a750" in gpu_model:
            tdp = 225
        elif "a580" in gpu_model:
            tdp = 185
        else:
            tdp = 150
        idle_power = tdp * 0.1
    
    # NVIDIA GPU (nvidia-smi 失敗時的備用)
    elif "rtx 4090" in gpu_model:
        tdp = 450
        idle_power = 20
    elif "rtx 4080" in gpu_model:
        tdp = 320
        idle_power = 18
    elif "rtx 4070" in gpu_model:
        tdp = 200
        idle_power = 15
    elif "rtx 3090" in gpu_model:
        tdp = 350
        idle_power = 20
    elif "rtx 3080" in gpu_model:
        tdp = 320
        idle_power = 18
    elif "rtx 3070" in gpu_model:
        tdp = 220
        idle_power = 15
    elif "rtx 3060" in gpu_model:
        tdp = 170
        idle_power = 12
    elif "gtx 1660" in gpu_model:
        tdp = 120
        idle_power = 10
    elif "gtx 1650" in gpu_model:
        tdp = 75
        idle_power = 8
    
    # AMD GPU
    elif "rx 7900" in gpu_model:
        tdp = 355
        idle_power = 20
    elif "rx 7800" in gpu_model:
        tdp = 263
        idle_power = 18
    elif "rx 7700" in gpu_model:
        tdp = 245
        idle_power = 15
    elif "rx 6900" in gpu_model:
        tdp = 300
        idle_power = 18
    elif "rx 6800" in gpu_model:
        tdp = 250
        idle_power = 15
    elif "rx 6700" in gpu_model:
        tdp = 230
        idle_power = 15
    elif "vega" in gpu_model:
        tdp = 295
        idle_power = 20
    
    # 通用預設值
    else:
        tdp = 75
        idle_power = 10
    
    # 根據使用率估算功耗
    if gpu_usage > 0:
        estimated_power = idle_power + (tdp - idle_power) * (gpu_usage / 100.0)
        print(f"[估算] GPU 功耗: {estimated_power:.1f}W (使用率: {gpu_usage}%, TDP: {tdp}W)")
        return round(estimated_power, 2)
    
    return idle_power

def get_cpu_power():
    """
    取得 CPU 功耗。
    優先使用 LibreHardwareMonitor，若失敗則使用 psutil 估算。
    """
    # --- 方法一：LibreHardwareMonitor (較準確) ---
    if computer_handle:
        try:
            for hardware in computer_handle.Hardware:
                if hardware.HardwareType == Hardware.HardwareType.Cpu:
                    hardware.Update()
                    for sensor in hardware.Sensors:
                        if sensor.SensorType == Hardware.SensorType.Power and "Package" in sensor.Name:
                             if sensor.Value is not None:
                                return round(float(sensor.Value), 2)
        except Exception as e:
            print(f"透過 LHM 取得 CPU 功耗時出錯: {e}")

    # --- 方法二：psutil 估算 (備援) ---
    cpu_percent = psutil.cpu_percent(interval=1)
    # 保持您原有的估算邏輯作為最終備援
    try:
        cpu_info = platform.processor().lower()
        cpu_count = psutil.cpu_count()
    except:
        cpu_info = ""
        cpu_count = 4
    
    if "intel" in cpu_info:
        base_power, max_additional = (10.0, 45.0)
    elif "amd" in cpu_info:
        base_power, max_additional = (12.0, 50.0)
    else:
        base_power, max_additional = (8.0, 40.0)
        
    additional_power = (cpu_percent / 100.0) * max_additional
    return round(base_power + additional_power, 2)


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
    """Improved system total power calculation"""
    # Memory power: DDR4/DDR5 ~3-4W per GB
    memory_gb = memory / 1024.0
    memory_power = memory_gb * 3.5
    
    # Base system power (motherboard, fans, SSD, network card, etc.)
    motherboard_power = 15.0
    cooling_power = 5.0
    storage_power = 5.0
    other_power = 10.0
    
    base_system_power = motherboard_power + cooling_power + storage_power + other_power
    
    # Calculate total power
    total_power = cpu + gpu + memory_power + base_system_power
    
    # PSU efficiency loss (assume 90% efficiency)
    efficiency_factor = 1.11  # 1/0.9
    
    final_power = total_power * efficiency_factor
    
    return round(final_power, 2)

def validate_power_readings(data):
    """Validate power readings for reasonableness"""
    limits = {
        'cpu': 125.0,
        'gpu': 500.0,
        'system_power': 800.0
    }
    
    warnings = []
    
    for key, limit in limits.items():
        if key in data and data[key] > limit:
            warnings.append(f"{key}: {data[key]}W -> {limit}W")
            data[key] = limit
    
    # Logic check: system power should not be less than CPU + GPU power
    min_system_power = data.get('cpu', 0) + data.get('gpu', 0) + 20
    if 'system_power' in data and data['system_power'] < min_system_power:
        warnings.append(f"system_power: {data['system_power']}W -> {min_system_power}W (logic adjustment)")
        data['system_power'] = min_system_power
    
    if warnings:
        print(f"Power value corrections: {', '.join(warnings)}")
    
    return data

def get_timestamp():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

def get_location():
    """Get location based on public IP address."""
    try:
        response = requests.get("http://ip-api.com/json/", timeout=5)
        if response.status_code == 200:
            data = response.json()
            city = data.get("city", "")
            country = data.get("country", "")
            if city and country:
                return f"{city}, {country}"
    except requests.exceptions.RequestException:
        pass  # Ignore connection errors
    return "Unknown"

def get_device_info():
    return (
        str(uuid.getnode()),
        getpass.getuser(),
        "v1.4.0",
        platform.system(),
        platform.version(),
        get_location()
    )

# ---------- Data Transmission ----------
def send_to_api(data):
    """Send data to ingestion-api with device fingerprinting"""
    try:
        headers = get_auth_headers()
        
        api_data = {
            # Basic energy data
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
            
            # Device fingerprint for security
            "cpu_model": data.get("cpu_model"),
            "cpu_count": data.get("cpu_count"),
            "total_memory": data.get("total_memory"),
            "disk_partitions": data.get("disk_partitions"),
            "network_interfaces": data.get("network_interfaces"),
            "platform_machine": data.get("platform_machine"),
            "platform_architecture": data.get("platform_architecture")
        }
        
        print(f"Sending data with device fingerprint to API...")
        print(f"Basic data: CPU={data['cpu']}W, GPU={data['gpu']}W, System={data['system_power']}W")
        print(f"Device fingerprint: {data.get('cpu_model', 'Unknown')} ({data.get('cpu_count', 'Unknown')} cores)")
        
        response = requests.post(
            f"{API_BASE_URL}/ingest",
            json=api_data,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            
            # Display fingerprint check results
            if "fingerprint_check" in result:
                fp_result = result["fingerprint_check"]
                risk_level = fp_result.get("risk_level", "unknown")
                message = fp_result.get("message", "")
                similarity = fp_result.get("similarity_score", 0)
                
                if risk_level == "high":
                    print(f"HIGH RISK device warning: {message} (similarity: {similarity:.2f})")
                elif risk_level == "medium":
                    print(f"Medium risk alert: {message} (similarity: {similarity:.2f})")
                else:
                    print(f"Device normal: {message} (similarity: {similarity:.2f})")
            
            print(f"Data successfully sent to API: {result.get('status', 'unknown')}")
            return True
            
        elif response.status_code == 401:
            print(f"Authentication failed: {response.json().get('detail', 'Unknown auth error')}")
            return False
        elif response.status_code == 403:
            print(f"Device not authorized: {response.json().get('detail', 'Device not authorized')}")
            print(f"   Your MAC address: {get_mac_address()}")
            print(f"   Please contact admin to add this device to whitelist")
            return False
        else:
            print(f"API response error: {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.ConnectionError:
        print(f"Cannot connect to API: {API_BASE_URL}")
        return False
    except requests.exceptions.Timeout:
        print("API request timeout")
        return False
    except Exception as e:
        print(f"Failed to send data: {str(e)}")
        return False

# ---------- CSV Backup Storage ----------
data_buffer = []
file_count = 0


def save_to_csv(row):
    global data_buffer, file_count
    data_buffer.append(row)
    if len(data_buffer) >= 50:
        # 使用 get_resource_path 確保路徑正確
        output_dir = get_resource_path("agent_logs")
        os.makedirs(output_dir, exist_ok=True)
        
        filename = os.path.join(output_dir, f"agent_data_{file_count}.csv")
        with open(filename, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            writer.writeheader()
            writer.writerows(data_buffer)
        print(f"CSV backup saved: {filename}")
        data_buffer = []
        file_count += 1

# ---------- Initialization and Health Check ----------
def check_api_connection():
    """Check API connection and verify device registration status"""
    try:
        # Check API health status
        response = requests.get(f"{API_BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            print("API service running normally")
        else:
            print(f"API health check abnormal: {response.status_code}")
    except:
        print(f"Cannot connect to API: {API_BASE_URL}")
        if FALLBACK_TO_CSV:
            print("Will use CSV backup mode")
        return False
    
    # Check device registration
    mac_address = get_mac_address()
    print(f"Device MAC address: {mac_address}")
    print(f"Device fingerprint function: Enabled")
    print(f"Power calculation: Optimized (smart estimation)")
    
    try:
        headers = get_auth_headers()
        response = requests.get(f"{API_BASE_URL}/admin/devices/{mac_address}", headers=headers, timeout=5)
        
        if response.status_code == 200:
            device_info = response.json()
            print(f"Device registered: {device_info['device_name']}")
            return True
        elif response.status_code == 404:
            print("Device not yet registered to whitelist, but fingerprint function still operational")
            return True
        else:
            print(f"Check device registration status failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"Check device registration failed: {e}")
        return False

# ---------- 主要整合類別 ----------

class IntegratedAgent:
    def __init__(self, config):
        """初始化整合後的 Agent"""
        self.config = config
        self.cleaner = DataCleaner()
        
        # 狀態管理
        self.previous_data = {}
        self.change_threshold = config.get('change_threshold', 5)
        
        # 使用者活動監測
        self.user_active = False
        self._start_input_monitoring()

    def _start_input_monitoring(self):
        """在背景執行緒中啟動使用者輸入監測"""
        def on_event(x):
            self.user_active = True

        def monitor_loop():
            try:
                # 使用 with 陳述式確保監聽器能被正確關閉
                with mouse.Listener(on_click=on_event) as m_listener, \
                     keyboard.Listener(on_press=on_event) as k_listener:
                    m_listener.join()
                    k_listener.join()
            except Exception as e:
                print(f"輸入監控啟動失敗: {e}")
        
        # 設定為守護執行緒，這樣主程式退出時它也會跟著退出
        input_thread = threading.Thread(target=monitor_loop, daemon=True)
        input_thread.start()

    def has_significant_change(self, new_data):
        """檢查是否有顯著變化"""
        if not self.previous_data:
            return True
        
        # 比較關鍵的功耗與資源使用率
        keys_to_check = ['cpu', 'gpu', 'memory']
        changes = [
            key for key in keys_to_check 
            if abs(new_data.get(key, 0) - self.previous_data.get(key, 0)) > self.change_threshold
        ]
        
        if changes:
            print(f"資料變動超過閾值: {', '.join(changes)}")
            return True
        return False

    def process_and_send_data(self):
            """
            整合了資料收集、清洗和發送的完整流程
            """
            # 1. 收集原始數據
            raw_data = self.collect_data()
            
            # 2. 驗證並修正功耗數據
            validated_data = validate_power_readings(raw_data)
            
            # 3. 清洗資料
            final_data = self.cleaner.clean(validated_data)

            # 顯示監控資訊 (已移除配額顯示)
            print(f"\n✅ 功耗監控 - CPU: {final_data['cpu']}W | GPU: {final_data['gpu']}W | 系統: {final_data['system_power']}W")

            # 4. 嘗試發送到 API
            api_success = send_to_api(final_data)
            
            # 5. 如果 API 失敗且啟用備援，則儲存到 CSV
            if not api_success and FALLBACK_TO_CSV:
                print("API 發送失敗，使用 CSV 備援儲存")
                save_to_csv(final_data)

    def collect_data(self):
        """收集所有系統和硬體數據"""
        device_id, user_id, agent_version, os_type, os_version, location = get_device_info()
        timestamp = get_timestamp()

        gpu_model = get_gpu_model()
        gpu_usage = get_gpu_usage()
        gpu_power = get_gpu_power_watt()
        cpu_power = get_cpu_power()
        memory_used = get_memory_usage()
        disk_read, disk_write = get_disk_read_write_rate(interval=1)
        system_power = get_system_power(cpu_power, gpu_power, memory_used)
        enhanced_info = get_enhanced_system_info()

        return {
            "timestamp": timestamp, "cpu": cpu_power, "gpu": gpu_power,
            "memory": memory_used, "disk_read": disk_read, "disk_write": disk_write,
            "gpu_usage": gpu_usage, "gpu_model": gpu_model, "system_power": system_power,
            "device_id": device_id, "user_id": user_id, "agent_version": agent_version,
            "os_type": os_type, "os_version": os_version, "location": location,
            **enhanced_info
        }

    def run(self):
        """啟動 Agent 的主循環"""
        print("整合版 Agent 啟動中...")
        print(f"API 地址: {API_BASE_URL}")
        print(f"版本: v2.0 (整合版)")
        
        api_available = check_api_connection()
        if not api_available and not FALLBACK_TO_CSV:
            print("API 不可用且未啟用 CSV 備援，程式結束")
            return
            
        print("開始監控...")
        
        while True:
            try:
                should_collect = is_class_time()
                if not should_collect and self.user_active:
                    print("偵測到使用者活動，進行一次資料收集...")
                    should_collect = True
                    self.user_active = False # 重置活動標記

                if should_collect:
                    current_snapshot = {
                        "cpu": get_cpu_power(),
                        "gpu": get_gpu_power_watt(),
                        "memory": get_memory_usage()
                    }
                    
                    if self.has_significant_change(current_snapshot):
                        self.process_and_send_data()
                        self.previous_data = current_snapshot
                    else:
                        print("數據無顯著變化，跳過本次傳送...")
                
                # 使用 config.yaml 中的間隔時間
                time.sleep(self.config.get('collection_interval', 60))

            except KeyboardInterrupt:
                print("\nAgent 停止運行")
                break
            except Exception as e:
                print(f"主循環發生錯誤: {e}")
                time.sleep(60)

# ---------- 主執行點 ----------

if __name__ == "__main__":
    # 在 Agent 啟動前，先初始化硬體監控器
    initialize_hardware_monitor() 
    
    # 建立 Agent 實例並傳入設定
    agent = IntegratedAgent(config)
    # 執行 Agent
    agent.run()