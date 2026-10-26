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
# BELLEK-KORUMA (2026-09-30): panolar OTOMATIK ACILMAZ. Makine 3.9GB RAM -> panolar bellek+CPU yiyip
# kilitlenmeye katkida bulunuyordu (kullanilmayan strateji goruntuleyicileri: G/dumpfade/F kapali).
# Bir panoyu GORMEK icin elle ac: python dashboard\<panel>.py. Ana veri (8050) main.py icinde, hep acik.
$panels = @()   # otomatik pano YOK (bellek icin). Ihtiyac halinde elle baslat.
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

# 2b) ORDER-FLOW SHADOW toplayici (SUREKLI, detached). Her dakika OBI + taker-akis + mid kaydeder.
#     Fiyat-DISI mikroyapiyi canli biriktirir (backtest edilemez) -> haftalar sonra WF+permut test.
if (Test-PyRunning "orderflow_shadow.py") {
    Write-Host "[2b] Order-flow shadow ZATEN calisiyor -> atlandi"
} else {
    Write-Host "[2b] Order-flow shadow baslatiliyor (surekli, her dakika OBI+akis)..."
    Start-Process $py -ArgumentList "scripts\orderflow_shadow.py" -WindowStyle Hidden `
        -RedirectStandardOutput "data\logs\orderflow_shadow.log" `
        -RedirectStandardError "data\logs\orderflow_shadow.err"
    Start-Sleep -Seconds 2
}

# 2c) G ERKEN-GIRIS shadow -- DURDURULDU 2026-09-15 (rejim donusunde yanlislandi, pump-tepesinde LONG).
#     Reboot'ta GERI GELMESIN diye kapatildi. Tekrar acmak icin bu blogu geri ekle.
# 2d) G-TERS shadow -- DURDURULDU 2026-09-15 (reverse-G hipotezi rejim donunce yanlislandi, bkz
#     g-reversed-live memory). Reboot'ta geri gelmesin diye kapatildi.

# 2e) TREND-LONG/NAKIT shadow (SUREKLI, detached). Cok-coin gunluk long-veya-nakit trend-takip.
#     Kurallı yon-bahsi (edge degil): dususte nakite cek, short'lama. Kullanici karari 09-15.
if (Test-PyRunning "trendlf_shadow.py") {
    Write-Host "[2e] Trend-LF shadow ZATEN calisiyor -> atlandi"
} else {
    Write-Host "[2e] Trend-LF shadow baslatiliyor (long-veya-nakit gunluk trend-takip)..."
    Start-Process $py -ArgumentList "scripts\trendlf_shadow.py" -WindowStyle Hidden `
        -RedirectStandardOutput "data\logs\trendlf_shadow.log" `
        -RedirectStandardError "data\logs\trendlf_shadow.err"
    Start-Sleep -Seconds 2
}

# 2f) DIP-AL shadow (SUREKLI, detached). Likit uptrend-dip-al forward (survivorship-testli bulgu, 09-24).
if (Test-PyRunning "buydip_shadow.py") {
    Write-Host "[2f] Buydip shadow ZATEN calisiyor -> atlandi"
} else {
    Write-Host "[2f] Buydip shadow baslatiliyor (likit uptrend-dip-al forward)..."
    Start-Process $py -ArgumentList "scripts\buydip_shadow.py" -WindowStyle Hidden `
        -RedirectStandardOutput "data\logs\buydip_shadow.log" `
        -RedirectStandardError "data\logs\buydip_shadow.err"
    Start-Sleep -Seconds 2
}

# 2g) BUYDIP CANLI (GERCEK EMIR, kucuk test). Flag .env V3_BUYDIP_LIVE; kapaliyken emir gitmez.
if (Test-PyRunning "engine.buydip_live") {   # '-m engine.buydip_live' calisir; 'buydip_live.py' ESLESMEZ (10-15 bug)
    Write-Host "[2g] Buydip CANLI ZATEN calisiyor -> atlandi"
} else {
    Write-Host "[2g] Buydip CANLI baslatiliyor (GERCEK EMIR - kucuk test, flag'e bagli)..."
    Start-Process $py -ArgumentList "-m","engine.buydip_live" -WindowStyle Hidden `
        -RedirectStandardOutput "data\logs\buydip_live.log" `
        -RedirectStandardError "data\logs\buydip_live.err"
    Start-Sleep -Seconds 2
}

# 2h) BUYDIP CANLI PANOSU (hafif ~27MB, port 8061). Kullanici izleme ister. Agir panolar kapali kalir.
if (Test-PyRunning "buydip_live_panel.py") {
    Write-Host "[2h] Buydip panosu (8061) ZATEN calisiyor -> atlandi"
} else {
    Write-Host "[2h] Buydip CANLI panosu baslatiliyor (http://localhost:8061)..."
    Start-Process $py -ArgumentList "dashboard\buydip_live_panel.py" -WindowStyle Hidden `
        -RedirectStandardOutput "data\logs\buydip_live_panel.log" `
        -RedirectStandardError "data\logs\buydip_live_panel.err"
    Start-Sleep -Seconds 2
}

# 3) DB retention - eski snapshot/event budama (13GB sismesin, her aciliste).
Write-Host "[3] DB budama (7 gunden eski snapshot/event)..."
& $py scripts\prune_db.py 7

# 4) Forward-shadow'lar - deterministik, idempotent (D cok-coin + funding-kontraryan).
Write-Host "[4] Forward-shadow'lar guncelleniyor (D cok-coin + funding)..."
& $py scripts\d_multicoin_shadow.py
& $py scripts\funding_shadow.py
& $py scripts\ftsm_shadow.py

# 5) Uzak-erisim tuneli (cloudflared quick-tunnel, idempotent). Guncel link -> Masaustu\GBOT_PANEL_LINK.txt
Write-Host "[5] Uzak-erisim tuneli (cloudflared) - link Masaustu\GBOT_PANEL_LINK.txt'e yazilir..."
& powershell -ExecutionPolicy Bypass -File ".\scripts\run_tunnel.ps1"

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
