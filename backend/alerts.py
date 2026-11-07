"""D5 alert feed — the monitor's live classification signal.

The redesigned monitor's RESTING state is a live dashboard, and this feed is its
heart. It is READ-OVER-SIGNALS: not a detector and not a stored table — every alert
is computed on request from the SAME sources the app already serves:

    emergency squawks  ← the live air snapshot (/live states)
    military air        ← the airplanes.live overlay (military.json)
    military satellite  ← AOI passes of UCS-tagged military sats (satellite_passes)
    maritime composite  ← the hull composite (maritime_hull_composite, ≥2 patterns)
    implausible position← implausible AIS fixes (vessel_states.position_quality)

The severity mapping is DELIBERATELY per-family and honest (see SEVERITY_BASIS):

    emergency   → HIGH   (the only high-by-definition family)
    military    → INFO   (presence — informational, NEVER a threat)
    maritime    → the hull's OWN composite_rank (high|medium|low) — a stronger
                         lead, still not proof
    implausible → LOW    (a spoofed/jammed vessel is usually a VICTIM, not an actor)

`severity_basis`, `vintage` and `live` travel WITH each alert so a card can never
present military presence or an implausible position as a high threat, and a stale
feed reads as stale.

This module is PURE — it turns already-fetched states/rows into Alert rows. All I/O
(the live snapshot, the DB reads) stays in the endpoint, matching how composite.py
ranks what its endpoint fetched.

⚠ GEO STAMPING IS ON THE DB PATH ONLY — a trap that has now bitten twice. The three
live JSON artifacts the collectors write (`latest.json`, `military.json`,
`vessels.json`) carry positions but NO country/near_country/geo_source: the shared
classifier runs in `ingest.py`, on the way into `state_vectors`. `/live` is served
ARCHIVE-FIRST by design (api.py build_live_payload), so its states are unstamped
even though the DB rows for the same aircraft are stamped 100 %. Any family reading
from a live JSON artifact must therefore classify the POSITION itself, through
`_classify_position` — never assume a country field is there.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .country_iso import to_iso
from .mmsi_flag import mmsi_to_iso
# RANK-S2: which rank reasons already name the grey flag — so the Arabic `why` does not state
# the same listed flag twice. Read from the module that OWNS the ranking rule, never re-tested.
from .composite import GREY_IS_THE_REASON
from . import pass_baseline as PB
from . import summaries_ar as AR
from .models import Alert, AlertEntityRef

# --- families / severities -------------------------------------------------
EMERGENCY, MILITARY, MARITIME, IMPLAUSIBLE = "emergency", "military", "maritime", "implausible"
HIGH, MEDIUM, LOW, INFO = "high", "medium", "low", "info"

#: sort key — most-severe first; INFO (presence) sinks below LOW so the honest
#: threat ordering holds (a low-but-real anomaly ranks above informational presence).
#: UNCHANGED by DASH-1, deliberately — see IMPORTANCE_ORDER below.
SEVERITY_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2, INFO: 3}

#: --- the SECOND, ORTHOGONAL axis (DASH-1 step 3) -------------------------------
#:
#: `severity` answers "HOW DANGEROUS is this claim?" and is a threat judgement.
#: `importance` answers "HOW MUCH does this deserve the analyst's eye in a country
#: window?" and is a DISPLAY-ORDERING concern. They are different questions, so they
#: are different fields — the same discipline that keeps position_quality (a fix),
#: maritime_events.classification (an event) and imo_quality (an identity) as three
#: axes that are never conflated.
#:
#: WHY A SECOND AXIS RATHER THAN RE-RANKING SEVERITY. A country-events card wants
#: military presence near the top: "a military aircraft is over Syria right now" is
#: the salient fact in a country watch. But military presence is INFO by an explicit,
#: repeated commitment — "informational, NEVER a threat" — carried per card in
#: severity_basis. Re-ranking SEVERITY_ORDER to lift it would make every existing
#: surface start asserting a threat the project has committed to not asserting.
#: Adding an axis lifts it in ONE view while every card still shows its honest INFO
#: badge and its basis. Nothing is downgraded and nothing is over-claimed.
#:
#: IMPORTANCE IS NOT A THREAT SCORE. Do not colour a card by it, do not sum it, and
#: do not present it as risk. It orders a list; that is all it means.
#:
#: The order, and why each sits where it does:
#:   0 emergency   an active 7500/7600/7700 is both the most severe AND the most
#:                 worth looking at — the one place the two axes agree.
#:   1 military    presence is THE salient fact of a country watch (the DASH ask),
#:                 while staying INFO on the severity axis.
#:   2 maritime    a multi-pattern hull — suspicious, but a standing picture over
#:                 history, not something happening in this country right now.
#:   3 implausible abnormal and real, but usually a VICTIM of spoofing/jamming, so it
#:                 informs the picture rather than demanding the first look.
IMPORTANCE_ORDER: dict[str, int] = {EMERGENCY: 0, MILITARY: 1, MARITIME: 2, IMPLAUSIBLE: 3}

#: Fallback for an unknown family — sorts last, never first. An unrecognised signal
#: must not jump the queue by accident.
IMPORTANCE_UNKNOWN = 9

#: active emergency transponder codes (ICAO): the frontend AlertBar mirrors this map.
EMERGENCY_SQUAWKS: dict[str, str] = {
    "7500": "hijack",
    "7600": "radio-failure",
    "7700": "general-emergency",
}

#: WHY each signal carries the severity it does — ALWAYS surfaced on the card so
#: military (info) and implausible (low) never read as high threat. Maritime's basis
#: is per-hull (built from its pattern count + grey flag), so it is composed inline.
SEVERITY_BASIS: dict[str, str] = {
    "emergency_squawk": "active emergency squawk — high by definition",
    "military_aircraft": "military aircraft presence — informational, not a threat",
    "military_satellite": "military satellite AOI pass — forward SGP4 prediction; strategic tag frozen 2023",
    "implausible_position": "implausible AIS position — usually a victim (spoof / jam / receiver fault), not an actor",
    # ⛔ MT-DETECT-3 — DELIBERATELY THE WEAKEST BASIS IN THE TABLE, and it must stay that
    # way. This is ONE pattern, from ONE source, on a `ship_id` that carries no MMSI or IMO
    # (measured 100 % NULL), so it can never be corroborated against the hull's history the
    # way a composite is. It is not a composite and must never be presented as one.
    "maritime_single": "single-pattern observation from the MarineTraffic scrape — one "
                       "source, no cross-pattern corroboration, and an identity that "
                       "cannot be joined to the AIS hull history",
}

#: IMO-2 spine caveats appended to a maritime alert's severity_basis when the hull's
#: IMO integrity is questionable (migration 035). A malformed spine has already been
#: re-keyed to the MMSI (migration 036); this says why the lead's identity is soft.
SPINE_CAVEAT = {
    "checksum_fail": "spine: checksum-fail",
    "malformed": "spine: malformed (decode artifact — keyed by MMSI)",
    "contested": "spine: contested (IMO collision)",
}

#: The compounding, human-facing honesty note — mirrors the per-family caveats the
#: underlying endpoints already return, unified for the feed.
CAVEAT = (
    "Alerts are read LIVE over existing signals — no stored alert table, no new "
    "detector. Severity is per-family and honest: emergency squawks are HIGH; "
    "military presence (aircraft + satellite AOI passes) is INFO — informational, "
    "not a threat; maritime composites carry their own rank (a stronger lead, still "
    "NOT proof); implausible positions are LOW (a spoofed/jammed vessel is usually a "
    "victim). Liveness is MIXED and shown per alert: air/sea feeds can be stale, "
    "satellite passes are forward SGP4 predictions, and each maritime composite "
    "carries its last-event vintage. AIS and ADS-B are spoofable."
)


def _iso(v: Any) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (v or None)


def importance_of(family: str) -> int:
    """The display-ordering rank for a family (see IMPORTANCE_ORDER). Lower sorts
    first. Orthogonal to severity — never a threat score."""
    return IMPORTANCE_ORDER.get(family, IMPORTANCE_UNKNOWN)


def count_positions_in(states: list[dict], iso: str,
                       lat_key: str = "latitude", lon_key: str = "longitude") -> int:
    """How many of these live states sit INSIDE or NEAR the given AOI country.

    Used to scope a domain stat to one country. It classifies POSITIONS through the
    shared classifier rather than reading a country field, because the live JSON
    artifacts do not carry one (see the module docstring) — and because deriving it
    here from the same polygons the filter uses means the count and the alert list
    can never disagree about where a border is.

    NEAR counts as present: the AOI is a neighbourhood, and the alert families it is
    compared against also admit NEAR. Measured cost 0.048 ms/point, and it is only
    paid when a country is requested."""
    n = 0
    for st in states:
        if iso in _classify_position(st.get(lat_key), st.get(lon_key)):
            n += 1
    return n


def _countries(*values: Any) -> list[str]:
    """Country NAMES (scalars or iterables, in preference order) → deduped ISO codes.

    Order is preserved so the first-listed source wins visually — for the air
    families that means the containing country before the near-border one. Falsy
    values drop out; an unrecognised name survives verbatim (see country_iso.to_iso),
    so a non-AOI country is never silently erased from an alert."""
    out: list[str] = []
    for v in values:
        if not v:
            continue
        items = [v] if isinstance(v, str) else list(v)
        for name in items:
            code = to_iso(name)
            if code and code not in out:
                out.append(code)
    return out


def _classify_position(lat: Any, lon: Any) -> list[str]:
    """Position → AOI countries, via the SAME shared classifier the ingest path uses.

    Needed because the airplanes.live military overlay is written straight to
    military.json by the collector and never passes through the geo classifier, so
    unlike the /live snapshot its states carry NO country/near_country — only
    lat/lon (measured: 0 of 2 states had either field). Re-classifying here keeps
    the military family's country attribution identical to every other layer's
    rather than inventing a second definition of "in Syria".

    Returns [] on a missing position or any classifier failure — an alert must never
    be lost to a geography lookup."""
    if lat is None or lon is None:
        return []
    try:
        from .geo_area import get_area
        geo = get_area().classify_columns(float(lat), float(lon))
    except Exception:                      # noqa: BLE001 — geography must never drop an alert
        return []
    return _countries(geo.country, geo.near_country)


# --- family builders (pure: rows/states in → Alert rows out) ---------------

# ======================================================================================
# BE-SUMMARY-3 — the emergency family's Arabic register.
#
# ⛔ INVERSE CALIBRATION (see `summaries_ar`): a squawk is REPORTED BY THE AIRCRAFT, so the
# occurrence is never hedged. The reservation moves to what the code MEANS — a category,
# not a severity — and to the fact that mis-setting is common. No cause is implied, and no
# duration is stated because the contract carries none.
#
# ⚠ EVERY CODE THE FAMILY HANDLES HAS A TEMPLATE. `SQUAWK_MEANING_AR` is keyed by the same
# three codes as `EMERGENCY_SQUAWKS`, and the assertion below fails loudly at import if the
# two ever drift — a new code reaching the family without a sentence would otherwise ship an
# alert whose Arabic is silently empty.
# ======================================================================================

assert set(AR.SQUAWK_MEANING_AR) == set(EMERGENCY_SQUAWKS), (
    "every emergency squawk the family handles needs an Arabic template: "
    f"{set(EMERGENCY_SQUAWKS) ^ set(AR.SQUAWK_MEANING_AR)} has none"
)

#: per-code caveat tails. 7700 adds nothing beyond the shared base: its own meaning is
#: already "a general emergency", and any extra clause would start describing the flight.
_EMG_CAVEAT_TAIL = {"7500": AR.EMG_CAVEAT_7500, "7600": AR.EMG_CAVEAT_7600}


def _emergency_ar(sq: str, icao: str, callsign: str, isos: list[str]) -> tuple[str, str, str, str]:
    """(headline, why, caveat) for one emergency squawk. Pure: fills fixed templates from
    the fields the alert already carries. Nothing is recomputed or inferred."""
    who = (AR.EMG_WHO_CALLSIGN.format(callsign=callsign) if callsign
           else AR.EMG_WHO_ICAO.format(icao24=icao))
    headline = AR.EMG_HEAD.format(meaning=AR.SQUAWK_MEANING_AR[sq], who=who,
                                  where=AR.airspace_phrase(isos))
    caveat = AR.EMG_CAVEAT_BASE + _EMG_CAVEAT_TAIL.get(sq, "")
    lede = AR.NEWS_LEDE_EMERGENCY.format(meaning=AR.SQUAWK_MEANING_AR[sq],
                                         where=AR.airspace_phrase(isos))
    return headline, AR.EMG_WHY, caveat, lede


def _age_s(observed_iso: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds between an observation and now. `None` when there is nothing to measure.

    ⛔ REQUIRED ON EVERY LIVE ROW (Alert.observed_age_s). Kay et al. (CHI 2016) found the
    freshness stamp is "one of the only available signals for expert users to assess risk",
    and MarineTraffic states its own stamp exists so a reader can judge currency "before
    relying on it for any decision".
    ⚠ NEGATIVE IS RETURNED AS-IS, not clamped to zero. A satellite pass starts in the FUTURE,
    so its age is negative by construction — clamping would erase the very distinction this
    field was added to expose, and a consumer seeing a negative age is seeing the truth."""
    if not observed_iso:
        return None
    try:
        dt = datetime.fromisoformat(str(observed_iso))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return round((ref - dt).total_seconds(), 1)


