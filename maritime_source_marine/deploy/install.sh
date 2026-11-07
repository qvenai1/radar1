#!/usr/bin/env bash
# Install mtscraper as a systemd **user** service.
#
# A user service (not a system one) because collection needs a browser and a
# display, and because this needs no root: the unit, its config, and its logs all
# live under the invoking user. Surviving reboot without an interactive login is
# the one thing that does require a privileged call (`loginctl enable-linger`),
# which this script asks for explicitly rather than assuming sudo.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mtscraper"
CONF_FILE="$CONF_DIR/mtscraper.env"

echo "project:  $PROJECT_DIR"

# --- preconditions ----------------------------------------------------------
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || {
    echo "error: $PROJECT_DIR/.venv/bin/python not found — create the venv first" >&2
    exit 1
}
command -v xvfb-run >/dev/null || {
    echo "error: xvfb-run not found — install it:  sudo apt install xvfb" >&2
    exit 1
}

chmod +x "$PROJECT_DIR/deploy/preflight.sh"

# --- config (never clobbered) -----------------------------------------------
mkdir -p "$CONF_DIR"
if [[ -e "$CONF_FILE" ]]; then
    echo "config:   $CONF_FILE (kept — your edits are preserved)"
    echo "          compare against deploy/mtscraper.env for new settings"
else
    install -m 600 "$PROJECT_DIR/deploy/mtscraper.env" "$CONF_FILE"
    echo "config:   $CONF_FILE (installed)"
fi

# --- unit -------------------------------------------------------------------
mkdir -p "$UNIT_DIR"
sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
    "$PROJECT_DIR/deploy/mtscraper.service" > "$UNIT_DIR/mtscraper.service"
echo "unit:     $UNIT_DIR/mtscraper.service"

systemctl --user daemon-reload
systemctl --user enable mtscraper.service >/dev/null
echo "enabled:  starts on login"

# --- survive reboot without logging in --------------------------------------
if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" == "yes" ]]; then
    echo "linger:   already on — survives reboot with no login"
else
    echo
    echo "linger is OFF: the service will only start once you log in graphically."
    echo "To make it start at boot regardless, run:"
    echo "    sudo loginctl enable-linger $USER"
fi

cat <<EOF

Next:
    systemctl --user start mtscraper        # start collecting now
    systemctl --user status mtscraper       # check it came up
    journalctl --user -u mtscraper -f       # watch cycles live
    systemctl --user stop mtscraper         # graceful stop (flushes writers)

Config (including which disk the archive goes to):
    \$EDITOR $CONF_FILE
    systemctl --user restart mtscraper
EOF
