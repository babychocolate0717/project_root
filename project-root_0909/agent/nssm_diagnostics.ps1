# nssm_diagnostics.ps1
# Complete diagnostic script for Energy Agent NSSM service

param(
    [switch]$ShowLogs,
    [int]$LogLines = 20,
    [switch]$WatchLogs
)

# Configuration
$nssmPath = "C:\Users\alisa\Downloads\nssm-2.24-101-g897c7ad\nssm-2.24-101-g897c7ad\win64\nssm.exe"
$serviceName = "EnergyAgent"
$logsDir = "C:\Users\alisa\OneDrive\Desktop\cc\project-root\agent\logs"
$outputLog = "$logsDir\agent_output.log"
$errorLog = "$logsDir\agent_error.log"

Write-Host "=== Energy Agent Service Diagnostics ===" -ForegroundColor Cyan
Write-Host "Time: $(Get-Date)" -ForegroundColor White

# Function to display colored status
function Show-Status {
    param($Status)
    switch ($Status) {
        "Running" { Write-Host "🟢 $Status" -ForegroundColor Green }
        "Stopped" { Write-Host "🔴 $Status" -ForegroundColor Red }
        "Paused" { Write-Host "🟡 $Status (Usually indicates error)" -ForegroundColor Yellow }
        "StartPending" { Write-Host "🟡 $Status" -ForegroundColor Yellow }
        "StopPending" { Write-Host "🟡 $Status" -ForegroundColor Yellow }
        default { Write-Host "❓ $Status" -ForegroundColor Gray }
    }
}

# 1. Basic Service Status
Write-Host "`n1. SERVICE STATUS" -ForegroundColor Yellow
Write-Host "=" * 50 -ForegroundColor Yellow

