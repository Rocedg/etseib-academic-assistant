# Prepares the server laptop (Windows 11): Ollama only on 127.0.0.1, models, first token, gateway at log-on
# and a public HTTPS URL through Tailscale Funnel. Safe to run again: every step checks before changing.
# Usage (from the repository root):  powershell -ExecutionPolicy Bypass -File server\install_server.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$models = @("qwen3:8b", "qwen3-embedding:0.6b")
function Step($t) { Write-Host "`n== $t ==" -ForegroundColor Cyan }

Step "1/6 Python"
try { py -3 --version } catch { Write-Host "Falta Python 3:  winget install -e --id Python.Python.3.12  (y vuelve a ejecutar)"; exit 1 }

Step "2/6 Ollama solo en 127.0.0.1 y modelos"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Write-Host "Falta Ollama:  winget install -e --id Ollama.Ollama  (y vuelve a ejecutar)"; exit 1 }
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "127.0.0.1:11434", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "-1", "User")      # the model stays loaded: no cold start in the demo
$env:OLLAMA_HOST = "127.0.0.1:11434"; $env:OLLAMA_KEEP_ALIVE = "-1"
Get-Process -Name "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
$app = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
if (Test-Path $app) { Start-Process $app } else { Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden }
$ok = $false
foreach ($i in 1..30) { try { Invoke-RestMethod http://127.0.0.1:11434/api/version | Out-Null; $ok = $true; break } catch { Start-Sleep 1 } }
if (-not $ok) { Write-Host "Ollama no arranca. Ábrelo desde el menú Inicio y repite."; exit 1 }
Get-NetTCPConnection -LocalPort 11434 -State Listen | Select-Object LocalAddress, LocalPort | Format-Table
Write-Host "Debe salir SOLO 127.0.0.1. Si sale 0.0.0.0 o '::', reinicia el portátil y repite."
$have = (ollama list) -join "`n"
foreach ($m in $models) { if ($have -notmatch [regex]::Escape($m)) { Write-Host "Descargando $m..."; ollama pull $m } else { Write-Host "$m ya está" } }

Step "3/6 Token personal"
Push-Location $root
py -3 server\server.py token list
$name = Read-Host "Nombre para un token nuevo (Intro para saltar si ya tienes uno)"
if ($name) { py -3 server\server.py token new $name }
Pop-Location

Step "4/6 La pasarela arranca sola al iniciar sesión (y se reinicia si se cae)"
$action = New-ScheduledTaskAction -Execute "pyw.exe" -Argument "-3 `"$root\server\server.py`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "ETSEIB-assistant-server" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Get-CimInstance Win32_Process -Filter "Name like 'py%.exe'" | Where-Object CommandLine -like "*server\server.py*" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-ScheduledTask -TaskName "ETSEIB-assistant-server"
$ok = $false
foreach ($i in 1..90) { try { $s = Invoke-RestMethod http://127.0.0.1:8080/health; $ok = $true; break } catch { Start-Sleep 2 } }
if ($ok) { $s | Format-List } else { Write-Host "La pasarela no responde. Ejecuta server\start_server.bat para ver el error."; exit 1 }

Step "5/6 Tailscale Funnel (URL pública HTTPS, protegida con el token)"
if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
  Write-Host "Falta Tailscale:  winget install -e --id Tailscale.Tailscale"
  Write-Host "Inicia sesión desde el icono de la bandeja y vuelve a ejecutar este script."; exit 0
}
tailscale status | Select-Object -First 3
Write-Host "Si a continuación sale un enlace a login.tailscale.com, ábrelo y autoriza Funnel; después vuelve a ejecutar el script."
tailscale funnel --bg 8080
tailscale funnel status

Step "6/6 Resumen"
Write-Host "Ollama:    solo 127.0.0.1:11434 (no se ve desde fuera), modelos $($models -join ', ')"
Write-Host "Pasarela:  127.0.0.1:8080, con token; tarea programada ETSEIB-assistant-server"
Write-Host "URL demo:  la https://....ts.net que sale arriba en 'funnel status' (ábrela en cualquier navegador)"
Write-Host "Datos:     $env:USERPROFILE\.etseib-assistant (hash de tokens, access.jsonl, traces)"
Write-Host "Recuerda:  Windows Update > Pausar 1 semana, para que no reinicie mientras no estás."
