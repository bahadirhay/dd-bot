# run_tunnel.ps1 — G paneli (8060) icin cloudflared quick-tunnel. Idempotent.
# URL her baslatmada DEGISIR (quick tunnel) -> guncel linki Masaustu\GBOT_PANEL_LINK.txt'e yazar.
# start_all.ps1 cagirir (reboot sonrasi autostart). Elle: powershell -ExecutionPolicy Bypass -File .\scripts\run_tunnel.ps1
$ErrorActionPreference = "Continue"
$botDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $botDir
$cf = Join-Path $botDir "tools\cloudflared.exe"
$log = Join-Path $botDir "data\logs\cloudflared.log"
$linkFile = Join-Path ([Environment]::GetFolderPath('Desktop')) "GBOT_PANEL_LINK.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

if (-not (Test-Path $cf)) { "cloudflared.exe yok: $cf" | Out-File $linkFile -Encoding utf8; exit 1 }

# zaten calisiyorsa yeni baslatma (idempotent), sadece linki tazele
$running = @(Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue)
if ($running.Count -eq 0) {
    Start-Process -FilePath $cf -ArgumentList "tunnel","--url","http://localhost:8060" `
        -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    Start-Sleep -Seconds 14
}
# URL'i logdan cek (hem stdout hem stderr'e bakabilir)
$url = $null
foreach ($f in @($log, "$log.err")) {
    if (Test-Path $f) {
        $m = Select-String -Path $f -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -AllMatches -ErrorAction SilentlyContinue |
             Select-Object -Last 1
        if ($m) { $url = $m.Matches[-1].Value; break }
    }
}
$ts = Get-Date -Format "yyyy-MM-dd HH:mm"
if ($url) {
    $body = @"
G PANELI - CANLI ERISIM LINKI  ($ts)
============================================
$url

Kullanici: gbot
Parola   : .env icindeki G_PANEL_PASS

NOT: Bu link her yeniden baslatmada DEGISIR. En guncel link hep bu dosyada.
"@
    $body | Out-File -FilePath $linkFile -Encoding utf8
    "[$ts] tunnel URL: $url" | Out-File -FilePath (Join-Path $botDir "data\logs\tunnel.log") -Append -Encoding utf8
} else {
    "[$ts] tunnel URL bulunamadi (cloudflared basliyor olabilir, birazdan tekrar dene)" | Out-File $linkFile -Encoding utf8
}
