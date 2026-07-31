# start_all.ps1 - Elektrik/reboot sonrasi TUM sistemi ayaga kaldir + kacirlan veriyi yakala.
# Kullanim:  powershell -ExecutionPolicy Bypass -File .\start_all.ps1
# GUVENLI: her bileseni once kontrol eder, ZATEN calisiyorsa atlar (cift bot = ayni pozisyonu
# iki kez yonetir, RISKLI). Idempotent - tekrar calistirmak zarar vermez.
#
# Bot (main.py) acilista OTOMATIK yapar: kline backfill (kacirlan fiyat verisi) + pozisyon/PnL
# reconcile (borsadan, kesinti sirasinda degisen ne varsa) + 8050 dashboard. Bu script ayrica
# multichart (8055) paneli baslatir ve cok-coin shadow'u gunceller (kacirlan barlar).

$ErrorActionPreference = "Continue"
$botDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $botDir
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = "."
$py = "C:\Users\BH\AppData\Local\Programs\Python\Python310\python.exe"
New-Item -ItemType Directory -Force -Path "data\logs" | Out-Null

function Test-PyRunning($pat) {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -like "*$pat*" }
    return [bool]$p
}
function Test-SupRunning {
    $p = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match '-File.*run_bot' }
    return [bool]$p
}
function Test-Port($port) {
    try { Invoke-WebRequest -Uri "http://127.0.0.1:$port" -TimeoutSec 5 -UseBasicParsing | Out-Null; return $true }
    catch { return $false }
}

Write-Host "=== ELEKTRIK/REBOOT SONRASI BASLATMA ==="

# 1) BOT (supervisor uzerinden). Bot kendi icinde: 8050 panel + kline backfill + pozisyon reconcile.
if ((Test-PyRunning "main.py") -or (Test-SupRunning)) {
    Write-Host "[1] Bot ZATEN calisiyor -> atlandi (cift bot riskli)"
} else {
    Write-Host "[1] Bot baslatiliyor (supervisor run_bot.ps1)..."
    Start-Process powershell -ArgumentList "-ExecutionPolicy","Bypass","-File",".\run_bot.ps1" -WindowStyle Minimized
    Write-Host "    backfill + reconcile + 8050 panel icin ~20sn bekleniyor..."
    Start-Sleep -Seconds 20
}

# 2) Multichart grid paneli (8055) - ayri surec.
if (Test-PyRunning "multichart.py") {
    Write-Host "[2] Grid panel (8055) ZATEN calisiyor -> atlandi"
} else {
    Write-Host "[2] Grid panel (8055) baslatiliyor..."
    Start-Process $py -ArgumentList "dashboard\multichart.py" -WindowStyle Minimized `
        -RedirectStandardOutput "data\logs\multichart.log" -RedirectStandardError "data\logs\multichart.err"
    Start-Sleep -Seconds 4
}

# 3) SOL/LINK cok-coin shadow - kacirlan barlari yakala (deterministik, idempotent).
Write-Host "[3] Cok-coin shadow guncelleniyor (kacirlan barlar yakalaniyor)..."
& $py scripts\d_multicoin_shadow.py

# 4) DURUM OZETI
Start-Sleep -Seconds 2
Write-Host ""
Write-Host "=== DURUM ==="
if (Test-PyRunning "main.py") { Write-Host "  Bot         : CALISIYOR" } else { Write-Host "  Bot         : YOK (!) - run_bot.ps1 loguna bak" }
if (Test-Port 8050) { Write-Host "  Ana panel   : http://localhost:8050  UP" } else { Write-Host "  Ana panel   : henuz DOWN (bot aciliyorsa 10-20sn daha bekle)" }
if (Test-Port 8055) { Write-Host "  Grid panel  : http://localhost:8055  UP" } else { Write-Host "  Grid panel  : DOWN (!) - data\logs\multichart.err bak" }
Write-Host ""
Write-Host "Kacirlan ~1sa veri: bot acilista kline backfill + borsa pozisyon/PnL reconcile yapti."
Write-Host "Shadow (SOL/LINK) kacirlan barlarla guncellendi. Panelleri tarayicida ac."
