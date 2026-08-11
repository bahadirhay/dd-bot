# run_candidate_scan.ps1 — AYLIK G aday-kesif taramasi (~70 likit coin, permut+iki-yari+Bonferroni+likidite).
# EMIR YOK, sadece analiz -> reports/g_scan_YYYY-MM-DD.txt. Zamanlanmis gorev calistirir.
# Elle:  powershell -ExecutionPolicy Bypass -File .\scripts\run_candidate_scan.ps1
$ErrorActionPreference = "Continue"
$botDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $botDir
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONPATH = "."
$py = "C:\Users\BH\AppData\Local\Programs\Python\Python310\python.exe"
New-Item -ItemType Directory -Force -Path (Join-Path $botDir "reports") | Out-Null
$report = Join-Path $botDir ("reports\g_scan_{0}.txt" -f (Get-Date -Format "yyyy-MM-dd"))
& $py scripts\g_candidate_monthly_scan.py *> $report
"[{0}] tarama bitti -> {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $report | Out-File -FilePath (Join-Path $botDir "data\logs\candidate_scan.log") -Append -Encoding utf8
