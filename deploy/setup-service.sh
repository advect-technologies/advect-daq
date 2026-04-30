#!/bin/bash
# deploy/setup-service.sh
# One-time setup script to install the systemd service from the template

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"   # assumes deploy/ is directly under project root

TEMPLATE_FILE="$SCRIPT_DIR/advect-daq.service"
SERVICE_FILE="/etc/systemd/system/advect-daq.service"

CURRENT_USER=$(whoami)
CURRENT_HOME="/home/$CURRENT_USER"

echo "=== advect-daq Systemd Service Installer ==="
echo "Current user : $CURRENT_USER"
echo "Project path : $PROJECT_ROOT"
echo "Template     : $TEMPLATE_FILE"

if [[ ! -f "$TEMPLATE_FILE" ]]; then
    echo "❌ Error: Template file not found at $TEMPLATE_FILE"
    exit 1
fi

# Create a temporary file with placeholders replaced
TEMP_SERVICE=$(mktemp)
sed -e "s|<usr>|$CURRENT_USER|g" \
    "$TEMPLATE_FILE" > "$TEMP_SERVICE"

echo "→ Created service file for user '$CURRENT_USER'"

# Copy to systemd directory (requires sudo)
echo "→ Installing service to /etc/systemd/system/ ..."
sudo cp "$TEMP_SERVICE" "$SERVICE_FILE"
rm "$TEMP_SERVICE"

# Set correct permissions
sudo chmod 644 "$SERVICE_FILE"

# Reload systemd and enable/start the service
echo "→ Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "→ Enabling service to start on boot..."
sudo systemctl enable advect-daq.service

echo "→ Starting service now..."
sudo systemctl start advect-daq.service

echo ""
echo "✅ Service installed successfully!"
echo ""
echo "Useful commands:"
echo "   sudo systemctl status advect-daq.service"
echo "   journalctl -u advect-daq.service -f          # live logs"
echo "   sudo systemctl restart advect-daq.service    # after config changes"
echo "   sudo systemctl stop advect-daq.service"