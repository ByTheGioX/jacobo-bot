# ============================================================
#  Jacobo-Bot — Instalacion automatica en Windows VPS
#  Ejecutar como Administrador en PowerShell
#  Uso: .\deploy_windows.ps1
# ============================================================

$ErrorActionPreference = "Stop"

# ----------------------------------------------------------
# Colores de consola
# ----------------------------------------------------------
function Log   { Write-Host "[OK] $args" -ForegroundColor Green }
function Warn  { Write-Host "[!!] $args" -ForegroundColor Yellow }
function Title { Write-Host "`n=== $args ===" -ForegroundColor Cyan }

# ----------------------------------------------------------
# Variables — editar si es necesario
# ----------------------------------------------------------
$BOT_DIR   = "C:\Users\LIVETEAM\Desktop\jacobo-bot"
$PYTHON_VERSION = "3.12.10"
$PYTHON_URL = "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-amd64.exe"
$GIT_URL    = "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe"
$TASK_NAME  = "JacoboBot"
$LOG_FILE   = "C:\Users\LIVETEAM\Desktop\jacobo-bot\data\jacobo_bot.log"

Clear-Host
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Jacobo-Bot - Instalacion en Windows VPS  " -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Verificar que se ejecuta como Administrador
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "[ERROR] Este script debe ejecutarse como Administrador." -ForegroundColor Red
    Write-Host "Haz clic derecho en PowerShell y selecciona 'Ejecutar como administrador'" -ForegroundColor Yellow
    exit 1
}

# ----------------------------------------------------------
# 1. Pedir datos necesarios
# ----------------------------------------------------------
Title "Configuracion inicial"

$GITHUB_USER  = Read-Host "Tu usuario de GitHub (ej: ByTheGioX)"
$GITHUB_TOKEN = Read-Host "Tu GitHub Personal Access Token" -AsSecureString
$GITHUB_TOKEN_PLAIN = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($GITHUB_TOKEN)
)
$REPO_NAME = Read-Host "Nombre del repositorio (ej: jacobo-bot)"
$REPO_URL = "https://${GITHUB_USER}:${GITHUB_TOKEN_PLAIN}@github.com/${GITHUB_USER}/${REPO_NAME}.git"

Write-Host ""

# ----------------------------------------------------------
# 2. Instalar Python 3.12
# ----------------------------------------------------------
Title "Instalando Python $PYTHON_VERSION"

$python_installed = Get-Command python -ErrorAction SilentlyContinue
if ($python_installed) {
    $ver = (python --version 2>&1)
    Warn "Python ya instalado: $ver — omitiendo"
} else {
    Log "Descargando Python $PYTHON_VERSION..."
    $python_installer = "$env:TEMP\python-installer.exe"
    Invoke-WebRequest -Uri $PYTHON_URL -OutFile $python_installer -UseBasicParsing
    Log "Instalando Python (modo silencioso)..."
    Start-Process -FilePath $python_installer -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
    Remove-Item $python_installer -Force
    # Recargar PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    Log "Python instalado correctamente"
}

# ----------------------------------------------------------
# 3. Instalar Git
# ----------------------------------------------------------
Title "Instalando Git"

$git_installed = Get-Command git -ErrorAction SilentlyContinue
if ($git_installed) {
    Warn "Git ya instalado — omitiendo"
} else {
    Log "Descargando Git..."
    $git_installer = "$env:TEMP\git-installer.exe"
    Invoke-WebRequest -Uri $GIT_URL -OutFile $git_installer -UseBasicParsing
    Log "Instalando Git (modo silencioso)..."
    Start-Process -FilePath $git_installer -ArgumentList "/VERYSILENT /NORESTART" -Wait
    Remove-Item $git_installer -Force
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    Log "Git instalado correctamente"
}

# ----------------------------------------------------------
# 4. Clonar o actualizar el repositorio
# ----------------------------------------------------------
Title "Descargando codigo del bot"

if (Test-Path "$BOT_DIR\.git") {
    Warn "Repositorio ya existe en $BOT_DIR — actualizando..."
    Set-Location $BOT_DIR
    git pull
} else {
    Log "Clonando repositorio en $BOT_DIR..."
    git clone $REPO_URL $BOT_DIR
    if (-not $?) { Write-Host "[ERROR] No se pudo clonar el repositorio. Verifica el token y el nombre del repo." -ForegroundColor Red; exit 1 }
}

