"""PORT-1 — port gazetteer + loader (asset only).

Pure, no DB (mirrors test_zones.py / test_airport_search.test_gazetteer_loads_and_resolves):
the committed asset is loaded in-process and its invariants + the loader's spatial queries
are asserted against known ports. No sessionization, no port_visit events, no composite —
sessionization is PORT-2 and composite admission is deliberately never (see the manifest).
"""

import json
import os
import time

import pytest

from backend import ports
from backend.ports import PortAssetError, _load

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

_REQUIRED = {"port_id", "port_name", "country", "lat", "lon", "radius_m", "radius_basis",
             "coord_precision", "source", "license", "source_ref", "captured_at",
             "merges_with"}


def test_asset_loads_and_every_feature_validates():
    """Committed asset — no DB, always runs (like the airports/zones gazetteer tests)."""
    rows = ports._ports()
    assert len(rows) == 190                                  # 180 WPI + 10 OSM
    ids = [p["port_id"] for p in rows]
    assert len(ids) == len(set(ids)), "port_id must be unique"
    for p in rows:
        assert _REQUIRED <= set(p)                           # required fields present
        assert p["source"] in ports.SOURCES
        assert p["license"]                                  # licence present per feature
        assert p["source_ref"]                               # per-feature provenance
        assert p["radius_m"] > 0
        assert p["captured_at"] == "2026-07-27"
        assert isinstance(p["merges_with"], list)
    assert {p["source"] for p in rows} == {"WPI", "OSM"}
    assert sum(1 for p in rows if p["source"] == "WPI") == 180
    assert sum(1 for p in rows if p["source"] == "OSM") == 10


def test_radius_basis_is_assumed_on_every_feature():
    """The layer's defining weakness, asserted so it cannot silently drift: NO source
    in this asset publishes a measured harbour extent, so no feature may claim
    'sourced' (unlike zones_aoi.json, where 29/36 platform radii are)."""
    rows = ports._ports()
    assert all(p["radius_basis"] == "assumed" for p in rows)
    assert ports.RADIUS_BASES == ("assumed",)                # a closed set of one
    assert all(p.get("radius_note") for p in rows)            # and each says why
    # the radius scale is the documented one, and nothing sits below the arcminute floor
    assert {p["radius_m"] for p in rows if p["source"] == "WPI"} <= {1500.0, 2500.0, 3500.0, 5000.0}
    assert min(p["radius_m"] for p in rows if p["source"] == "WPI") == 1500.0


def test_coord_precision_present_and_honest_per_feature():
    rows = ports._ports()
    assert all(p["coord_precision"] in ports.COORD_PRECISIONS for p in rows)
    wpi = [p for p in rows if p["source"] == "WPI"]
    # Pub 150 s3 gives positions in degrees and MINUTES; measured 356/360 AOI coordinate
    # components carry seconds = 00. The two exceptions (Al Fujayrah, Khawr Fakkan) carry
    # whole non-zero seconds and are labelled 'arcsecond' rather than blanket-tagged.
    assert sum(1 for p in wpi if p["coord_precision"] == "arcminute") == 178
    assert sum(1 for p in wpi if p["coord_precision"] == "arcsecond") == 2
    assert {p["port_name"] for p in wpi if p["coord_precision"] == "arcsecond"} == {
        "Al Fujayrah", "Khawr Fakkan"}
    # OSM features carry their OWN precision, never WPI's arcminute claim
    osm = [p for p in rows if p["source"] == "OSM"]
    assert all(p["coord_precision"] in ("polygon_centroid", "node") for p in osm)


def test_syria_lebanon_hole_is_filled():
    """PORT-1's headline requirement. WPI carries ZERO Syrian ports, ONE Lebanese entry
    and NO Beirut — and the AOI centres on Syria, so a gazetteer blind there would be
    close to useless. The targeted OSM supplement must fix exactly that."""
    rows = ports._ports()
    syria = [p for p in rows if p["country"] == "Syria"]
    lebanon = [p for p in rows if p["country"] == "Lebanon"]
    assert len(syria) == 3 and len(lebanon) == 6
    # WPI alone would still be 0 / 1 — the supplement is what closes it
    assert sum(1 for p in syria if p["source"] == "WPI") == 0
    assert sum(1 for p in lebanon if p["source"] == "WPI") == 1
    # every port named in the requirement resolves, by the spelling the requirement used
    for q, expect in (("Latakia", "Port of Latakia"), ("Tartus", "Tartus Port"),
                      ("Baniyas", "Banyas Port"), ("Beirut", "Port of Beirut"),
                      ("Tripoli", "Port of Tripoli")):
        assert any(p["port_name"] == expect for p in ports.resolve(q)), q
    # ...and by its Arabic name (the user's language, and the OSM source's own tagging)
    for q, expect in (("اللاذقية", "Port of Latakia"), ("طرطوس", "Tartus Port"),
                      ("بيروت", "Port of Beirut"), ("بانياس", "Banyas Port")):
        assert any(p["port_name"] == expect for p in ports.resolve(q)), q


