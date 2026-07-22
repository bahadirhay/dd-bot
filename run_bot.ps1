# run_bot.ps1 - bot supervisor: cokerse otomatik yeniden baslatir.
# Kullanim:  powershell -ExecutionPolicy Bypass -File .\run_bot.ps1
# Neden: 2026-07-22 02:38'de python.exe sistem bellek-tukenmesi (claude.exe 13GB) yuzunden
# native cokme (0xc00000fd stack overflow, select.pyd) ile durdu ve ~4.4 saat kapali kaldi.
# Bu wrapper cokmeyi engellemez ama saniyeler icinde geri getirir. Borsa-tarafi SL zaten
# kapaliyken korur; supervisor gozetimsiz sureyi minimuma indirir.

$ErrorActionPreference = "Continue"
$botDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $botDir

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = "."
$py = "C:\Users\BH\AppData\Local\Programs\Python\Python310\python.exe"

$supLog = Join-Path $botDir "data\logs\supervisor.log"
New-Item -ItemType Directory -Force -Path (Split-Path $supLog) | Out-Null

$restartCount = 0
while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$ts] BOT BASLIYOR (restart #$restartCount)" | Tee-Object -FilePath $supLog -Append

    & $py main.py
    $code = $LASTEXITCODE

    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$ts] BOT DURDU (exit=$code) - 5sn sonra yeniden baslatiliyor" | Tee-Object -FilePath $supLog -Append

    # Cok hizli cokme donguse girmesin diye kisa bekleme.
    $restartCount++
    Start-Sleep -Seconds 5
}
