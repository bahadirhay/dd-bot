# update_shadows.ps1 — D cok-coin + funding forward-shadow'lari gunceller (deterministik, idempotent).
# EMIR YOK, paper. Zamanlanmis gorev gunluk calistirir -> D (ve funding izleme) forward verisi birikir.
# Elle de calistirilabilir:  powershell -ExecutionPolicy Bypass -File .\scripts\update_shadows.ps1
$ErrorActionPreference = "Continue"
$botDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $botDir
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = "."
$py = "C:\Users\BH\AppData\Local\Programs\Python\Python310\python.exe"
$log = Join-Path $botDir "data\logs\shadow_update.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$ts] shadow update BASLADI" | Out-File -FilePath $log -Append -Encoding utf8
& $py scripts\d_multicoin_shadow.py  *>> $log
& $py scripts\funding_shadow.py      *>> $log
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$ts] shadow update BITTI" | Out-File -FilePath $log -Append -Encoding utf8
