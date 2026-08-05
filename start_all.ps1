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
    Write-Host "[1] Bot baslatiliyor (supervisor run_bot.ps1) - GORUNUR pencere (akisi izle)..."
    Start-Process powershell -ArgumentList "-ExecutionPolicy","Bypass","-File",".\run_bot.ps1" -WindowStyle Normal
    Write-Host "    backfill + reconcile + 8050 panel icin ~20sn bekleniyor..."
    Start-Sleep -Seconds 20
}

# 2) Ayri panel surecleri (bot disinda, hepsi read-only/ayri port). Liste -> yeni panel ekle.
$panels = @(
    @{ name="Grid cok-coin"; script="dashboard\multichart.py";          port=8055; log="multichart" },
    @{ name="DumpFade";      script="dashboard\dumpfade_live_panel.py";  port=8056; log="dumpfade_panel" },
    @{ name="Strateji F";    script="dashboard\ftsm_live_panel.py";      port=8057; log="ftsm_panel" },
    @{ name="Rejim izleme";  script="dashboard\regime_panel.py";         port=8058; log="regime_panel" }
)
foreach ($pn in $panels) {
    $base = Split-Path $pn.script -Leaf
    if (Test-PyRunning $base) {
        Write-Host ("[2] {0} ({1}) ZATEN calisiyor -> atlandi" -f $pn.name, $pn.port)
    } else {
        Write-Host ("[2] {0} ({1}) baslatiliyor..." -f $pn.name, $pn.port)
        Start-Process $py -ArgumentList $pn.script -WindowStyle Minimized `
            -RedirectStandardOutput ("data\logs\{0}.log" -f $pn.log) `
            -RedirectStandardError ("data\logs\{0}.err" -f $pn.log)
        Start-Sleep -Seconds 3
    }
}

# 3) DB retention - eski snapshot/event budama (13GB sismesin, her aciliste).
Write-Host "[3] DB budama (7 gunden eski snapshot/event)..."
& $py scripts\prune_db.py 7

# 4) Forward-shadow'lar - deterministik, idempotent (D cok-coin + funding-kontraryan).
Write-Host "[4] Forward-shadow'lar guncelleniyor (D cok-coin + funding)..."
& $py scripts\d_multicoin_shadow.py
& $py scripts\funding_shadow.py

# 4) DURUM OZETI
Start-Sleep -Seconds 2
Write-Host ""
Write-Host "=== DURUM ==="
if (Test-PyRunning "main.py") { Write-Host "  Bot           : CALISIYOR" } else { Write-Host "  Bot           : YOK (!) - run_bot.ps1 loguna bak" }
if (Test-Port 8050) { Write-Host "  Ana panel     : http://localhost:8050  UP" } else { Write-Host "  Ana panel     : henuz DOWN (bot aciliyorsa 10-20sn daha bekle)" }
foreach ($pn in $panels) {
    if (Test-Port $pn.port) { Write-Host ("  {0,-13} : http://localhost:{1}  UP" -f $pn.name, $pn.port) }
    else { Write-Host ("  {0,-13} : DOWN (!) - data\logs\{1}.err bak" -f $pn.name, $pn.log) }
}
Write-Host ""
Write-Host "Kacirlan ~1sa veri: bot acilista kline backfill + borsa pozisyon/PnL reconcile yapti."
Write-Host "Shadow (SOL/LINK) kacirlan barlarla guncellendi. Panelleri tarayicida ac."