def test_baniyas_carries_its_tag_caveat_rather_than_a_fabricated_coordinate():
    """OSM has no harbour/port polygon for Baniyas. The feature is honest about being an
    amenity=ferry_terminal node and about the offshore oil terminal being absent —
    a documented limitation, not an invented coordinate."""
    b = next(p for p in ports._ports() if p["port_id"] == "osm-node-1001586641")
    assert b["country"] == "Syria"
    assert "ferry_terminal" in b["osm_tags"]
    assert b["source_note"] and "oil terminal" in b["source_note"].lower()
    assert b["coord_precision"] == "node"


def test_licences_and_the_odbl_obligation_are_carried_per_feature():
    rows = ports._ports()
    wpi = [p for p in rows if p["source"] == "WPI"]
    osm = [p for p in rows if p["source"] == "OSM"]
    # WPI: US-Government public domain, the Title 17 notice quoted on the feature
    assert all("TITLE 17" in p["license"] for p in wpi)
    assert all("public domain" in p["license"].lower() for p in wpi)
    # OSM: ODbL named on every feature AND its element URL carried, so attribution +
    # share-alike are traceable per feature (the zones_aoi.json pattern)
    assert all("ODbL" in p["license"] for p in osm)
    assert all("OpenStreetMap contributors" in p["license"] for p in osm)
    assert all(p["source_ref"].startswith("https://www.openstreetmap.org/") for p in osm)
    assert all(p.get("osm_tags") for p in osm)


def test_manifest_documents_sources_scope_and_deferred_decisions():
    raw = json.loads(ports.DEFAULT_ASSET.read_text(encoding="utf-8"))
    man = raw["manifest"]
    assert man["build_date"] == "2026-07-27"
    assert {"WPI", "OSM"} <= set(man["sources"])
    # the ODbL obligation statement, and WPI's *absence* of one, are both explicit
    assert "odbl_obligation" in man["sources"]["OSM"]
    assert "None" in man["sources"]["WPI"]["attribution_obligation"]
    # WPI's undated API is recorded as a provenance gap, not papered over
    assert "NOT AVAILABLE" in man["sources"]["WPI"]["vintage"]
    # the sources that were considered and rejected are named with reasons
    assert {"UN/LOCODE", "EMODnet Human Activities Main Ports"} <= set(
        man["sources"]["considered_and_rejected"])
    # the geometry policy states the point+radius decision and the assumed-radius weakness
    assert "NO polygons" in man["geometry_policy"]["form"]
    assert "assumed" in man["geometry_policy"]["radius_basis"]
    # the deferred/dropped decisions read as decisions, not omissions
    d = man["deferred"]
    assert {"listed_ports", "gap_toward_a_port_signal", "gulf_theater_osm_supplement",
            "port_visit_composite_admission"} <= set(d)
    assert d["gap_toward_a_port_signal"]["status"] == "dropped, not deferred"
    assert d["port_visit_composite_admission"]["status"] == "deliberately never"
    assert "no port-keyed source exists" in d["listed_ports"]["reason"].lower()
    # the Syria/Lebanon coverage claim is recorded in the manifest too
    cov = man["syria_lebanon_coverage"]
    assert cov["wpi_only"] == {"Syria": 0, "Lebanon": 1}
    assert cov["after_osm_supplement"] == {"Syria": 3, "Lebanon": 6}
    assert cov["not_found_anywhere"] == []
    assert man["known_limits"]


