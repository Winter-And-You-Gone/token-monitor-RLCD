#!/usr/bin/env bash
# Install RLCD bridge daemon as a systemd --user service on Linux.
#
# Usage:  scripts/install-bridge-linux.sh
# Run from anywhere — it resolves paths relative to the repo root.

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BRIDGE_DIR="$REPO_ROOT/bridge"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_FILE="$UNIT_DIR/rlcd-bridge.service"
ENV_FILE="$BRIDGE_DIR/.env"

if [[ ! -d "$BRIDGE_DIR" ]]; then
    echo "error: $BRIDGE_DIR not found" >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "error: 'uv' not installed. See https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi

if ! command -v ccusage >/dev/null 2>&1; then
    echo "warning: 'ccusage' not on PATH — install once with: npm install -g ccusage" >&2
fi

mkdir -p "$UNIT_DIR"

UV_BIN="$(command -v uv)"

unit_path() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/ /\\x20/g'
}

BRIDGE_DIR_UNIT="$(unit_path "$BRIDGE_DIR")"
ENV_FILE_UNIT="$(unit_path "$ENV_FILE")"

cat > "$UNIT_FILE" <<EOF
[Unit]
Description=RLCD bridge (Claude usage -> HTTP JSON for ESP32-S3-RLCD-4.2)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$BRIDGE_DIR_UNIT
EnvironmentFile=-$ENV_FILE_UNIT
ExecStart=$UV_BIN run python bridge.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now rlcd-bridge.service

echo
echo "✓ Installed and started: $UNIT_FILE"
echo "  Status:  systemctl --user status rlcd-bridge"
echo "  Logs:    journalctl --user -u rlcd-bridge -f"
echo "  Test:    curl http://localhost:7777/api/usage | jq"
echo
echo "If you want it to keep running after logout:"
echo "  loginctl enable-linger \$USER"
