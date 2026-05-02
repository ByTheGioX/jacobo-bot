#!/bin/bash
# ============================================================
#  Jacobo-Bot — Script de instalación automática en VPS
#  Compatible con Ubuntu 22.04 / 24.04
#  Uso: bash deploy.sh
# ============================================================

set -e  # Parar si cualquier comando falla

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[!!]${NC} $1"; }
fail() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo "============================================"
echo "  Jacobo-Bot — Instalación en VPS"
echo "============================================"
echo ""

# ----------------------------------------------------------
# 1. Variables — editar si es necesario
# ----------------------------------------------------------
BOT_USER="jacobo"
BOT_DIR="/home/$BOT_USER/jacobo-bot"
REPO_URL="https://github.com/ByTheGioX/jacobo-bot.git"  # cambiar si es privado
PYTHON="python3.12"
SERVICE_NAME="jacobo-bot"

# ----------------------------------------------------------
# 2. Crear usuario del sistema (sin contraseña, sin login)
# ----------------------------------------------------------
if id "$BOT_USER" &>/dev/null; then
    warn "Usuario '$BOT_USER' ya existe — omitiendo creación"
else
    log "Creando usuario del sistema: $BOT_USER"
    useradd -m -s /bin/bash "$BOT_USER"
fi

# ----------------------------------------------------------
# 3. Instalar dependencias del sistema
# ----------------------------------------------------------
log "Actualizando paquetes del sistema..."
apt-get update -qq

log "Instalando Python 3.12 y herramientas base..."
apt-get install -y -qq \
    software-properties-common \
    git \
    curl \
    wget \
    unzip \
    build-essential

# Python 3.12 via deadsnakes si no está disponible
if ! command -v python3.12 &>/dev/null; then
    log "Añadiendo repositorio deadsnakes para Python 3.12..."
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    apt-get install -y -qq python3.12 python3.12-venv python3.12-dev
fi

# Dependencias de Playwright/Chromium
log "Instalando dependencias de navegador (Playwright/Chromium)..."
apt-get install -y -qq \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 \
    libcairo2 libatspi2.0-0 libwayland-client0 \
    fonts-liberation libx11-6 libx11-xcb1 libxcb1 \
    libxext6 libxtst6 2>/dev/null || warn "Algunas dependencias de Chromium no disponibles (puede que no sean necesarias)"

# ----------------------------------------------------------
# 4. Clonar o actualizar el repositorio
# ----------------------------------------------------------
if [ -d "$BOT_DIR/.git" ]; then
    log "Repositorio ya existe — haciendo git pull..."
    sudo -u "$BOT_USER" git -C "$BOT_DIR" pull
else
    log "Clonando repositorio en $BOT_DIR..."
    sudo -u "$BOT_USER" git clone "$REPO_URL" "$BOT_DIR" || \
        fail "No se pudo clonar el repositorio. Verifica REPO_URL en este script."
fi

# ----------------------------------------------------------
# 5. Crear entorno virtual e instalar dependencias Python
# ----------------------------------------------------------
log "Creando entorno virtual Python..."
sudo -u "$BOT_USER" $PYTHON -m venv "$BOT_DIR/.venv"

log "Instalando dependencias del bot..."
sudo -u "$BOT_USER" "$BOT_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$BOT_USER" "$BOT_DIR/.venv/bin/pip" install --quiet -r "$BOT_DIR/requirements.txt"

# ----------------------------------------------------------
# 6. Instalar Playwright y Camoufox
# ----------------------------------------------------------
log "Instalando navegador Chromium para Playwright..."
sudo -u "$BOT_USER" "$BOT_DIR/.venv/bin/python" -m playwright install chromium 2>&1 | tail -3

log "Descargando Camoufox (Firefox anti-detección)..."
sudo -u "$BOT_USER" "$BOT_DIR/.venv/bin/python" -m camoufox fetch 2>&1 | tail -3

# ----------------------------------------------------------
# 7. Crear directorios de datos
# ----------------------------------------------------------
log "Creando directorios de datos..."
sudo -u "$BOT_USER" mkdir -p "$BOT_DIR/data/photos"
sudo -u "$BOT_USER" mkdir -p "$BOT_DIR/data/browser_session"

# ----------------------------------------------------------
# 8. Instalar servicio systemd
# ----------------------------------------------------------
log "Instalando servicio systemd: $SERVICE_NAME..."
cat > "/etc/systemd/system/$SERVICE_NAME.service" << EOF
[Unit]
Description=Jacobo-Bot — Monitor inmobiliario Idealista → WordPress
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$BOT_USER
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/.venv/bin/python main.py
Restart=on-failure
RestartSec=60
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME
Environment=PYTHONUNBUFFERED=1
Environment=DISPLAY=:99

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
log "Servicio '$SERVICE_NAME' instalado y habilitado para arranque automático"

# ----------------------------------------------------------
# 9. Resumen final
# ----------------------------------------------------------
echo ""
echo "============================================"
echo "  Instalación completada"
echo "============================================"
echo ""
echo "  Bot instalado en: $BOT_DIR"
echo "  Usuario del sistema: $BOT_USER"
echo ""
echo "  Comandos útiles:"
echo "    systemctl start $SERVICE_NAME      # Arrancar"
echo "    systemctl stop $SERVICE_NAME       # Parar"
echo "    systemctl status $SERVICE_NAME     # Ver estado"
echo "    journalctl -u $SERVICE_NAME -f     # Ver logs en vivo"
echo ""
echo "  ANTES DE ARRANCAR verifica la configuración:"
echo "    $BOT_DIR/configuracion/"
echo ""
