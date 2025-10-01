# test_agent.py - 簡化版本用於測試
import time
import psutil
import traceback

print("簡化版 Agent 啟動...")

try:
    while True:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        print(f"[{time.strftime('%H:%M:%S')}] CPU: {cpu_percent}%, Memory: {memory.percent}%")
        time.sleep(30)
except KeyboardInterrupt:
    print("收到停止信號")
except Exception as e:
    print(f"錯誤: {e}")
    print(traceback.format_exc())
