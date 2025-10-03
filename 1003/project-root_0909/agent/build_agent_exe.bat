@echo off
echo ========================================
echo Energy Monitor Agent 打包工具
echo ========================================
echo.

REM 檢查 Python 是否安裝
python --version >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 找不到 Python，請先安裝 Python 3.8+
    pause
    exit /b 1
)

echo [1/5] 檢查必要套件...
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [安裝] PyInstaller 未安裝，正在安裝...
    pip install pyinstaller
)

echo.
echo [2/5] 清理舊的打包檔案...
if exist "dist" rmdir /s /q dist
if exist "build" rmdir /s /q build
if exist "*.spec" del /q *.spec

echo.
echo [3/5] 檢查 config.yaml 是否存在...
if not exist "config.yaml" (
    echo [警告] config.yaml 不存在！
    echo 請確保 config.yaml 與此腳本在同一目錄
    pause
    exit /b 1
)

echo [確認] config.yaml 內容：
type config.yaml
echo.

echo [4/5] 開始打包（這可能需要幾分鐘）...
pyinstaller --onefile ^
    --add-data "config.yaml;." ^
    --name "EnergyMonitorAgent" ^
    --console ^
    --icon=NONE ^
    integrated_agent.py

if errorlevel 1 (
    echo.
    echo [錯誤] 打包失敗！
    pause
    exit /b 1
)

echo.
echo [5/5] 複製必要檔案到 dist 目錄...
copy config.yaml dist\
if exist "agent_logs" xcopy /E /I agent_logs dist\agent_logs

echo.
echo ========================================
echo 打包完成！
echo ========================================
echo.
echo 執行檔位置: dist\EnergyMonitorAgent.exe
echo 設定檔位置: dist\config.yaml
echo.
echo 你可以將整個 dist 資料夾複製到其他電腦使用
echo.
pause