def build_emergency_alerts(states: list[dict], vintage: str | None, live: bool) -> list[Alert]:
    """Air family — every live aircraft squawking 7500/7600/7700. HIGH by
    definition (the one non-informational live family)."""
    out: list[Alert] = []
    for s in states:
        sq = str(s.get("squawk") or "")
        if sq not in EMERGENCY_SQUAWKS:
            continue
        icao = str(s.get("icao24") or "")
        cs = (s.get("callsign") or "").strip()
        out.append(Alert(
            domain="air", family=EMERGENCY, signal_type="emergency_squawk",
            importance=importance_of(EMERGENCY),
            severity=HIGH, severity_basis=SEVERITY_BASIS["emergency_squawk"],
            entity_ref=AlertEntityRef(kind="aircraft", id=icao, label=cs or icao,
                                      live_key=icao),
            # ⚠ NOT a read. /live is served ARCHIVE-FIRST from latest.json, which the
            # collector writes WITHOUT geo columns — the classifier runs on the
            # ingest.py DB path, not the archive one (measured: country is 0/567 on
            # /live while geo_source is 100 % on the DB rows). The DB-backed shape
            # is still honoured first for the day /live is served from it.
            countries=(_countries(s.get("country"), s.get("near_country"))
                       or _classify_position(s.get("latitude"), s.get("longitude"))),
            ts=vintage, vintage=vintage, live=live,
            # DASH-3 — an active squawk is an INSTANT the feed observed: it applies at the
            # snapshot time and was known at the snapshot time. phenomenon_end stays None
            # because the alert carries no duration (see EMG_CAVEAT_BASE, which says outright
            # that the code's persistence is NOT part of this signal).
            phenomenon_start=vintage, result_time=vintage, observed_age_s=_age_s(vintage),
            summary=f"squawk {sq} — {EMERGENCY_SQUAWKS[sq]}",
            **dict(zip(("headline", "why", "caveat", "lede"),
                       _emergency_ar(sq, icao, cs,
                                     (_countries(s.get("country"), s.get("near_country"))
                                      or _classify_position(s.get("latitude"),
                                                            s.get("longitude")))), strict=True)),
            source="opensky (latest.json)",
        ))
    return out


