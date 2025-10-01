# install_energy_agent_final.ps1
# Final installation script with correct NSSM path

Write-Host "=== Energy Agent Service Installation ===" -ForegroundColor Cyan

# Check Administrator
if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "ERROR: Please run as Administrator" -ForegroundColor Red
    Read-Host "Press Enter to exit..."
    exit 1
}

# Define paths
$nssmPath = "C:\Users\alisa\Downloads\nssm-2.24-101-g897c7ad\nssm-2.24-101-g897c7ad\win64\nssm.exe"
$agentDir = "C:\Users\alisa\OneDrive\Desktop\cc\project-root\agent"
$agentFile = "$agentDir\agent_with_auth.py"
$serviceName = "EnergyAgent"

# Find Python
$pythonPaths = @(
    "C:\Python\python.exe",
    "C:\Python39\python.exe",
    "C:\Python310\python.exe",
    "C:\Python311\python.exe",
    "C:\Python312\python.exe"
)

$pythonPath = ""
foreach ($path in $pythonPaths) {
    if (Test-Path $path) {
        $pythonPath = $path
        break
    }
}

if (-not $pythonPath) {
    try {
        $pythonPath = (Get-Command python).Source
    } catch {
        Write-Host "ERROR: Python not found" -ForegroundColor Red
        Read-Host "Press Enter to exit..."
        exit 1
    }
}

Write-Host "Configuration:" -ForegroundColor Yellow
Write-Host "  NSSM: $nssmPath" -ForegroundColor White
Write-Host "  Python: $pythonPath" -ForegroundColor White
Write-Host "  Agent: $agentFile" -ForegroundColor White
Write-Host "  Service: $serviceName" -ForegroundColor White

# Verify all files exist
if (-not (Test-Path $nssmPath)) {
    Write-Host "ERROR: NSSM not found at $nssmPath" -ForegroundColor Red
    Read-Host "Press Enter to exit..."
    exit 1
}

if (-not (Test-Path $pythonPath)) {
    Write-Host "ERROR: Python not found at $pythonPath" -ForegroundColor Red
    Read-Host "Press Enter to exit..."
    exit 1
}

if (-not (Test-Path $agentFile)) {
    Write-Host "ERROR: Agent file not found at $agentFile" -ForegroundColor Red
    Read-Host "Press Enter to exit..."
    exit 1
}

Write-Host "`nAll files verified!" -ForegroundColor Green

# Test NSSM
Write-Host "`nTesting NSSM..." -ForegroundColor Yellow
try {
    $nssmOutput = & $nssmPath 2>&1
    Write-Host "NSSM is working!" -ForegroundColor Green
} catch {
    Write-Host "ERROR: NSSM test failed: $($_.Exception.Message)" -ForegroundColor Red
    Read-Host "Press Enter to exit..."
    exit 1
}

# Check if service already exists
$existingService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "`nWARNING: Service $serviceName already exists" -ForegroundColor Yellow
    $response = Read-Host "Remove and reinstall? (y/N)"
    if ($response -eq "y" -or $response -eq "Y") {
        Write-Host "Removing existing service..." -ForegroundColor Yellow
        try {
            & $nssmPath stop $serviceName 2>$null
            & $nssmPath remove $serviceName confirm 2>$null
            Write-Host "Existing service removed" -ForegroundColor Green
        } catch {
            Write-Host "Warning: Could not remove existing service completely" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Installation cancelled" -ForegroundColor Yellow
        Read-Host "Press Enter to exit..."
        exit 0
    }
}

Write-Host "`nInstalling Energy Agent service..." -ForegroundColor Yellow

