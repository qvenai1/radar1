"""AOI country name ⇄ ISO 3166-1 alpha-2 — the one shared place both the alert
filter and any display surface resolve a country through.

WHY THIS EXISTS. The committed boundary asset (`backend/data/aoi_countries.geojson`)
carries a `name` property and nothing else — no ISO code. Every country fact the
backend produces is therefore a NAME: `vessel_states.country`, `state_vectors.country`,
`satellite_passes.countries`, `CompositeHull.aoi_waters`. A caller that wants a flag,
a URL segment, or a stable key needs a code. Without a single shared map, that
translation gets re-invented per call site and drifts.

SCOPE — the seven AOI countries only, deliberately. This is NOT a world gazetteer:
`geo_area.py` only ever classifies a position into these seven (or nothing), so a
map with more entries would imply a coverage this project does not have.

THE CODES ARE NOT AN ASSET. ISO 3166-1 alpha-2 is a public standard and these seven
two-letter codes are facts, not a downloaded dataset — nothing is vendored, and there
is no licence to carry.

RESOLUTION IS FORGIVING BUT LOSSLESS. `normalise` accepts either a name or a code, in
any case, so `?country=SY`, `?country=sy` and `?country=Syria` are one query. An
unknown value resolves to None (the caller decides whether that is an error), and
`to_iso` passes an unrecognised name through VERBATIM rather than dropping it —
flag-never-delete, the same rule the data layers use: a country we cannot code is
still a country we observed.
"""

from __future__ import annotations

#: The seven AOI countries, keyed by the exact `name` in aoi_countries.geojson.
#: Order mirrors `AOI_COUNTRIES` in the collector for readability, not semantics.
AOI_NAME_TO_ISO: dict[str, str] = {
    "Syria": "SY",
    "Turkey": "TR",
    "Iraq": "IQ",
    "Jordan": "JO",
    "Lebanon": "LB",
    "Israel": "IL",
    "Cyprus": "CY",
}

#: Reverse map, built once — never hand-maintained in parallel (that is how the two
#: directions drift apart).
AOI_ISO_TO_NAME: dict[str, str] = {v: k for k, v in AOI_NAME_TO_ISO.items()}

#: Case-insensitive lookup for both directions, built once.
_LOWER: dict[str, str] = {n.lower(): i for n, i in AOI_NAME_TO_ISO.items()}
_LOWER.update({i.lower(): i for i in AOI_ISO_TO_NAME})


def to_iso(name: str | None) -> str | None:
    """Country NAME → ISO alpha-2. An unrecognised non-empty name passes through
    VERBATIM (never dropped): a country we cannot code is still one we observed.
    Returns None only for a null/blank input."""
    if not name:
        return None
    return AOI_NAME_TO_ISO.get(name.strip(), name.strip())


def to_name(code: str | None) -> str | None:
    """ISO alpha-2 → the AOI country name, or the input verbatim if unrecognised."""
    if not code:
        return None
    return AOI_ISO_TO_NAME.get(code.strip().upper(), code.strip())


def normalise(value: str | None) -> str | None:
    """Any accepted spelling of an AOI country → its ISO code, else None.

    Accepts a name or a code, any case: 'Syria', 'syria', 'SY', 'sy' → 'SY'.
    None means "not one of the seven" — the caller decides if that is a 400 or an
    empty result, because this module must not own HTTP semantics."""
    if not value:
        return None
    return _LOWER.get(value.strip().lower())


def iso_codes() -> list[str]:
    """The seven ISO codes, in the map's declared order."""
    return list(AOI_NAME_TO_ISO.values())


def names() -> list[str]:
    """The seven country names exactly as the boundary asset spells them."""
    return list(AOI_NAME_TO_ISO)
