# scripts/watchdog.ps1 - SUREKLI TOPLAYICILARI diri tut (30dk'da bir GBot_Watchdog gorevi calistirir).
# Amac: standalone shadow'lar (orderflow, trendlf) cokerse REBOOT beklemeden geri getir = VERI KACMASIN.
# Bot (main.py) zaten supervisor'la korunuyor; supervisor da olduyse geri getir. Idempotent: calisan atlanir.
$ErrorActionPreference = "Continue"
$botDir = "C:\Users\BH\Desktop\bot"
Set-Location $botDir
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONPATH = "."
$py = "C:\Users\BH\AppData\Local\Programs\Python\Python310\python.exe"
$log = "$botDir\data\logs\watchdog.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

function Running($pat) {
    [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$pat*" })
}
function SupRunning {
    [bool](Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -EA SilentlyContinue |
        Where-Object { $_.CommandLine -match '-File.*run_bot' })
}
function StartShadow($script, $logname) {
    Start-Process $py -ArgumentList $script -WindowStyle Hidden `
        -RedirectStandardOutput "$botDir\data\logs\$logname.log" `
        -RedirectStandardError "$botDir\data\logs\$logname.err"
}

$acted = @()
# 1) Bot / supervisor
if (-not (Running "main.py") -and -not (SupRunning)) {
    Start-Process powershell -ArgumentList "-ExecutionPolicy","Bypass","-File",".\run_bot.ps1" -WindowStyle Normal
    $acted += "bot(supervisor)"
}
# 2) Order-flow toplayici
if (-not (Running "orderflow_shadow.py")) { StartShadow "scripts\orderflow_shadow.py" "orderflow_shadow"; $acted += "orderflow" }
# 3) Trend-LF shadow
if (-not (Running "trendlf_shadow.py"))  { StartShadow "scripts\trendlf_shadow.py"  "trendlf_shadow";  $acted += "trendlf" }
# 4) Buydip shadow
if (-not (Running "buydip_shadow.py"))   { StartShadow "scripts\buydip_shadow.py"   "buydip_shadow";   $acted += "buydip" }
# 5) Buydip CANLI (gercek emir, flag'e bagli; sinyal kacmasin diye diri tut)
#    NOT: surec '-m engine.buydip_live' olarak calisir -> 'buydip_live.py' ESLESMEZ -> 55-surec bug'i (10-15).
#    'engine.buydip_live' ara (panel 'buydip_live_panel' ile karismaz).
if (-not (Running "engine.buydip_live")) {
    Start-Process $py -ArgumentList "-m","engine.buydip_live" -WindowStyle Hidden `
        -RedirectStandardOutput "$botDir\data\logs\buydip_live.log" -RedirectStandardError "$botDir\data\logs\buydip_live.err"
    $acted += "buydip_live"
}

if ($acted.Count -gt 0) {
    "[$ts] WATCHDOG geri baslatti: $($acted -join ', ')" | Out-File -FilePath $log -Append -Encoding utf8
} else {
    "[$ts] WATCHDOG ok (hepsi calisiyor)" | Out-File -FilePath $log -Append -Encoding utf8
}
