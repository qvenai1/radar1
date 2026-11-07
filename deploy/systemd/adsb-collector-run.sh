#!/bin/sh
# Launcher for the adsb_scraper collector service.
#
# Prefer HEADED under a virtual display (xvfb-run --no-headless) — the mode the
# operator selected to reliably defeat Cloudflare — and fall back to HEADLESS if
# xvfb is not installed yet (still collects via adsb_scraper's `interception`
# strategy). This makes the service correct in BOTH states, so there is no window
# where collection breaks: it runs headless until `apt install xvfb` is done, then
# a restart auto-upgrades it to headed — no unit edit needed.
#
# xvfb-run gives a real X server with NO physical monitor, so the headed mode is
# display-independent and survives reboot with just enable-linger (unlike a
# graphical-session service, which would need auto-login). Crash/power-loss safety
# is the archive's regardless: adsb_scraper fsyncs each flush and repairs a
# trailing partial line on open.
set -eu

PY=/home/gis-syr/Desktop/scraplan/.venv/bin/python
OUT="${ADSB_OUTPUT:-/home/gis-syr/Desktop/scraplan/aerial_source_ads/data_p1}"

if command -v xvfb-run >/dev/null 2>&1; then
    echo "adsb-collector: xvfb-run present -> HEADED (--no-headless) on a virtual display" >&2
    exec xvfb-run -a "$PY" -m adsb_scraper --no-headless --output "$OUT"
else
    echo "adsb-collector: xvfb not installed -> HEADLESS fallback (install xvfb + restart for headed)" >&2
    exec "$PY" -m adsb_scraper --output "$OUT"
fi
