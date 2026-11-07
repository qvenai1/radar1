"""Unit tests: tracker_satellites 3LE parser + TLE epoch decode."""

from tracker_satellites import parse_3le, parse_tle_epoch

# Real CelesTrak 3LE (stations group, CRLF line endings as served)
SAMPLE = (
    "ISS (ZARYA)             \r\n"
    "1 25544U 98067A   26194.12129675  .00004316  00000+0  86456-4 0  9991\r\n"
    "2 25544  51.6304 171.7447 0006685 289.3803  70.6462 15.48996109575778\r\n"
    "POISK                   \r\n"
    "1 36086U 09060A   26194.12129675  .00004316  00000+0  86456-4 0  9999\r\n"
    "2 36086  51.6304 171.7447 0006685 289.3803  70.6462 15.48996109593175\r\n"
)


def test_parse_two_objects():
    els = parse_3le(SAMPLE)
    assert len(els) == 2
    norad, name, l1, l2, epoch = els[0]
    assert norad == 25544
    assert name == "ISS (ZARYA)"           # trailing pad stripped
    assert l1.startswith("1 25544U") and l2.startswith("2 25544")
    assert epoch is not None and epoch.year == 2026


def test_epoch_decode_and_century_pivot():
    # 26194 → 2026, day 194
    e = parse_tle_epoch("1 25544U 98067A   26194.50000000  .0 0 0 0 1")
    assert e is not None and e.year == 2026
    # YY 98 → 1998 (pivot < 57 = 2000s, else 1900s)
    e2 = parse_tle_epoch("1 25544U 98067A   98001.00000000  .0 0 0 0 1")
    assert e2 is not None and e2.year == 1998


def test_malformed_lines_skipped():
    assert parse_3le("garbage\nno tle here\n") == []
    # a name with only one element line → skipped (needs both 1/2)
    assert parse_3le("NAME\r\n1 25544U ...\r\n") == []
