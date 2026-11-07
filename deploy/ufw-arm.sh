#!/usr/bin/env bash
# =============================================================================
# Atheer RADAR — arming the host firewall (SEC-1 residual, REVIEW_DECISIONS.md)
#
# ⛔ RUN THIS FROM THE PHYSICAL CONSOLE (or with a second session open), NEVER
#    from the only SSH session you have: `ufw enable` applies the ruleset
#    immediately, and a missing allow rule cuts the connection that typed it.
#
# ⚠ Measured state this script was written against (2026-09-01):
#    `systemctl is-active ufw` = active  BUT  /etc/ufw/ufw.conf ENABLED=no
#    — i.e. NO firewall is armed today, despite appearances. Re-check both
#    before and after: `sudo ufw status verbose` is the only honest readout.
#
# What the rules below preserve, and why (every consumer measured in-tree):
#   22/tcp   in   SSH into this host (and anything else that administers it)
#   8000/tcp in   the dashboard + API — deliberately LAN-open (server.py 0.0.0.0)
#   OUTBOUND      default allow — covers the collectors (OpenSky/AISStream/GFW/
#                 CelesTrak/MarineTraffic), enrichment (adsbdb/planespotters/
#                 Wikimedia), Telegram gateway, and the two backup pushes
#                 (10.10.20.57 offsite + 10.0.0.113 WINSTORE) — all outbound.
#   Postgres 5435 needs NO rule: it binds 127.0.0.1 only (verified via ss).
#
# Optional hardening once comfortable: restrict 8000 to the analyst subnet, e.g.
#   sudo ufw allow from 10.0.0.0/24 to any port 8000 proto tcp
# and delete the open rule. Left commented on purpose — subnet is yours to name.
# =============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo, from the console"; exit 1; fi

ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 8000/tcp comment 'Atheer RADAR dashboard+API (LAN)'
# ufw allow from 10.0.0.0/24 to any port 8000 proto tcp comment 'analyst subnet only'

ufw --force enable
ufw status verbose
echo
echo "Now, WITHOUT closing this console: open a NEW SSH session and load the"
echo "dashboard from another machine. Only after BOTH work is this done."
