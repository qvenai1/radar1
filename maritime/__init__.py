"""Maritime AIS tracking layer — mirrors the aviation pipeline's architecture.

Data flow (same shape as tracker_opensky/tracker_airplanes):
    adapters (stream/REST) → in-memory Fleet → periodic snapshots
    → maritime.json (atomic, live view) + maritime.jsonl (append, ground truth)
    → [phase 2] DB mirror into vessel_states with `source` in the natural key.

HARD LIMITS to keep in mind (documented per source in adapters/):
  * Free AIS is TERRESTRIAL only: reliable coverage ~40–60 NM off shore.
    The open ocean is dark — absence of a vessel is NOT evidence of absence.
  * AIS is self-reported and spoofable. Never treat a position as verified
    truth without a second source (SAR imagery, port calls, …).
"""