Set-Location $BOT_DIR

# ----------------------------------------------------------
# 5. Entorno virtual e instalacion de dependencias
# ----------------------------------------------------------
Title "Instalando dependencias Python"

if (-not (Test-Path "$BOT_DIR\.venv")) {
    Log "Creando entorno virtual..."
    python -m venv .venv
}

Log "Instalando paquetes del bot..."
& "$BOT_DIR\.venv\Scripts\pip.exe" install --quiet --upgrade pip
& "$BOT_DIR\.venv\Scripts\pip.exe" install --quiet -r requirements.txt
Log "Dependencias instaladas"

# ----------------------------------------------------------
# 6. Instalar Playwright y Camoufox
# ----------------------------------------------------------
Title "Instalando navegadores"

Log "Instalando Chromium para Playwright..."
& "$BOT_DIR\.venv\Scripts\python.exe" -m playwright install chromium

Log "Descargando Camoufox (Firefox anti-deteccion)..."
& "$BOT_DIR\.venv\Scripts\python.exe" -m camoufox fetch

# ----------------------------------------------------------
# 7. Crear directorios de datos
# ----------------------------------------------------------
Title "Creando estructura de carpetas"

New-Item -ItemType Directory -Force -Path "$BOT_DIR\data\photos" | Out-Null
New-Item -ItemType Directory -Force -Path "$BOT_DIR\data\browser_session" | Out-Null
Log "Carpetas creadas"

# ----------------------------------------------------------
# 8. Crear tarea en el Programador de tareas de Windows
# ----------------------------------------------------------
Title "Configurando inicio automatico (Task Scheduler)"

# Eliminar tarea anterior si existe
$existing = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
if ($existing) {
    Warn "Tarea '$TASK_NAME' ya existe — reemplazando..."
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false
}

$action = New-ScheduledTaskAction `
    -Execute "$BOT_DIR\.venv\Scripts\python.exe" `
    -Argument "main.py" `
    -WorkingDirectory $BOT_DIR

# Arrancar al iniciar el sistema + repetir cada hora si falla
$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TASK_NAME `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Jacobo-Bot: monitor inmobiliario Idealista -> WordPress" | Out-Null

Log "Tarea '$TASK_NAME' creada y configurada para arranque automatico"

# ----------------------------------------------------------
# 9. Resumen final
# ----------------------------------------------------------
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Instalacion completada correctamente" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Bot instalado en: $BOT_DIR" -ForegroundColor White
Write-Host ""
Write-Host "  Comandos utiles:" -ForegroundColor Cyan
Write-Host "    Arrancar bot ahora:" -ForegroundColor White
Write-Host "      Start-ScheduledTask -TaskName '$TASK_NAME'" -ForegroundColor Gray
Write-Host ""
Write-Host "    Parar bot:" -ForegroundColor White
Write-Host "      Stop-ScheduledTask -TaskName '$TASK_NAME'" -ForegroundColor Gray
Write-Host ""
Write-Host "    Ver estado:" -ForegroundColor White
Write-Host "      Get-ScheduledTask -TaskName '$TASK_NAME' | Select-Object State" -ForegroundColor Gray
Write-Host ""
Write-Host "    Ver logs en vivo:" -ForegroundColor White
Write-Host "      Get-Content $LOG_FILE -Wait -Tail 50" -ForegroundColor Gray
Write-Host ""
Write-Host "    Ejecutar ciclo manual unico:" -ForegroundColor White
Write-Host "      cd $BOT_DIR; .venv\Scripts\python.exe main.py --once" -ForegroundColor Gray
Write-Host ""
Write-Host "    Actualizar codigo:" -ForegroundColor White
Write-Host "      cd $BOT_DIR; git pull; Restart-ScheduledTask '$TASK_NAME'" -ForegroundColor Gray
Write-Host ""
Write-Host "  IMPORTANTE: Verifica la configuracion antes de arrancar:" -ForegroundColor Yellow
Write-Host "    $BOT_DIR\configuracion\" -ForegroundColor Gray
Write-Host ""

$start = Read-Host "Arrancar el bot ahora? (s/n)"
if ($start -eq "s" -or $start -eq "S") {
    Start-ScheduledTask -TaskName $TASK_NAME
    Log "Bot arrancado. Usa 'Get-Content $LOG_FILE -Wait -Tail 50' para ver logs."
}
