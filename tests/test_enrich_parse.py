"""Unit tests: backend.enrich parsers against REAL probed upstream shapes."""

from backend.enrich import _parse_route, _strip_html

# verbatim shape probed live from api.adsbdb.com/v0/callsign/ABY288 (2026-07-08)
ADSBDB_ROUTE = {
    "response": {
        "flightroute": {
            "callsign": "ABY288",
            "airline": {"name": "Air Arabia", "icao": "ABY", "iata": "G9",
                        "country": "United Arab Emirates"},
            "origin": {
                "country_iso_name": "TR", "country_name": "Turkey",
                "elevation": 325, "iata_code": "IST", "icao_code": "LTFM",
                "latitude": 41.261297, "longitude": 28.741951,
                "municipality": "Arnavutköy, Istanbul", "name": "İstanbul Airport",
            },
            "destination": {
                "country_iso_name": "AE", "country_name": "United Arab Emirates",
                "elevation": 111, "iata_code": "SHJ", "icao_code": "OMSJ",
                "latitude": 25.3286, "longitude": 55.5172,
                "municipality": "Sharjah", "name": "Sharjah International Airport",
            },
        }
    }
}


def test_route_parsed():
    r = _parse_route(ADSBDB_ROUTE)
    assert r["found"] is True
    assert r["airline_name"] == "Air Arabia"
    assert r["origin_icao"] == "LTFM" and r["origin_iata"] == "IST"
    assert r["dest_icao"] == "OMSJ" and r["dest_municipality"] == "Sharjah"
    assert isinstance(r["origin_lat"], float)


def test_unknown_callsign_shape():
    # adsbdb returns a plain string response for unknowns
    assert _parse_route({"response": "unknown callsign"})["found"] is False


def test_empty_and_malformed():
    assert _parse_route(None)["found"] is False
    assert _parse_route({})["found"] is False
    assert _parse_route({"response": {"flightroute": {"origin": {}}}})["found"] is False


def test_strip_html_commons_artist():
    # Commons extmetadata Artist values are HTML — the vessel-photo path
    # strips tags for a clean photographer credit.
    assert _strip_html('<a href="/wiki/User:X">Bernard Gagnon</a>') == "Bernard Gagnon"
    assert _strip_html("kees torn") == "kees torn"
    assert _strip_html(None) is None
    assert _strip_html("") is None