# ======================================================================================
# BE-SUMMARY-4 — the military AIRCRAFT family's Arabic register.
#
# ⛔ SCOPE: `military_aircraft` ONLY. The military family also carries `military_satellite`,
# which is deliberately NOT covered here — a pass is a FORWARD SGP4 PREDICTION, so it needs
# future tense («يمرّ», never «رُصد») and a third kind of caveat (SGP4 accuracy decays with
# TLE age). Its own item. The drift gate below makes that omission LOUD rather than silent.
#
# ⛔ THE COMMITMENT THIS MUST NOT BREAK: military presence is INFORMATION, NEVER A THREAT.
# Severity stays INFO and `severity_basis` is untouched; nothing below adds a motion verb,
# a proximity judgement or a mission. See `summaries_ar` for the refused verbs and why.
# ======================================================================================

#: The military family's signal types, split into COVERED and explicitly DEFERRED.
#: ⚠ Read from SEVERITY_BASIS rather than hand-listed, so the gate below tracks the real
#: taxonomy instead of a copy of it that can rot.
_MIL_SIGNAL_TYPES = {k for k in SEVERITY_BASIS if k.startswith("military_")}
_MIL_AR_COVERED = {"military_aircraft", "military_satellite"}
_MIL_AR_DEFERRED: set[str] = set()          # BE-SUMMARY-5 closed the last one

assert _MIL_SIGNAL_TYPES == _MIL_AR_COVERED | _MIL_AR_DEFERRED, (
    "a military signal_type is neither covered by the Arabic register nor explicitly "
    f"deferred: {_MIL_SIGNAL_TYPES ^ (_MIL_AR_COVERED | _MIL_AR_DEFERRED)}. Give it a "
    "template or add it to _MIL_AR_DEFERRED on purpose — never let it ship empty Arabic"
)


def _military_air_ar(icao: str, callsign: str, acdesc: str | None,
                     baro_m: float | None, isos: list[str]) -> tuple[str, str, str, str]:
    """(headline, why, caveat) for one military aircraft. Pure: fills fixed templates from
    fields the overlay already carries. No motion verb, no proximity, no mission."""
    who = (AR.MIL_WHO_CALLSIGN.format(callsign=callsign) if callsign
           else AR.MIL_WHO_ICAO.format(icao24=icao))
    where = AR.airspace_phrase(isos) or AR.MIL_NO_GEO
    typ = AR.MIL_TYPE.format(type=acdesc) if acdesc else ""
    # ⚠ rounded to the metre for readability — a FORMAT, not a derivation. The unit is the
    # record's own (tracker_airplanes normalises ft → m at ingestion).
    alt = AR.MIL_ALT.format(alt=f"{round(baro_m):,}") if baro_m is not None else ""
    headline = AR.MIL_AIR_HEAD.format(where=where, who=who, type=typ, alt=alt)
    caveat = AR.MIL_CAVEAT_ID + AR.MIL_CAVEAT_LAWFUL + AR.MIL_CAVEAT_NO_MISSION
    lede = AR.NEWS_LEDE_MIL_AIR.format(
        who=(callsign or icao), where=AR.airspace_phrase(isos))
    return headline, AR.MIL_AIR_WHY, caveat, lede


