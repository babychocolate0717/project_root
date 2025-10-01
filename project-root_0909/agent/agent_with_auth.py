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

# ---------- Configuration ----------
API_BASE_URL = "http://localhost:8000"
AUTH_SECRET_KEY = "NTCUST-ENERGY-MONITOR"
FALLBACK_TO_CSV = True

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
    """Get GPU power consumption in Watts"""
    try:
        # Method 1: Direct from nvidia-smi
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
    
    # Method 2: Estimate based on usage
    gpu_usage = get_gpu_usage()
    gpu_model = get_gpu_model().lower()
    
    # Set power ranges based on GPU model
    if "mx250" in gpu_model:
        base_power, max_power = 5.0, 25.0
    elif "rtx" in gpu_model:
        if "4090" in gpu_model:
            base_power, max_power = 50.0, 450.0
        elif "4080" in gpu_model:
            base_power, max_power = 40.0, 320.0
        elif "4070" in gpu_model:
            base_power, max_power = 30.0, 200.0
        else:
            base_power, max_power = 25.0, 250.0
    elif "gtx" in gpu_model:
        base_power, max_power = 15.0, 180.0
    else:
        base_power, max_power = 10.0, 75.0
    
    additional_power = (gpu_usage / 100.0) * (max_power - base_power)
    estimated_power = base_power + additional_power
    
    return round(estimated_power, 2)

def get_cpu_power():
    """Improved CPU power calculation"""
    cpu_percent = psutil.cpu_percent(interval=1)
    
    try:
        cpu_info = platform.processor().lower()
        cpu_count = psutil.cpu_count()
    except:
        cpu_info = ""
        cpu_count = 4
    
    # Estimate power based on CPU type and core count
    if "intel" in cpu_info:
        if "i9" in cpu_info or "xeon" in cpu_info:
            base_power = 15.0 + (cpu_count * 2)
            max_additional = 50.0 + (cpu_count * 5)
        elif "i7" in cpu_info:
            base_power = 12.0 + (cpu_count * 1.5)
            max_additional = 35.0 + (cpu_count * 4)
        elif "i5" in cpu_info:
            base_power = 10.0 + (cpu_count * 1.2)
            max_additional = 25.0 + (cpu_count * 3)
        else:
            base_power = 8.0 + (cpu_count * 1)
            max_additional = 20.0 + (cpu_count * 2.5)
    elif "amd" in cpu_info:
        if "ryzen 9" in cpu_info or "threadripper" in cpu_info:
            base_power = 15.0 + (cpu_count * 1.8)
            max_additional = 45.0 + (cpu_count * 4.5)
        elif "ryzen 7" in cpu_info:
            base_power = 12.0 + (cpu_count * 1.4)
            max_additional = 30.0 + (cpu_count * 3.5)
        elif "ryzen 5" in cpu_info:
            base_power = 10.0 + (cpu_count * 1.2)
            max_additional = 25.0 + (cpu_count * 3)
        else:
            base_power = 8.0 + (cpu_count * 1)
            max_additional = 20.0 + (cpu_count * 2.5)
    else:
        base_power = 10.0 + (cpu_count * 1)
        max_additional = 25.0 + (cpu_count * 3)
    
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

def get_device_info():
    return (
        str(uuid.getnode()),
        getpass.getuser(),
        "v1.4.0",
        platform.system(),
        platform.version(),
        "Taipei, Taiwan"
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
        print(f"CSV backup saved: {filename}")
        data_buffer = []
        file_count += 1

# ---------- Data Processing and Storage ----------
def process_and_send_data():
    """Process and send data with optimized power calculations"""
    device_id, user_id, agent_version, os_type, os_version, location = get_device_info()
    timestamp = get_timestamp()

    # Collect hardware data
    gpu_model = get_gpu_model()
    gpu_usage = get_gpu_usage()
    gpu_power = get_gpu_power_watt()
    cpu_power = get_cpu_power()
    memory_used = get_memory_usage()
    disk_read, disk_write = get_disk_read_write_rate(interval=1)
    system_power = get_system_power(cpu_power, gpu_power, memory_used)

    # Collect enhanced system info
    enhanced_info = get_enhanced_system_info()

    # Prepare data
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
        
        # Enhanced system info
        **enhanced_info
    }

    # Validate and correct power data
    data = validate_power_readings(data)

    # Display improved power info
    print(f"\nPower Monitor - CPU: {data['cpu']}W | GPU: {data['gpu']}W | System: {data['system_power']}W")
    print(f"Memory: {data['memory']:.1f}MB ({data['memory']/1024:.1f}GB)")
    print(f"GPU: {data['gpu_model']} ({data['gpu_usage']}%)")

    print("\n========== Complete Data Output ==========")
    for k, v in data.items():
        if isinstance(v, float):
            print(f"{k}: {v:.2f}")
        else:
            print(f"{k}: {v}")
    
    # Try to send to API
    api_success = send_to_api(data)
    
    # If API fails and backup enabled, save to CSV
    if not api_success and FALLBACK_TO_CSV:
        print("API send failed, using CSV backup storage")
        save_to_csv(data)
    
    return api_success

# ---------- Change Detection ----------
previous_data = {"cpu": 0, "gpu": 0, "memory": 0, "disk_read": 0, "disk_write": 0}
CHANGE_THRESHOLD = 5

def has_significant_change(new, old):
    changes = [k for k in new if abs(new[k] - old[k]) > CHANGE_THRESHOLD]
    if changes:
        print(f"Data change threshold exceeded: {', '.join(changes)}")
        return True
    return False

# ---------- User Activity Detection ----------
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
        print(f"Input monitoring startup failed: {e}")

threading.Thread(target=monitor_input, daemon=True).start()

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

# ---------- Main Loop ----------
def main():
    global user_active, previous_data 
    
    print("Agent starting...")
    print(f"API address: {API_BASE_URL}")
    print(f"MAC address: {get_mac_address()}")
    print(f"Version: v1.4.0 (smart power calculation)")
    
    # Initialization check
    api_available = check_api_connection()
    
    if not api_available and not FALLBACK_TO_CSV:
        print("API unavailable and CSV backup not enabled, program ending")
        return
    
    print("Starting monitoring...")
    
    while True:
        try:
            in_class = is_class_time()
            should_grab = False

            if in_class:
                should_grab = True
                print("Class time, continuous monitoring")
            elif user_active:
                should_grab = True
                print("User activity detected")
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
            print("\nAgent stopped")
            break
        except Exception as e:
            print(f"Runtime error: {e}")
            time.sleep(60)  # Wait then retry

# ---------- Startup ----------
if __name__ == "__main__":
    main()