try {
    $service = Get-Service -Name $serviceName -ErrorAction Stop
    Write-Host "Service Name: $($service.Name)" -ForegroundColor White
    Write-Host "Display Name: $($service.DisplayName)" -ForegroundColor White
    Write-Host "Status: " -NoNewline -ForegroundColor White
    Show-Status $service.Status
    Write-Host "Start Type: $($service.StartType)" -ForegroundColor White
    Write-Host "Can Stop: $($service.CanStop)" -ForegroundColor White
    Write-Host "Can Pause: $($service.CanPauseAndContinue)" -ForegroundColor White
} catch {
    Write-Host "❌ Service '$serviceName' not found or error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# 2. NSSM Specific Status
Write-Host "`n2. NSSM STATUS" -ForegroundColor Yellow
Write-Host "=" * 50 -ForegroundColor Yellow

if (Test-Path $nssmPath) {
    try {
        $nssmStatus = & $nssmPath status $serviceName 2>&1
        Write-Host "NSSM Status: $nssmStatus" -ForegroundColor White
        
        # Get NSSM configuration
        Write-Host "`nNSSM Configuration:" -ForegroundColor Cyan
        $appPath = & $nssmPath get $serviceName Application 2>$null
        $appDir = & $nssmPath get $serviceName AppDirectory 2>$null
        $appArgs = & $nssmPath get $serviceName AppParameters 2>$null
        
        Write-Host "  Application: $appPath" -ForegroundColor White
        Write-Host "  Directory: $appDir" -ForegroundColor White
        Write-Host "  Arguments: $appArgs" -ForegroundColor White
        
    } catch {
        Write-Host "❌ Error getting NSSM status: $($_.Exception.Message)" -ForegroundColor Red
    }
} else {
    Write-Host "❌ NSSM not found at: $nssmPath" -ForegroundColor Red
}

# 3. Process Information
Write-Host "`n3. PROCESS INFORMATION" -ForegroundColor Yellow
Write-Host "=" * 50 -ForegroundColor Yellow

$pythonProcesses = Get-Process -Name "python*" -ErrorAction SilentlyContinue | 
    Where-Object { $_.CommandLine -like "*agent_with_auth*" }

if ($pythonProcesses) {
    Write-Host "🟢 Found Energy Agent processes:" -ForegroundColor Green
    foreach ($proc in $pythonProcesses) {
        Write-Host "  PID: $($proc.Id)" -ForegroundColor White
        Write-Host "  CPU: $($proc.CPU)" -ForegroundColor White
        Write-Host "  Memory: $([math]::Round($proc.WorkingSet64/1MB, 2)) MB" -ForegroundColor White
        Write-Host "  Start Time: $($proc.StartTime)" -ForegroundColor White
        if ($proc.CommandLine) {
            Write-Host "  Command: $($proc.CommandLine)" -ForegroundColor Gray
        }
        Write-Host ""
    }
} else {
    Write-Host "🔴 No Energy Agent Python processes found" -ForegroundColor Red
}

# 4. Log Files Status
Write-Host "`n4. LOG FILES STATUS" -ForegroundColor Yellow
Write-Host "=" * 50 -ForegroundColor Yellow

Write-Host "Log Directory: $logsDir" -ForegroundColor White

if (Test-Path $logsDir) {
    Write-Host "🟢 Log directory exists" -ForegroundColor Green
    
    # Output log
    if (Test-Path $outputLog) {
        $outputInfo = Get-Item $outputLog
        Write-Host "🟢 Output Log: $outputLog" -ForegroundColor Green
        Write-Host "  Size: $([math]::Round($outputInfo.Length/1KB, 2)) KB" -ForegroundColor White
        Write-Host "  Last Modified: $($outputInfo.LastWriteTime)" -ForegroundColor White
    } else {
        Write-Host "🔴 Output log not found: $outputLog" -ForegroundColor Red
    }
    
    # Error log
    if (Test-Path $errorLog) {
        $errorInfo = Get-Item $errorLog
        Write-Host "🟢 Error Log: $errorLog" -ForegroundColor Green
        Write-Host "  Size: $([math]::Round($errorInfo.Length/1KB, 2)) KB" -ForegroundColor White
        Write-Host "  Last Modified: $($errorInfo.LastWriteTime)" -ForegroundColor White
    } else {
        Write-Host "🔴 Error log not found: $errorLog" -ForegroundColor Red
    }
} else {
    Write-Host "🔴 Log directory not found: $logsDir" -ForegroundColor Red
}

# 5. Windows Event Logs
Write-Host "`n5. WINDOWS EVENT LOGS" -ForegroundColor Yellow
Write-Host "=" * 50 -ForegroundColor Yellow

try {
    # System events related to the service
    $systemEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System'
        ProviderName = 'Service Control Manager'
        StartTime = (Get-Date).AddDays(-1)
    } -MaxEvents 50 -ErrorAction SilentlyContinue | 
    Where-Object { $_.Message -like "*$serviceName*" }
    
    if ($systemEvents) {
        Write-Host "🟢 Recent system events for $serviceName (last 24 hours):" -ForegroundColor Green
        $systemEvents | Select-Object -First 5 | ForEach-Object {
            $level = switch ($_.LevelDisplayName) {
                "Error" { "🔴" }
                "Warning" { "🟡" }
                "Information" { "🟢" }
                default { "ℹ️" }
            }
            Write-Host "  $level [$($_.TimeCreated.ToString('HH:mm:ss'))] $($_.LevelDisplayName): $($_.Message.Split("`n")[0])" -ForegroundColor White
        }
    } else {
        Write-Host "ℹ️ No recent system events found for $serviceName" -ForegroundColor Gray
    }
} catch {
    Write-Host "⚠️ Could not retrieve system events: $($_.Exception.Message)" -ForegroundColor Yellow
}

# 6. Recent Log Content
if ($ShowLogs -or $PSBoundParameters.Count -eq 0) {
    Write-Host "`n6. RECENT LOG CONTENT" -ForegroundColor Yellow
    Write-Host "=" * 50 -ForegroundColor Yellow
    
    if (Test-Path $outputLog) {
        Write-Host "`nOutput Log (last $LogLines lines):" -ForegroundColor Cyan
        try {
            $outputContent = Get-Content $outputLog -Tail $LogLines -ErrorAction Stop
            if ($outputContent) {
                $outputContent | ForEach-Object {
                    Write-Host "  $_" -ForegroundColor White
                }
            } else {
                Write-Host "  (Output log is empty)" -ForegroundColor Gray
            }
        } catch {
            Write-Host "  Error reading output log: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
    
    if (Test-Path $errorLog) {
        Write-Host "`nError Log (last $LogLines lines):" -ForegroundColor Red
        try {
            $errorContent = Get-Content $errorLog -Tail $LogLines -ErrorAction Stop
            if ($errorContent) {
                $errorContent | ForEach-Object {
                    Write-Host "  $_" -ForegroundColor Red
                }
            } else {
                Write-Host "  (Error log is empty)" -ForegroundColor Gray
            }
        } catch {
            Write-Host "  Error reading error log: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
}

# 7. Troubleshooting Recommendations
Write-Host "`n7. TROUBLESHOOTING RECOMMENDATIONS" -ForegroundColor Yellow
Write-Host "=" * 50 -ForegroundColor Yellow

if ($service.Status -ne "Running") {
    Write-Host "🔧 Service is not running. Try these steps:" -ForegroundColor Yellow
    Write-Host "  1. Start service: Start-Service $serviceName" -ForegroundColor White
    Write-Host "  2. Or use NSSM: `"$nssmPath`" start $serviceName" -ForegroundColor White
    Write-Host "  3. Check error logs for issues" -ForegroundColor White
}

if ((Test-Path $errorLog) -and (Get-Item $errorLog).Length -gt 0) {
    Write-Host "🔧 Error log has content. Check for:" -ForegroundColor Yellow
    Write-Host "  1. Missing Python modules (pip install pynput psutil requests)" -ForegroundColor White
    Write-Host "  2. Python path issues" -ForegroundColor White
    Write-Host "  3. Permission problems" -ForegroundColor White
}

Write-Host "`n8. USEFUL COMMANDS" -ForegroundColor Yellow
Write-Host "=" * 50 -ForegroundColor Yellow

Write-Host "Service Management:" -ForegroundColor Cyan
Write-Host "  Start:    Start-Service $serviceName" -ForegroundColor Gray
Write-Host "  Stop:     Stop-Service $serviceName" -ForegroundColor Gray  
Write-Host "  Restart:  Restart-Service $serviceName" -ForegroundColor Gray
Write-Host "  Status:   Get-Service $serviceName" -ForegroundColor Gray

Write-Host "`nNSSM Management:" -ForegroundColor Cyan
Write-Host "  Start:    `"$nssmPath`" start $serviceName" -ForegroundColor Gray
Write-Host "  Stop:     `"$nssmPath`" stop $serviceName" -ForegroundColor Gray
Write-Host "  Status:   `"$nssmPath`" status $serviceName" -ForegroundColor Gray
Write-Host "  Restart:  `"$nssmPath`" restart $serviceName" -ForegroundColor Gray

Write-Host "`nLog Monitoring:" -ForegroundColor Cyan
Write-Host "  View Output: Get-Content '$outputLog' -Tail 20" -ForegroundColor Gray
Write-Host "  View Errors: Get-Content '$errorLog' -Tail 20" -ForegroundColor Gray
Write-Host "  Watch Live:  Get-Content '$outputLog' -Wait" -ForegroundColor Gray

Write-Host "`nDiagnostics:" -ForegroundColor Cyan
Write-Host "  Full Diagnostic: .\nssm_diagnostics.ps1 -ShowLogs" -ForegroundColor Gray
Write-Host "  Watch Logs: .\nssm_diagnostics.ps1 -WatchLogs" -ForegroundColor Gray

# 9. Watch Logs (if requested)
if ($WatchLogs) {
    Write-Host "`n9. LIVE LOG MONITORING (Ctrl+C to exit)" -ForegroundColor Yellow
    Write-Host "=" * 50 -ForegroundColor Yellow
    
    if (Test-Path $outputLog) {
        Write-Host "Watching: $outputLog" -ForegroundColor Cyan
        Get-Content $outputLog -Wait
    } else {
        Write-Host "Output log not found: $outputLog" -ForegroundColor Red
    }
}

Write-Host "`n=== Diagnostic Complete ===" -ForegroundColor Cyan