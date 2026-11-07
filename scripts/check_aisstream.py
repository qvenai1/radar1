#!/usr/bin/env python3
"""Is the AIS feed actually delivering? A differential probe against AISStream.

WHY THIS EXISTS. On 2026-08-11 the maritime tab was empty and the bot sent no maritime
alerts. Every layer downstream looked healthy and said so:

    scraplan-maritime.service   active            (but msgs=0 reconnects=311)
    the detectors               computed minutes ago (over frozen data)
    /api/v1/maritime            HTTP 200          (available:false, count:0)

The collector was a GREEN UNIT THAT HAD DELIVERED NOTHING FOR SIX DAYS. `systemctl status`
cannot see that, because "the process is running" and "the data is arriving" are different
facts. This script measures the second one.

WHAT IT DISTINGUISHES, and why a single connection test cannot. A silent socket has three
very different causes, and they need different fixes:

    key rejected      -> AISStream CLOSES the connection almost immediately
    bbox wrong        -> connection stays open, silent for your box, DATA for the whole world
    account inactive  -> connection stays open, silent EVEN FOR THE WHOLE WORLD

So it probes all three: a deliberately bogus key (to prove rejection looks like a close), a
whole-world box (to take geography out of the question), and finally the project's own box.

Usage:
    .venv/bin/python scripts/check_aisstream.py
    .venv/bin/python scripts/check_aisstream.py --key <a-new-key-to-test>

Exit code 0 = data arrived. Non-zero = it did not, and the reason is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv()

URL = "wss://stream.aisstream.io/v0/stream"

#: The project's collection box, as passed to tracker_maritime.py
#: (--bbox 12.0 24.0 42.0 63.5), in AISStream's [[lat,lon],[lat,lon]] order.
PROJECT_BOX = [[[12.0, 24.0], [42.0, 63.5]]]
WORLD_BOX = [[[-90.0, -180.0], [90.0, 180.0]]]


async def probe(key: str, boxes: list, wait: float, label: str) -> tuple[str, str]:
    """(outcome, detail). outcome is 'data' | 'silent' | 'closed' | 'error'."""
    try:
        import websockets
    except ImportError:
        return "error", "the `websockets` package is not installed in this venv"
    try:
        async with websockets.connect(URL, open_timeout=15) as ws:
            await ws.send(json.dumps({"APIKey": key, "BoundingBoxes": boxes}))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=wait)
            except asyncio.TimeoutError:
                return "silent", f"connection stayed OPEN, no frame in {wait:.0f}s"
            text = raw if isinstance(raw, str) else raw.decode(errors="replace")
            return "data", text[:160]
    except Exception as exc:                       # noqa: BLE001
        name = type(exc).__name__
        if "ConnectionClosed" in name:
            code = getattr(exc, "code", "?")
            return "closed", f"server closed the connection (code={code})"
        return "error", f"{name}: {exc}"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", help="test a specific key instead of AISSTREAM_API_KEY")
    ap.add_argument("--wait", type=float, default=25.0,
                    help="seconds to wait for a frame (default 25)")
    args = ap.parse_args()

    key = args.key or os.environ.get("AISSTREAM_API_KEY", "")
    if not key:
        print("✗ AISSTREAM_API_KEY is not set (and no --key given).")
        print("  Get one at https://aisstream.io and put it in .env")
        return 2
    # ⛔ Never print the key. Length only — enough to spot an empty or truncated paste.
    print(f"key present (len={len(key)})\n")

    print("1) bogus key — establishes what REJECTION looks like from this network")
    out, detail = await probe("x" * 40, WORLD_BOX, 12, "bogus")
    print(f"   {out:6} — {detail}")
    rejection_closes = out == "closed"
    if not rejection_closes:
        print("   ⚠ a bad key did NOT get closed, so 'stays open' cannot prove the key is"
              " good. Treat the result below as inconclusive on the key.")

    print("\n2) your key, WHOLE WORLD — removes the bounding box from the question")
    world, wdetail = await probe(key, WORLD_BOX, args.wait, "world")
    print(f"   {world:6} — {wdetail}")

    print("\n3) your key, the project's box (12,24)-(42,63.5)")
    proj, pdetail = await probe(key, PROJECT_BOX, args.wait, "project")
    print(f"   {proj:6} — {pdetail}")

    print("\n" + "=" * 68)
    if proj == "data":
        print("✅ AIS IS FLOWING for the project's box. If the maritime tab is still empty,")
        print("   the break is downstream — check: systemctl --user status scraplan-maritime")
        return 0
    if world == "data" and proj != "data":
        print("⚠ Data arrives WORLDWIDE but not for the project's box.")
        print("  The key and account are fine; the BOUNDING BOX is the problem.")
        print("  Check the --bbox argument in scraplan-maritime.service.")
        return 1
    if world == "closed" or proj == "closed":
        print("✗ The server CLOSED the connection — the key is rejected.")
        print("  Generate a new key at https://aisstream.io, put it in .env as")
        print("  AISSTREAM_API_KEY, then: systemctl --user restart scraplan-maritime")
        return 1
    if rejection_closes:
        print("✗ The key is ACCEPTED (a bad key is closed; yours is not) but NO DATA")
        print("  arrives — not even for the whole world. That is an ACCOUNT-SIDE state:")
        print("  quota exhausted, plan lapsed, or the key deactivated server-side.")
        print("\n  → Sign in at https://aisstream.io and check the key's quota/status.")
        print("  → Generating a fresh key is the usual fix; put it in .env and run:")
        print("     systemctl --user restart scraplan-maritime.service")
        print("     .venv/bin/python scripts/check_aisstream.py")
    else:
        print("✗ No data arrived, and this network could not be shown to reject bad keys,")
        print("  so the cause is not isolated. Re-run when the network is stable.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