def build_military_air_alerts(states: list[dict], vintage: str | None, live: bool) -> list[Alert]:
    """Air family — live military aircraft (airplanes.live overlay). INFO: presence,
    not a threat."""
    out: list[Alert] = []
    for s in states:
        icao = str(s.get("icao24") or "")
        cs = (s.get("callsign") or "").strip()
        typ = s.get("aircraft_type") or s.get("type") or ""
        # The overlay bypasses the geo classifier, so country/near_country are
        # absent here (measured) — fall back to classifying its position.
        codes = _countries(s.get("country"), s.get("near_country")) \
            or _classify_position(s.get("latitude"), s.get("longitude"))
        bits = [b for b in (typ, ", ".join(codes)) if b]
        out.append(Alert(
            domain="air", family=MILITARY, signal_type="military_aircraft",
            importance=importance_of(MILITARY),
            severity=INFO, severity_basis=SEVERITY_BASIS["military_aircraft"],
            entity_ref=AlertEntityRef(kind="aircraft", id=icao, label=cs or icao,
                                      live_key=icao),
            countries=codes,
            ts=vintage, vintage=vintage, live=live,
            phenomenon_start=vintage, result_time=vintage, observed_age_s=_age_s(vintage),
            summary="military aircraft" + (f" — {', '.join(bits)}" if bits else ""),
            **dict(zip(("headline", "why", "caveat", "lede"),
                       _military_air_ar(icao, cs, s.get("acdesc"),
                                        s.get("baro_altitude"), codes), strict=True)),
            source="airplanes.live",
        ))
    return out


# ======================================================================================
# BE-SUMMARY-5 — the space family's Arabic register. The fifth and last.
#
# ⛔ FUTURE TENSE THROUGHOUT: the pass has not happened. «رُصد»/«سُجّل» are false here, and
# the subject is the COMPUTATION, not a sensor and not the object.
# ⛔ INHERITS THE MILITARY COMMITMENT WHOLE: severity stays INFO, and BE-SUMMARY-4's refused
# verbs apply verbatim plus the orbital variants (يراقب/يستطلع/يتجسّس/يمسح).
# ⚠ The epoch span is `start_time − tle_epoch`: BOTH are fields the row already carries, so
# this is arithmetic on the record, not a new measurement, and it is PURE (no `now`).
# ======================================================================================


def _satellite_ar(name: str, isos: list[str], start_iso: str | None,
                  epoch_iso: str | None, closest_km: float | None,
                  alt_km: float | None = None,
                  verdict: Any = None) -> tuple[str, str, str, str]:
    """(headline, why, caveat) for one predicted AOI pass. Pure: fills fixed templates."""
    obj = AR.SAT_OBJECT_MIL.format(name=name)
    # ⚠ trimmed to the minute: the raw ISO carries microseconds and an offset, which read
    # as noise inside a sentence. A FORMAT of the carried value, not a change to it — `ts`
    # still carries the full precision for any consumer that needs it.
    # ⛔ BE-FIX-1: bidi-isolated. Two number groups («2026-08-03» and «23:49Z») separated by a
    # space are ordered right-to-left by the RTL paragraph unless isolated — measured reversed
    # in the real UI. The value's bytes are unchanged; only two invisible marks are added.
    start_txt = (AR.iso_run(start_iso[:16].replace("T", " ") + "Z") if start_iso
                 else "وقت غير محدَّد")  # ⚠ the fallback is Arabic → never isolated
    names = [AR.AOI_ISO_TO_NAME_AR.get(i, i) for i in isos if i]
    if names:
        joined = names[0] if len(names) == 1 else (
            f"{names[0]} و{names[1]}" if len(names) == 2
            else f"{'، '.join(names[:-1])} و{names[-1]}")
        headline = AR.SAT_HEAD.format(object=obj, countries=joined, start=start_txt)
    else:
        headline = AR.SAT_HEAD_NO_COUNTRY.format(object=obj, start=start_txt)
    # ⛔ THE DISTANCE IS NAMED FOR WHAT IT MEASURES, AND THE ALTITUDE RIDES WITH IT.
    # `closest_km` is a GROUND-TRACK distance (passes_derive), not the object's range. On a
    # GNSS satellite that read as «41 كم» while the object was ~20,000 km up — off by ~490x.
    # See the note on AR.SAT_CLOSEST for the measured pair. Altitude absent -> the ground
    # track is still stated, but nothing is invented in its place.
    if closest_km is not None:
        km = f"{round(closest_km):,}"
        headline += (AR.SAT_CLOSEST_ALT.format(km=km, alt=f"{round(alt_km):,}")
                     if alt_km is not None else AR.SAT_CLOSEST.format(km=km))

    # ⛔ BASELINE-1 — the comparison with this object's OWN usual geometry. Always stated,
    # including when the answer is "no basis yet": an omitted line would let a missing
    # baseline read as normality, which is the invention the baseline exists to prevent.
    if verdict is not None:
        tmpl = AR.SAT_BASELINE_BY_REASON.get(verdict.reason, AR.SAT_BASELINE_UNKNOWN)
        med = verdict.usual_closest_km
        headline += (tmpl.format(median=f"{round(med):,}") if "{median}" in tmpl
                     and med is not None else
                     (AR.SAT_BASELINE_UNKNOWN if "{median}" in tmpl else tmpl))

    caveat = AR.SAT_CAVEAT_COMPUTED
    # (2) the element's age — stated from the carried epoch, or its absence declared.
    span = None
    if epoch_iso and start_iso:
        try:
            span = (datetime.fromisoformat(start_iso) - datetime.fromisoformat(epoch_iso)).total_seconds()
        except ValueError:
            span = None
    if epoch_iso and span is not None and span >= 0:
        # ⚠ the epoch date reverses too — measured, and it would have been missed by
        # reasoning: a single hyphenated group looks safe but is not.
        caveat += AR.SAT_CAVEAT_EPOCH.format(epoch=AR.iso_run(epoch_iso[:10]), span=AR.days_span(span))
    elif epoch_iso:
        caveat += AR.SAT_CAVEAT_EPOCH.format(epoch=AR.iso_run(epoch_iso[:10]), span="مدّة غير محسوبة")
    else:
        caveat += AR.SAT_CAVEAT_NO_EPOCH
    caveat += AR.SAT_CAVEAT_DECAY
    caveat += (AR.SAT_CAVEAT_NO_COUNTRY.format(country=names[0]) if names
               else AR.SAT_CAVEAT_NO_COUNTRY_GENERIC)
    caveat += AR.SAT_CAVEAT_TAG + AR.SAT_CAVEAT_NO_MISSION
    # The comparison's own provenance: «المعتاد» is back-propagated, not observed. Appended
    # only when a comparison was actually made, so an UNKNOWN verdict does not carry a
    # caveat about a baseline it did not use.
    if verdict is not None and verdict.verdict != PB.UNKNOWN:
        caveat += AR.SAT_CAVEAT_BASELINE
    names_ar = [AR.AOI_ISO_TO_NAME_AR.get(i, i) for i in isos if i]
    lede = AR.NEWS_LEDE_MIL_SAT.format(
        object=name, where=(" فوق " + "، ".join(names_ar)) if names_ar else "")
    return headline, AR.SAT_WHY, caveat, lede