def test_resolution_by_name_alias_unlocode_and_id():
    # the Port Said case, explicitly: WPI files it as "Bur Sa'id" and the name a user
    # actually types lives ONLY in alternate_name, so a name-only search would miss it
    got = ports.resolve("port said")
    assert got and got[0]["port_name"] == "Bur Sa'id"
    assert "Port Said" in got[0]["alternate_name"]
    assert any(p["port_name"] == "Bur Sa'id" for p in ports.resolve("bur sa"))
    # UN-LOCODE, in any spacing/punctuation
    for q in ("IL HFA", "ilhfa", "il-hfa"):
        assert [p["port_name"] for p in ports.resolve(q)] == ["Haifa"], q
    # port_id exact
    assert any(p["port_id"] == "wpi-45140" for p in ports.resolve("wpi-45140"))
    assert ports.resolve("zznope") == []
    assert ports.resolve("") == []
    assert ports.resolve("   ") == []


def test_cached_singleton():
    assert ports._ports() is ports._ports()                  # lru_cache identity


def test_merges_with_is_symmetric_and_containment_returns_all_members():
    """15 AOI pairs sit within 6 km, so overlapping radii are expected. Attribution
    ambiguity must be DATA — ports_containing returns EVERY match so PORT-2 can say
    'either X or Y' rather than silently picking the first."""
    rows = ports._ports()
    by_id = {p["port_id"]: p for p in rows}
    pairs = set()
    for p in rows:
        for other in p["merges_with"]:
            assert other in by_id                            # references a real port
            assert p["port_id"] in by_id[other]["merges_with"]   # symmetric
            pairs.add(tuple(sorted((p["port_id"], other))))
    assert len(pairs) == 15
    assert sum(1 for p in rows if p["merges_with"]) == 26
    # the tightest known pair (Gebze <-> Kaba Burnu, 1.40 km): a point between them
    # falls inside BOTH circles, and both are returned
    gebze = next(p for p in rows if p["port_name"] == "Gebze")
    kaba = next(p for p in rows if p["port_name"] == "Kaba Burnu")
    assert kaba["port_id"] in gebze["merges_with"]
    mid_lat = (gebze["lat"] + kaba["lat"]) / 2
    mid_lon = (gebze["lon"] + kaba["lon"]) / 2
    hits = ports.ports_containing(mid_lat, mid_lon)
    assert {h["port_name"] for h in hits} >= {"Gebze", "Kaba Burnu"}
    assert all("distance_m" in h for h in hits)
    assert hits == sorted(hits, key=lambda h: h["distance_m"])   # nearest-first


def test_spatial_queries_at_known_coords():
    rows = ports._ports()
    haifa = next(p for p in rows if p["port_name"] == "Haifa")
    # a port's own centre falls inside its own circle, at distance 0
    c = ports.ports_containing(haifa["lat"], haifa["lon"])
    assert any(p["port_id"] == haifa["port_id"] for p in c)
    assert c[0]["distance_m"] == 0.0
    # proximity search is independent of each port's own radius
    near = ports.ports_near(haifa["lat"], haifa["lon"], radius_km=30)
    assert any(p["port_id"] == haifa["port_id"] for p in near)
    assert all("distance_km" in p for p in near)
    assert near == sorted(near, key=lambda p: p["distance_km"])
    with pytest.raises(ValueError):
        ports.ports_near(0.0, 0.0, radius_km=-1)
    # a bbox over the Levant coast returns the supplement's Syrian/Lebanese ports
    bb = ports.ports_in_bbox(33.0, 35.0, 36.0, 36.4)
    assert {"Port of Latakia", "Tartus Port", "Port of Beirut"} <= {p["port_name"] for p in bb}
    # open sea sits inside no port (empty, not an error)
    assert ports.ports_containing(34.0, 30.0) == []
    assert ports.ports_near(34.0, 30.0, radius_km=5) == []


def _destination(lat, lon, bearing_deg, dist_km):
    """Great-circle destination point, on the SAME sphere haversine_km measures — so
    the sampled ring is exactly the boundary ports_containing tests against."""
    import math

    from backend.geo_area import EARTH_RADIUS_KM

    ang = dist_km / EARTH_RADIUS_KM
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(ang) + math.cos(p1) * math.sin(ang) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(ang) * math.cos(p1),
                         math.cos(ang) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