try {
    # Step 1: Install service
    Write-Host "Step 1: Installing service..." -ForegroundColor Cyan
    $result = & $nssmPath install $serviceName $pythonPath $agentFile 2>&1
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Service installation output: $result" -ForegroundColor Yellow
        if ($result -like "*already exists*") {
            Write-Host "Service already exists, continuing with configuration..." -ForegroundColor Yellow
        } else {
            throw "Failed to install service. Exit code: $LASTEXITCODE"
        }
    } else {
        Write-Host "Service installed successfully!" -ForegroundColor Green
    }
    
    # Step 2: Set working directory
    Write-Host "Step 2: Setting working directory..." -ForegroundColor Cyan
    & $nssmPath set $serviceName AppDirectory $agentDir
    
    # Step 3: Set description
    Write-Host "Step 3: Setting description..." -ForegroundColor Cyan
    & $nssmPath set $serviceName Description "Energy Monitoring Agent - Power Consumption Monitor"
    
    # Step 4: Set startup type
    Write-Host "Step 4: Setting startup type..." -ForegroundColor Cyan
    & $nssmPath set $serviceName Start SERVICE_AUTO_START
    
    # Step 5: Create logs directory
    Write-Host "Step 5: Creating logs directory..." -ForegroundColor Cyan
    $logsDir = "$agentDir\logs"
    New-Item -Path $logsDir -ItemType Directory -Force | Out-Null
    
    # Step 6: Set log files
    Write-Host "Step 6: Setting log files..." -ForegroundColor Cyan
    & $nssmPath set $serviceName AppStdout "$logsDir\agent_output.log"
    & $nssmPath set $serviceName AppStderr "$logsDir\agent_error.log"
    
    # Step 7: Set restart options
    Write-Host "Step 7: Setting restart options..." -ForegroundColor Cyan
    & $nssmPath set $serviceName AppRestartDelay 5000
    
    Write-Host "`nService configuration completed!" -ForegroundColor Green
    
    # Step 8: Start service
    Write-Host "Step 8: Starting service..." -ForegroundColor Cyan
    & $nssmPath start $serviceName
    
    # Wait for service to start
    Write-Host "Waiting for service to start..." -ForegroundColor Yellow
    Start-Sleep 5
    
    # Check service status
    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($service) {
        Write-Host "`nService Status: $($service.Status)" -ForegroundColor $(if ($service.Status -eq "Running") { "Green" } else { "Yellow" })
        
        if ($service.Status -eq "Running") {
            Write-Host "`n🎉 SUCCESS! Energy Agent is running as a Windows service!" -ForegroundColor Green
            
            Write-Host "`nService Information:" -ForegroundColor Cyan
            Write-Host "  Name: $serviceName" -ForegroundColor White
            Write-Host "  Status: $($service.Status)" -ForegroundColor White
            Write-Host "  Display Name: $($service.DisplayName)" -ForegroundColor White
            Write-Host "  Start Type: $($service.StartType)" -ForegroundColor White
            
            Write-Host "`nFile Locations:" -ForegroundColor Cyan
            Write-Host "  Agent: $agentFile" -ForegroundColor White
            Write-Host "  Logs: $logsDir" -ForegroundColor White
            Write-Host "  Output Log: $logsDir\agent_output.log" -ForegroundColor White
            Write-Host "  Error Log: $logsDir\agent_error.log" -ForegroundColor White
            
            Write-Host "`nManagement Commands:" -ForegroundColor Yellow
            Write-Host "  Start:    `"$nssmPath`" start $serviceName" -ForegroundColor Gray
            Write-Host "  Stop:     `"$nssmPath`" stop $serviceName" -ForegroundColor Gray
            Write-Host "  Restart:  `"$nssmPath`" restart $serviceName" -ForegroundColor Gray
            Write-Host "  Remove:   `"$nssmPath`" remove $serviceName confirm" -ForegroundColor Gray
            Write-Host "  Status:   Get-Service $serviceName" -ForegroundColor Gray
            
            Write-Host "`nMonitoring Commands:" -ForegroundColor Yellow
            Write-Host "  View Output: Get-Content '$logsDir\agent_output.log' -Tail 20" -ForegroundColor Gray
            Write-Host "  View Errors: Get-Content '$logsDir\agent_error.log' -Tail 20" -ForegroundColor Gray
            Write-Host "  Follow Logs: Get-Content '$logsDir\agent_output.log' -Wait" -ForegroundColor Gray
            
        } else {
            Write-Host "`nWARNING: Service installed but not running properly" -ForegroundColor Yellow
            Write-Host "Let's check the error log..." -ForegroundColor Yellow
            
            Start-Sleep 2
            if (Test-Path "$logsDir\agent_error.log") {
                Write-Host "`nError Log (last 10 lines):" -ForegroundColor Red
                Get-Content "$logsDir\agent_error.log" -Tail 10 | ForEach-Object {
                    Write-Host "  $_" -ForegroundColor Red
                }
            }
            
            Write-Host "`nTroubleshooting steps:" -ForegroundColor Yellow
            Write-Host "1. Check if Python packages are installed:" -ForegroundColor White
            Write-Host "   pip install pynput psutil requests" -ForegroundColor Gray
            Write-Host "2. Test the agent manually:" -ForegroundColor White
            Write-Host "   python $agentFile" -ForegroundColor Gray
            Write-Host "3. Check the full error log:" -ForegroundColor White
            Write-Host "   Get-Content '$logsDir\agent_error.log'" -ForegroundColor Gray
        }
    } else {
        Write-Host "ERROR: Could not retrieve service information" -ForegroundColor Red
    }
    
} catch {
    Write-Host "`nERROR during installation: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Please check the error details above and try again" -ForegroundColor Yellow
}

Read-Host "`nPress Enter to exit..."