def build_military_sat_alerts(rows: list[dict]) -> list[Alert]:
    """Space family — UCS-tagged military satellites with an AOI pass in the forward
    window. INFO: presence; the pass is a forward SGP4 PREDICTION and the strategic
    tag is frozen 2023 (both stated in the basis). Not live — computed."""
    out: list[Alert] = []
    for r in rows:
        norad = str(r.get("norad_id"))
        name = r.get("object_name") or f"#{norad}"
        start = _iso(r.get("start_time"))
        closest = r.get("closest_km")
        # Structural, not only prose: these were already computed into
        # satellite_passes.countries/near_countries and then thrown away in the
        # summary string. A pass crosses several borders, hence a list.
        codes = _countries(r.get("countries"), r.get("near_countries"))
        countries = r.get("countries") or []
        over = ", ".join(countries) if countries else None
        users = r.get("users") or "Military"
        oc = r.get("operator_country")
        # Mean altitude from the object's own satellite_orbit row, when the endpoint
        # joined it. Averaged across apogee/perigee because a single "altitude" for an
        # elliptical orbit is a simplification either way — and stating the mean beside a
        # ground-track distance is what stops the distance being read as a range.
        ap, pe = r.get("apogee_km"), r.get("perigee_km")
        alt_km = ((ap + pe) / 2.0) if (ap is not None and pe is not None) else None

        # BASELINE-1 — is this pass usual for THIS object? The row carries the baseline
        # (LEFT JOIN, so it is legitimately absent for a satellite not yet baselined) and
        # `is_persistent`, which settles the geosync case before any sample counting.
        bl = (PB.Baseline(
            n_passes=int(r["bl_n"]), window_days=int(r.get("bl_days") or 0),
            passes_per_day=float(r.get("bl_per_day") or 0.0),
            closest_p10=r.get("bl_p10"), closest_median=r.get("bl_median"),
            usual_countries=tuple(r.get("bl_countries") or ()))
            if r.get("bl_n") is not None else None)
        verdict = PB.classify(closest, list(countries or []) + list(r.get("near_countries") or []),
                              bl, is_persistent=bool(r.get("is_persistent")))
        parts = []
        if closest is not None:
            parts.append(f"ground track {closest:.0f} km"
                         + (f" · altitude {alt_km:.0f} km" if alt_km is not None else ""))
        if over:
            parts.append(f"over {over}")
        tail = f" — {'; '.join(parts)}" if parts else ""
        out.append(Alert(
            domain="space", family=MILITARY, signal_type="military_satellite",
            importance=importance_of(MILITARY),
            severity=INFO, severity_basis=SEVERITY_BASIS["military_satellite"],
            entity_ref=AlertEntityRef(kind="satellite", id=norad, label=name,
                                      live_key=norad),
            countries=codes,
            ts=start, vintage=_iso(r.get("tle_epoch")), live=False,
            # ⛔ DASH-3 — THE FAMILY THIS AXIS EXISTS FOR. The pass has not happened: its
            # phenomenon window is ahead of now, while `computed_at` records when the TLE was
            # propagated to predict it. That ordering — result BEFORE phenomenon — is OGC's
            # own machine test for a prediction, so `is_prediction` becomes true here and
            # nowhere else without a single hand-set flag.
            # ⚠ observed_age_s is measured against the TLE EPOCH, not the pass: the epoch is
            # the observation this prediction rests on, and its age is what degrades the
            # forecast (see SAT_CAVEAT_EPOCH, which already states the epoch and its span).
            phenomenon_start=start, phenomenon_end=_iso(r.get("end_time")),
            result_time=_iso(r.get("computed_at")),
            observed_age_s=_age_s(_iso(r.get("tle_epoch"))),
            baseline_verdict=verdict.verdict,
            summary=f"AOI pass{tail}" + (f" · {users}" + (f" ({oc})" if oc else "")),
            **dict(zip(("headline", "why", "caveat", "lede"),
                       _satellite_ar(name, codes, start, _iso(r.get("tle_epoch")),
                                     closest, alt_km, verdict), strict=True)),
            source="celestrak TLE + UCS strategic",
        ))
    return out


def _keys(hull: Any, live_keys: dict[str, list[str]] | None) -> list[str]:
    """The hull's MMSIs, recency-ordered when the endpoint resolved them."""
    if live_keys is not None and hull.hull in live_keys:
        return live_keys[hull.hull]
    return [m for m in (getattr(i, "mmsi", None)
                        for i in (getattr(hull, "identities", None) or [])) if m]


def _maritime_basis(n_pattern_types: int, grey: bool, rank: str) -> str:
    base = (f"composite of {n_pattern_types} pattern type"
            f"{'s' if n_pattern_types != 1 else ''} — a stronger lead, still not proof")
    if grey:
        base += "; involves a grey/sanctions flag"
    return base


# ======================================================================================
# BE-SUMMARY-1 — the maritime family's readable Arabic register.
#
# Fixed templates from `summaries_ar`, filled from fields that ALREADY EXIST on
# CompositeHull. Nothing here recomputes a count, a rank or a country.
#
# ⚠ WHICH PATTERNS A HULL HAS comes from `timeline[].event_type`, not from the n_* counts.
# Measured over 400 real hulls: `n_pattern_types` equals the number of DISTINCT timeline
# event types in 400/400 cases, but exceeds the number of non-zero n_* fields in 71/400 —
# because `gnss_anomaly` and `zone_dwell` have no count column. So the timeline set is the
# authoritative pattern set and reading it REPRODUCES the existing field rather than
# deriving a new one. ⚠ It also means today's English `summary` understates those 71 hulls:
# it lists only the counted patterns while n_pattern_types counts one more.
#
# ⚠ Consequence for tone: gaps / rendezvous / identity-changes carry a COUNT; GNSS anomaly
# and zone dwell state PRESENCE ONLY. No number is invented for them, and none is softened
# into "several" — a vague quantity reads as a precise one withheld.
# ======================================================================================

#: the three patterns that have a count field, in the order the headline lists them
_COUNTED = (("ais_gap", "n_gaps"), ("rendezvous", "n_rendezvous"),
            ("identity_change", "n_identity_changes"))
_HEAD_COUNTED = {"ais_gap": AR.GAP_HEAD, "rendezvous": AR.RENDEZVOUS_HEAD,
                 "identity_change": AR.IDENTITY_HEAD}