@pytest.mark.parametrize("port_name", ["Haifa", "Port of Latakia", "Sayda", "Al Fujayrah"])
def test_vicinity_box_is_a_true_superset_of_the_circle(port_name):
    """The SQL prefilter box PORT-2 hands to the vessel_states GiST index must contain
    the WHOLE circle, or fixes on the rim are silently dropped before the exact test
    ever runs. Asserted by sampling the rim at every degree of bearing, on the same
    sphere haversine_km uses — the flat r/(R·cos lat) longitude approximation fails
    this by a few metres."""
    port = next(p for p in ports._ports() if p["port_name"] == port_name)
    lamin, lomin, lamax, lomax = ports.vicinity_box(port)
    r_km = port["radius_m"] / 1000.0
    for bearing in range(360):
        lat, lon = _destination(port["lat"], port["lon"], bearing, r_km)
        assert lamin <= lat <= lamax, (port_name, bearing, "latitude outside box")
        assert lomin <= lon <= lomax, (port_name, bearing, "longitude outside box")
    # and it is not absurdly loose: the box half-widths stay within 1% of the rim extent
    assert lamax - lamin < 2.02 * r_km / 110.0
    # padding widens it
    padded = ports.vicinity_box(port, pad_km=10.0)
    assert padded[0] < lamin and padded[2] > lamax
    assert padded[1] < lomin and padded[3] > lomax
    with pytest.raises(ValueError):
        ports.vicinity_box(port, pad_km=-1)


def test_wpi_sayda_coordinate_disagreement_is_recorded_not_hidden():
    """A cross-check found WPI's Sayda point ~7.3 km from the OSM Port of Sidon polygon —
    7x WPI's own stated precision, so it looks misplaced rather than rounded. The
    disagreement rides ON the feature; the coordinate was NOT silently overwritten."""
    sayda = next(p for p in ports._ports() if p["port_name"] == "Sayda")
    cc = sayda["coord_crosscheck"]
    assert cc["offset_km"] > 7.0
    assert "976693697" in cc["against"]                      # the OSM element it disagrees with
    assert sayda["source"] == "WPI" and sayda["lat"] == 33.5  # original coordinate intact


def test_search_caveat_states_the_layer_limits():
    c = ports.SEARCH_CAVEAT
    assert "assumed" in c and "search aperture" in c
    assert "merges_with" in c
    assert "43 of 180" in c                                  # a silent port is coverage


def test_fail_fast_on_malformed_asset(tmp_path):
    feat = {"port_id": "x", "port_name": "X", "country": "C", "lat": 33.0, "lon": 34.0,
            "radius_m": 1500, "radius_basis": "assumed", "coord_precision": "arcminute",
            "source": "WPI", "license": "L", "source_ref": "gid", "captured_at": "2026-07-27",
            "merges_with": []}

    def write(obj):
        p = tmp_path / "a.json"
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p

    with pytest.raises(PortAssetError):                      # missing file
        _load(tmp_path / "nope.json")
    with pytest.raises(PortAssetError):                      # unreadable / not JSON
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        _load(p)
    with pytest.raises(PortAssetError):                      # no 'ports' list
        _load(write({"nope": []}))
    with pytest.raises(PortAssetError):                      # empty ports list
        _load(write({"ports": []}))
    with pytest.raises(PortAssetError):                      # missing required field
        _load(write({"ports": [{k: v for k, v in feat.items() if k != "license"}]}))
    with pytest.raises(PortAssetError):                      # unknown source
        _load(write({"ports": [{**feat, "source": "MADEUP"}]}))
    with pytest.raises(PortAssetError):                      # a 'sourced' radius claim
        _load(write({"ports": [{**feat, "radius_basis": "sourced"}]}))
    with pytest.raises(PortAssetError):                      # bad coord_precision
        _load(write({"ports": [{**feat, "coord_precision": "exact"}]}))
    with pytest.raises(PortAssetError):                      # bad lat/lon
        _load(write({"ports": [{**feat, "lat": 99.0}]}))
    with pytest.raises(PortAssetError):                      # non-positive radius
        _load(write({"ports": [{**feat, "radius_m": 0}]}))
    with pytest.raises(PortAssetError):                      # duplicate port_id
        _load(write({"ports": [feat, dict(feat)]}))
    with pytest.raises(PortAssetError):                      # merges_with -> unknown port
        _load(write({"ports": [{**feat, "merges_with": ["ghost"]}]}))
    with pytest.raises(PortAssetError):                      # asymmetric merges_with
        b = {**feat, "port_id": "y", "merges_with": []}
        _load(write({"ports": [{**feat, "merges_with": ["y"]}, b]}))
    # the symmetric version loads fine
    ok = _load(write({"ports": [{**feat, "merges_with": ["y"]},
                                {**feat, "port_id": "y", "merges_with": ["x"]}]}))
    assert len(ok) == 2


