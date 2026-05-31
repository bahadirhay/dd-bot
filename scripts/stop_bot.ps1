# Çalışan tüm bot python süreçlerini listele / kapat
Write-Host "Bot klasoru: $PSScriptRoot\.."
Get-Process python* -ErrorAction SilentlyContinue | ForEach-Object {
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
    if ($cmd -match "main\.py" -or $cmd -match "Desktop\\bot") {
        Write-Host "PID $($_.Id): $cmd"
    }
}
$pidFile = Join-Path $PSScriptRoot "..\data\bot.pid"
if (Test-Path $pidFile) {
    $botPid = (Get-Content $pidFile -Raw).Trim()
    Write-Host "bot.pid = $botPid"
    if ($botPid -match '^\d+$') {
        Stop-Process -Id ([int]$botPid) -Force -ErrorAction SilentlyContinue
        Write-Host "Durduruldu: $botPid"
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
Write-Host "Simdi tek terminal: python main.py"