_HEAD_PRESENT = {"gnss_anomaly": AR.GNSS_HEAD, "zone_dwell": AR.ZONE_DWELL_HEAD}
_CAVEATS = {"ais_gap": AR.CAVEAT_GAP, "rendezvous": AR.CAVEAT_RENDEZVOUS,
            "identity_change": AR.CAVEAT_IDENTITY, "gnss_anomaly": AR.CAVEAT_GNSS,
            "zone_dwell": AR.CAVEAT_ZONE_DWELL}


def _hull_patterns(h) -> list[str]:
    """The hull's DISTINCT pattern types, from its timeline — the authoritative set (see
    the note above). Ordered by the taxonomy, not by first appearance, so two hulls with
    the same patterns always read the same way."""
    seen = {getattr(t, "event_type", None) for t in (getattr(h, "timeline", None) or [])}
    order = ["ais_gap", "rendezvous", "identity_change", "gnss_anomaly", "zone_dwell"]
    return [p for p in order if p in seen]



def _first(xs: list[str]) -> str | None:
    """First element or None — typed replacement for the `(xs[:1] or [None])[0]` idiom."""
    return xs[0] if xs else None

def _maritime_ar(h, flag: str | None, isos: list[str]) -> tuple[str, str, str, str]:
    """(headline, why, caveat) for one hull. Pure: reads fields, fills templates."""
    pats = _hull_patterns(h)
    waters = AR.waters_phrase(isos)

    # ---- headline: one clause per pattern, counted where a count exists ----
    clauses: list[str] = []
    for pat, field in _COUNTED:
        if pat in pats:
            n = int(getattr(h, field, 0) or 0)
            if n:
                clauses.append(_HEAD_COUNTED[pat].format(times=AR.times(n), waters=""))
    for pat in ("gnss_anomaly", "zone_dwell"):
        if pat in pats:
            clauses.append(_HEAD_PRESENT[pat].format(waters=""))

    if len(pats) >= 2:
        labels = "، ".join(AR.PATTERN_AR.get(p, p) for p in pats)
        tmpl = AR.COMPOSITE_HEAD_DUAL if len(pats) == 2 else AR.COMPOSITE_HEAD
        lead = tmpl.format(n=len(pats), patterns=labels, waters=waters)
        headline = lead + ". " + "؛ ".join(clauses)
    elif clauses:
        # ONE pattern — never called a composite, and the waters ride on the clause
        headline = clauses[0] + waters
    else:
        headline = ""
    if flag:
        headline = (headline + " · " if headline else "") + AR.FLAG_LINE.format(country=flag)

    # ---- lede: the news title, from the SAME facts the headline states ----
    # ⛔ «تزامن N أنماط» — a COUNT of coinciding pattern types, which is what the composite
    # measures. NOT «مؤشرات غير اعتيادية»: "unusual" is a comparison against a norm, and no
    # maritime baseline exists (BASELINE-1 built one for satellites only). See NEWS-1.
    # ⚠ Read from the hull here, not from the caller's `ident` — that local lives in
    # build_maritime_alerts and this function only receives (h, flag, isos). A NameError
    # here 500'd /api/v1/alerts until it was caught by running the endpoint.
    _id0 = h.identities[0] if getattr(h, "identities", None) else None
    _vessel = getattr(_id0, "name", None) if _id0 else None
    _lede = (AR.NEWS_LEDE_MARITIME.format(patterns=AR.patterns_count(len(pats)),
                                          vessel=_vessel or f"#{h.hull}", waters=waters)
             if len(pats) >= 2 else
             AR.NEWS_LEDE_MARITIME_ONE.format(
                 pattern=AR.PATTERN_AR.get(pats[0], pats[0]) if pats else "مؤشّر",
                 vessel=_vessel or f"#{h.hull}", waters=waters))

    # ---- why: the rank's reason, restating the existing basis ----
    #
    # ⛔ RANK-S2 — TWO PATTERN COUNTS EXIST AND THEY ARE NOT INTERCHANGEABLE. The rank's
    # explanation reads `n_pattern_types`, the field the RANK was computed from; the headline
    # above lists `_hull_patterns` (the timeline), which is authoritative for WHICH patterns
    # were observed. Reading the timeline here produced «الرتبة مرتفعة لنمط واحد مرصود» on a
    # hull the feed admits only at n_pattern_types >= 2 — measured on 1 of 200 live alerts:
    # the sentence explained a rank its own count could not have produced.
    #
    # ⚠ And the CAUSE now comes from `rank_reason` rather than being inferred from the count,
    # because the count is not the cause: two pattern types alone are MEDIUM, so crediting
    # them for a HIGH hull named an insufficient condition (172 of 200). See
    # composite.rank_with_reason — the rule stays written once, there.
    npt = int(getattr(h, "n_pattern_types", 0) or 0)
    rank = AR.RANK_AR.get(h.composite_rank, h.composite_rank)
    reason = getattr(h, "rank_reason", "") or ""
    # an unknown or absent reason states the rank and NO cause — never a wrong one
    tmpl = AR.WHY_BY_REASON.get(reason, AR.WHY_RANK_ONLY)
    why = tmpl.format(rank=rank, patterns_n=AR.patterns_count(npt))
    # ⛔ the flag is appended ONLY when it is not already named as the determinant — otherwise
    # its own template and this clause would state the same listed flag twice in one sentence
    if getattr(h, "involves_grey", False) and reason not in GREY_IS_THE_REASON:
        why += AR.WHY_GREY
    spine = AR.WHY_SPINE.get(getattr(h, "imo_quality", None) or "")
    if spine:
        why += spine

    # ---- caveat: MANDATORY. one per pattern present, plus the composite clause ----
    parts = [_CAVEATS[p] for p in pats if p in _CAVEATS]
    if len(pats) >= 2:
        parts.insert(0, AR.CAVEAT_COMPOSITE)
    caveat = " ".join(parts)
    if len(isos) > 1:
        caveat += AR.CAVEAT_MULTI_WATERS
    return headline, why, caveat, _lede