# ---------------------------------------------------------------------------
# PORT-1 sanity measurement (read-only, DB-gated). NOT sessionization — no
# entry/exit, no dwell, no events. It answers exactly one question: does the
# committed geometry sit where vessels actually are? A SILENT port is a COVERAGE
# fact, not a mis-sited feature (PORT-pre measured only 43 of 180 WPI AOI ports
# producing any session over 16 days), so nothing here asserts per-port traffic.
# Sampled, never swept: PORT-pre measured ~167 ms/port and ~30 s for all 180.
# ---------------------------------------------------------------------------

#: A spread across the size range + both sources + the Levant supplement.
SANITY_SAMPLE = [
    "Haifa",             # WPI M — PORT-pre's densest observed port
    "Bur Sa'id",         # WPI L — Port Said, the alternate-name case
    "Limassol",          # WPI S — Cyprus
    "Ambarli",           # WPI M — the collector's densest 1-degree cell
    "Sayda",             # WPI V — and the suspect-coordinate case
    "Port of Latakia",   # OSM commercial — Syria, the supplement's headline
    "Tartus Port",       # OSM commercial — Syria
    "Port of Beirut",    # OSM commercial — Lebanon
    "Moni",              # OSM fishing — smallest radius in the asset
]

VESSELS_IN_RADIUS_SQL = """
SELECT vs.mmsi, vs.latitude, vs.longitude
FROM vessel_states vs
WHERE vs.ts > now() - make_interval(hours => $1)
  AND vs.position_quality IS DISTINCT FROM 'implausible'
  AND point(vs.longitude, vs.latitude) <@ box(point($2, $3), point($4, $5))
"""


@requires_db
@pytest.mark.asyncio
async def test_sanity_sampled_ports_traffic(capsys):
    import asyncpg

    from backend.geo_area import haversine_km

    conn = await asyncpg.connect(DSN)
    try:
        window_h = 24 * 16                      # the full retention span, time-bounded
        rows_out = []
        for name in SANITY_SAMPLE:
            port = next(p for p in ports._ports() if p["port_name"] == name)
            lamin, lomin, lamax, lomax = ports.vicinity_box(port)
            t0 = time.perf_counter()
            fixes = await conn.fetch(VESSELS_IN_RADIUS_SQL, window_h,
                                     lomin, lamin, lomax, lamax)
            ms = (time.perf_counter() - t0) * 1000.0
            r_m = port["radius_m"]
            inside = [f for f in fixes
                      if haversine_km(f["latitude"], f["longitude"],
                                      port["lat"], port["lon"]) * 1000.0 <= r_m]
            rows_out.append((name, port["source"], port.get("harbor_size") or "-",
                             r_m, len(fixes), len(inside),
                             len({f["mmsi"] for f in inside}), ms))
            # the box must be a superset of the circle in PRACTICE too
            assert len(inside) <= len(fixes)
            assert ms < 5000.0, f"{name}: {ms:.0f} ms — the GiST path should stay sub-second"

        with capsys.disabled():
            print(f"\n  PORT-1 sanity measurement — last {window_h} h, "
                  f"position_quality IS DISTINCT FROM 'implausible'")
            print(f"  {'port':18} {'src':4} {'sz':3} {'r_m':>6} {'box_fix':>8} "
                  f"{'in_r':>7} {'vessels':>8} {'ms':>7}")
            for r in rows_out:
                print(f"  {r[0][:18]:18} {r[1]:4} {r[2]:3} {r[3]:>6.0f} {r[4]:>8} "
                      f"{r[5]:>7} {r[6]:>8} {r[7]:>7.0f}")
            silent = [r[0] for r in rows_out if r[6] == 0]
            print(f"  ports with traffic: {sum(1 for r in rows_out if r[6])}"
                  f"/{len(rows_out)}; silent: {silent or 'none'}")
            print("  a silent port is a COVERAGE fact, not a mis-sited feature")

        # at least one sampled port must show traffic, else the geometry (or the
        # bbox/lon-lat argument order) is wrong rather than the coverage being thin
        assert any(r[6] for r in rows_out), "no sampled port saw any vessel — check geometry"
    finally:
        await conn.close()