def build_maritime_alerts(hulls: list, live_keys: dict[str, list[str]] | None = None) -> list[Alert]:
    """Sea family — the hull composite (≥2 pattern types). Severity IS the hull's own
    composite_rank (high|medium|low). Not a live position: it is a standing picture
    over the hull's event history, so live=False and vintage = its last event.

    `live_keys` maps hull → its MMSIs ordered MOST-RECENTLY-HEARD FIRST, so
    entity_ref.live_key is the MMSI currently carrying the IMO and alt_keys are the
    earlier ones. The ordering is a DB read, so the endpoint supplies it and this
    module stays pure; omitted, the ref falls back to the hull's own MMSI list
    unordered, which is still usable but not recency-resolved."""
    out: list[Alert] = []
    for h in hulls:
        ident = h.identities[0] if getattr(h, "identities", None) else None
        name = getattr(ident, "name", None) if ident else None
        flag = getattr(ident, "flag_country", None) if ident else None
        counts = []
        if h.n_gaps:
            counts.append(f"{h.n_gaps} gap{'s' if h.n_gaps != 1 else ''}")
        if h.n_rendezvous:
            counts.append(f"{h.n_rendezvous} rendezvous")
        if h.n_identity_changes:
            counts.append(f"{h.n_identity_changes} identity-change{'s' if h.n_identity_changes != 1 else ''}")
        waters = f" · {', '.join(h.aoi_waters)} waters" if h.aoi_waters else ""
        label = name or (f"IMO {h.imo}" if h.imo else f"#{h.hull}")
        # IMO-2: a questionable spine is a caveat on the lead's reliability (never
        # hides the hull) — carried in the severity_basis, the honesty register.
        basis = _maritime_basis(h.n_pattern_types, h.involves_grey, h.composite_rank)
        spine = SPINE_CAVEAT.get(getattr(h, "imo_quality", None) or "")
        if spine:
            basis += f"; {spine}"
        out.append(Alert(
            domain="sea", family=MARITIME, signal_type="maritime_composite",
            importance=importance_of(MARITIME),
            severity=h.composite_rank,
            severity_basis=basis,
            entity_ref=AlertEntityRef(
                kind="vessel", id=(h.imo or h.hull), label=label,
                # id is the IMO (durable across a re-flag); live_key is the MMSI that
                # actually opens the live payload. One-to-many, so the rest survive
                # in alt_keys rather than being dropped.
                live_key=_first(_keys(h, live_keys)),
                alt_keys=_keys(h, live_keys)[1:]),
            # aoi_waters (M1) IS the geographic attribution — whose waters the
            # hull's events touched. NOT flag_country, which is registration.
            countries=_countries(h.aoi_waters),
            ts=h.last_event_ts, vintage=h.last_event_ts, live=False,
            # ⛔ DASH-3 — THE COMPOSITE IS A SPAN, NOT A MOMENT. `ts` reports the hull's LAST
            # event because a feed has to sort by something; the picture itself runs from the
            # first observed event to the last, and a renderer drawing a lane needs both ends.
            # ⚠ These are the hull's OBSERVED events. There is no prediction here, so
            # result_time is the last event too and `is_prediction` stays false — the correct
            # answer for a standing historical picture.
            # ⚠ getattr WITH DEFAULTS, MATCHING THIS FILE'S EXISTING IDIOM (`getattr(h,
            # "n_pattern_types", 0)`, `getattr(h, "rank_reason", "")`, …). This builder is
            # duck-typed over anything hull-shaped, and the first draft read the attributes
            # directly — which broke 18 tests whose fixtures are partial SimpleNamespaces.
            # A missing attribute must degrade to "not stated", never to an exception.
            phenomenon_start=getattr(h, "first_event_ts", None) or h.last_event_ts,
            phenomenon_end=h.last_event_ts,
            result_time=h.last_event_ts,
            observed_age_s=_age_s(h.last_event_ts),
            first_seen_at=getattr(h, "first_seen_at", None),
            counterparts=list(getattr(h, "counterparts", []) or []),
            pattern_spans=list(getattr(h, "pattern_spans", []) or []),
            n_pattern_spans=int(getattr(h, "n_pattern_spans", 0) or 0),
            summary=(", ".join(counts) or "multi-pattern") + waters
                    + (f" · {flag}" if flag else ""),
            # BE-SUMMARY-1: the Arabic register alongside the English `summary`, which is
            # kept until the frontend consumes these and is then removed in its own commit.
            **dict(zip(("headline", "why", "caveat", "lede"),
                       _maritime_ar(h, flag, _countries(h.aoi_waters)), strict=True)),
            # MID-ISO-1: the registration flag as an ISO code, resolved MID -> ISO from
            # the hull's own MMSI. ⛔ Not from `flag` (the NAME) and not via country_iso —
            # that module is the seven-country AOI layer and governs filtering, which a
            # registry flag must never feed. None when the MMSI is absent or its MID is
            # unallocated: "cannot resolve this registry to a code", never "no flag".
            flag_iso=mmsi_to_iso(_first(_keys(h, live_keys))),
            source="maritime_hull_composite",
        ))
    return out


# ======================================================================================
# BE-SUMMARY-2 — the implausible family's Arabic register.
#
# ⛔ SEPARATELY CALIBRATED, and the reason is in `summaries_ar`: an implausible position is
# a DATA DEFECT, not behaviour, so every sentence's subject is the OBSERVATION and never
# the hull. Nothing here implies intent, and the caveat names the technical causes
# outright — the victim/actor question is one the G rules exist to keep open.
#
# ⚠ NO SECOND DERIVATION: the per-kind counts arrive already aggregated by the alerts
# query (`n_on_land` / `n_impossible_speed` / `n_teleport`), read from `anomaly_flags`
# which `gnss_anomaly.py` wrote. This function counts nothing itself.
# ======================================================================================

#: flag → (count field, headline template), in the order a headline lists them
_IMPL_KINDS = (
    ("on_land", "n_on_land", AR.IMPL_HEAD_ON_LAND),
    ("impossible_speed", "n_impossible_speed", AR.IMPL_HEAD_SPEED),
    ("teleport_return", "n_teleport", AR.IMPL_HEAD_TELEPORT),
)


def _implausible_ar(r: dict, isos: list[str]) -> tuple[str, str, str, str]:
    """(headline, why, caveat) for one hull's implausible fixes. Pure: reads the
    already-aggregated counts and fills fixed templates."""
    present = [(k, int(r.get(f) or 0), t) for k, f, t in _IMPL_KINDS if int(r.get(f) or 0)]
    waters = AR.waters_phrase(isos)

    clauses = [t.format(times=AR.times(n)) for _, n, t in present]
    if len(present) >= 2:
        kinds = "، ".join(AR.IMPL_KIND_AR[k] for k, _, _ in present)
        headline = AR.IMPL_HEAD_LEAD.format(waters=waters, kinds=kinds) + ". " + "؛ ".join(clauses)
    elif clauses:
        headline = clauses[0] + waters
    else:
        # every implausible row carries at least one flag (measured 7,006/7,006), but a
        # future rule could add a verdict with none — say nothing rather than guess.
        headline = ""

    why = AR.IMPL_WHY + (AR.IMPL_WHY_MULTI if len(present) >= 2 else "")
    caveat = AR.IMPL_CAVEAT
    if any(k == "on_land" for k, _, _ in present):
        caveat += AR.IMPL_CAVEAT_ON_LAND
    lede = AR.NEWS_LEDE_IMPL.format(vessel=r.get("name") or f"MMSI {r.get('mmsi')}",
                                    waters=waters)
    return headline, why, caveat, lede


def build_implausible_alerts(rows: list[dict], live: bool) -> list[Alert]:
    """Sea family — vessels with implausible AIS fixes in the window (one alert per
    hull, aggregating its fixes). LOW: usually a victim, not an actor. A LIVE signal
    (it comes off the live feed), so live tracks the feed."""
    out: list[Alert] = []
    for r in rows:
        mmsi = str(r.get("mmsi"))
        name = r.get("name") or mmsi
        n = r.get("n_fixes") or 0
        last = _iso(r.get("last_ts"))
        flag = r.get("flag_country")
        out.append(Alert(
            domain="sea", family=IMPLAUSIBLE, signal_type="implausible_position",
            importance=importance_of(IMPLAUSIBLE),
            severity=LOW, severity_basis=SEVERITY_BASIS["implausible_position"],
            entity_ref=AlertEntityRef(kind="vessel", id=mmsi, label=name,
                                      live_key=mmsi),
            # Aggregated over the hull's implausible fixes in the window — a drifting
            # spoofed track can cross waters. NOT flag_country (registration).
            countries=_countries(r.get("countries"), r.get("near_countries")),
            ts=last, vintage=last, live=live,
            # ⛔ AN INSTANT, NOT A SPAN — and the first draft of this line invented a `first`
            # that does not exist in this builder. The query aggregates the hull's implausible
            # fixes and returns `last_ts` and `n_fixes`; it carries NO first-fix column, so the
            # honest phenomenon is the last observed fix. ⚠ Do NOT substitute `n_fixes` or a
            # window bound for a start time: the window is the QUERY's shape, not the event's.
            # Observed, never predicted.
            phenomenon_start=last, result_time=last,
            observed_age_s=_age_s(last),
            summary=f"{n} implausible position fix{'es' if n != 1 else ''}"
                    + (f" · {flag}" if flag else ""),
            source="vessel_states (position_quality)",
            flag_iso=mmsi_to_iso(mmsi),
            **dict(zip(("headline", "why", "caveat", "lede"),
                       _implausible_ar(r, _countries(r.get("countries"), r.get("near_countries"))), strict=True)),
        ))
    return out


# ======================================================================================
# MT-DETECT-3 — the MarineTraffic single-source family.
#
# ⛔ WHY IT IS A SEPARATE SIGNAL_TYPE RATHER THAN MORE MARITIME COMPOSITE ROWS. The
# composite's whole claim is "≥2 patterns coincide on ONE STITCHED HULL". An MT event has
# one pattern and no stitchable identity, so admitting it to that family would let a
# strictly weaker observation inherit a stronger family's weight — the exact defect the
# migration-043 guard keeps out of the composite view. Same family label (`maritime`, so
# subscriber filters keep working), different signal_type, fixed LOW severity.
#
# ⚠ LOW IS NOT A HEDGE, IT IS THE HONEST RANK. LOW routes to the digest rather than
# interrupting (backend/notify/policy.py), which is the right weight for an uncorroborated
# single-source lead.
# ======================================================================================

def build_mt_alerts(rows: list[dict]) -> list[Alert]:
    """MarineTraffic-derived events -> LOW alerts. One row per event.

    `rows` carry the event plus the vessel's latest MT statics (name/flag), joined by the
    endpoint. Nothing is recomputed here."""
    out: list[Alert] = []
    for r in rows:
        sid = str(r.get("ship_id") or "")
        pattern = str(r.get("event_type") or "")
        name = r.get("name")
        label = name or f"MT {sid}"
        pat_ar = AR.MT_PATTERN_AR.get(pattern, pattern)
        who = (AR.MT_WHO_NAME.format(name=name) if name
               else AR.MT_WHO_ID.format(ship_id=sid))
        codes = _countries(r.get("country"), r.get("near_country"))
        headline = AR.MT_HEAD.format(pattern=pat_ar, who=who,
                                     waters=AR.waters_phrase(codes))
        out.append(Alert(
            domain="sea", family=MARITIME, signal_type="maritime_single",
            importance=importance_of(MARITIME),
            severity=LOW, severity_basis=SEVERITY_BASIS["maritime_single"],
            # ⛔ live_key IS the ship_id, and it opens /maritime/vessel?ship_id= (MT-DETECT-3).
            # Before that route existed this entity had no destination at all — an alert that
            # looks actionable and is not is worse than no alert.
            entity_ref=AlertEntityRef(kind="vessel", id=sid, label=label, live_key=sid),
            countries=codes,
            ts=_iso(r.get("start_ts")), vintage=_iso(r.get("start_ts")), live=False,
            phenomenon_start=_iso(r.get("start_ts")), phenomenon_end=_iso(r.get("end_ts")),
            result_time=_iso(r.get("computed_at")) or _iso(r.get("start_ts")),
            observed_age_s=_age_s(_iso(r.get("start_ts"))),
            summary=f"{pattern} (marinetraffic, single-source)",
            source="marinetraffic (scrape)",
            headline=headline, why=AR.MT_WHY, caveat=AR.MT_CAVEAT,
            lede=AR.NEWS_LEDE_MT.format(pattern=pat_ar, vessel=label,
                                        waters=AR.waters_phrase(codes)),
        ))
    return out


def sort_alerts(alerts: list[Alert]) -> list[Alert]:
    """Most-severe first (INFO sinks below LOW), then most-recent first within a
    severity band. Two stable passes: recency (desc) then severity (asc).

    UNCHANGED by DASH-1 — this is the honest threat ordering and stays the default
    for the feed. The country card uses sort_by_importance() instead."""
    by_recent = sorted(alerts, key=lambda a: "" if a.ts is None else a.ts, reverse=True)
    return sorted(by_recent, key=lambda a: SEVERITY_ORDER.get(a.severity, 9))


def sort_by_importance(alerts: list[Alert]) -> list[Alert]:
    """Most-IMPORTANT first, then most-recent within a band — the country card's
    ordering (DASH-1 step 3).

    A DISPLAY order, not a threat order: it lifts military presence toward the top
    because that is the salient fact of a country watch, while every card keeps its
    honest severity badge and severity_basis. `sort_alerts` remains the severity
    ordering and is untouched."""
    by_recent = sorted(alerts, key=lambda a: "" if a.ts is None else a.ts, reverse=True)
    return sorted(by_recent, key=lambda a: a.importance)
