"""S2a — Space-Track client foundation: auth, session reuse, rate limiting,
backoff resilience, and Alpha-5 handling. Fully offline (fake transport + fake
clock); no network, no credentials, no persistence.

The LIVE authenticated round-trip is a separate manual proof
(`python backend/spacetrack_client.py` with SPACETRACK_USER/PASS set) and is not
part of the unit suite — these tests prove the mechanism deterministically.
"""

import logging

import pytest

from backend.spacetrack_client import (
    AuthError, RateLimiter, SpaceTrackClient, SpaceTrackConfig,
    alpha5_to_int, int_to_alpha5,
)

CFG_OK = SpaceTrackConfig(identity="user@example.com", password="unit-test-pw",
                          max_per_min=100, max_per_hour=1000)


# ---- fakes -----------------------------------------------------------------

class FakeResp:
    def __init__(self, status_code=200, text="", json_data=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        return self._json


class FakeHttp:
    """Records login POSTs and serves queued GET responses (or 200 [] default).
    A queued Exception is raised to simulate a network error."""
    def __init__(self, login_resp=None, get_responses=None):
        self.login_resp = login_resp or FakeResp(200, "")
        self._gets = list(get_responses or [])
        self.posts = []   # (url, data) per login
        self.gets = []

    async def post(self, url, data=None):
        self.posts.append((url, data))
        return self.login_resp

    async def get(self, url):
        self.gets.append(url)
        if self._gets:
            r = self._gets.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return FakeResp(200, "", json_data=[])

    async def aclose(self):
        pass


class Clock:
    """Deterministic clock: only advances when sleep() is awaited."""
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def time(self):
        return self.t

    async def sleep(self, dt):
        self.sleeps.append(dt)
        self.t += dt


def _client(http, clock=None, cfg=CFG_OK):
    clock = clock or Clock()
    return SpaceTrackClient(cfg, http=http, sleep=clock.sleep, time_fn=clock.time), clock


# ---- Alpha-5 (pure) --------------------------------------------------------

def test_alpha5_classic_ids_passthrough():
    assert alpha5_to_int("25544") == 25544
    assert int_to_alpha5(25544) == "25544"
    assert int_to_alpha5(544) == "00544"


def test_alpha5_decode_encode_roundtrip():
    # A0001 -> 100001 (A=10); boundaries; I and O are skipped in the alphabet
    assert alpha5_to_int("A0001") == 100001
    assert int_to_alpha5(100001) == "A0001"
    assert alpha5_to_int("Z9999") == 339999
    assert int_to_alpha5(339999) == "Z9999"
    # 'J' immediately follows 'H' (no 'I'): J0000 -> 180000
    assert alpha5_to_int("J0000") == 180000
    assert int_to_alpha5(180000) == "J0000"
    for n in (100000, 123456, 250000, 339999):
        assert alpha5_to_int(int_to_alpha5(n)) == n


def test_alpha5_out_of_range_rejected():
    with pytest.raises(ValueError):
        int_to_alpha5(340000)


# ---- config / credentials --------------------------------------------------

def test_from_env_reads_credentials(monkeypatch):
    monkeypatch.setenv("SPACETRACK_USER", "u@x.com")
    monkeypatch.setenv("SPACETRACK_PASS", "secret")
    monkeypatch.setenv("SPACETRACK_MAX_PER_MIN", "17")
    cfg = SpaceTrackConfig.from_env()
    assert cfg.identity == "u@x.com" and cfg.has_credentials
    assert cfg.max_per_min == 17


def test_missing_creds_default_below_hard_limits():
    cfg = SpaceTrackConfig()
    assert not cfg.has_credentials
    assert cfg.max_per_min < 30 and cfg.max_per_hour < 300  # margin under the cap


def test_password_absent_from_repr():
    cfg = SpaceTrackConfig(identity="u", password="TOP-SECRET-VALUE")
    assert "TOP-SECRET-VALUE" not in repr(cfg)


@pytest.mark.asyncio
async def test_missing_creds_failfast_no_network():
    http = FakeHttp()
    client, _ = _client(http, cfg=SpaceTrackConfig(identity="", password=""))
    with pytest.raises(AuthError) as ei:
        await client.login()
    assert http.posts == []                       # never touched the network
    assert "not set" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_password_never_logged(caplog):
    pw = "SUPERSECRET-PW-123"
    cfg = SpaceTrackConfig(identity="u@x.com", password=pw)
    http = FakeHttp(login_resp=FakeResp(200, ""))
    client, _ = _client(http, cfg=cfg)
    with caplog.at_level(logging.DEBUG, logger="spacetrack"):
        await client.login()
    assert pw not in caplog.text                   # never logged
    assert pw in http.posts[0][1]["password"]      # but IS sent to the auth endpoint


@pytest.mark.asyncio
async def test_login_failure_raises_without_password():
    cfg = SpaceTrackConfig(identity="u@x.com", password="SECRET-PW")
    http = FakeHttp(login_resp=FakeResp(401, "Login Failed"))
    client, _ = _client(http, cfg=cfg)
    with pytest.raises(AuthError) as ei:
        await client.login()
    assert "SECRET-PW" not in str(ei.value)        # error carries identity + status only


# ---- session reuse ---------------------------------------------------------

@pytest.mark.asyncio
async def test_session_established_once_and_reused():
    http = FakeHttp()
    client, _ = _client(http)
    for _ in range(3):
        await client.query("gp", NORAD_CAT_ID=25544)
    assert client.logins == 1          # logged in ONCE
    assert len(http.posts) == 1        # ...one login POST for three queries
    assert client.queries == 3


# ---- rate limiting ---------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limiter_spaces_calls_at_minute_cap():
    clock = Clock()
    lim = RateLimiter(max_per_min=3, max_per_hour=1000,
                      time_fn=clock.time, sleep_fn=clock.sleep)
    for _ in range(3):
        await lim.acquire()
    assert clock.sleeps == []                       # first 3 free
    await lim.acquire()                             # 4th in the window must wait
    assert len(clock.sleeps) == 1
    assert abs(clock.sleeps[0] - 60.0) < 1e-6       # spaced to the minute window


@pytest.mark.asyncio
async def test_client_applies_rate_limit_across_queries():
    http = FakeHttp()
    cfg = SpaceTrackConfig(identity="u", password="p", max_per_min=2, max_per_hour=1000)
    client, clock = _client(http, cfg=cfg)
    # login + first query fill the 2/min window; the 2nd query must be spaced
    await client.query("gp", NORAD_CAT_ID=1)
    await client.query("gp", NORAD_CAT_ID=2)
    assert client._limiter.waits >= 1


# ---- resilience: backoff, not crash ----------------------------------------

@pytest.mark.asyncio
async def test_throttle_429_backs_off_then_succeeds():
    http = FakeHttp(get_responses=[
        FakeResp(429, "", headers={"Retry-After": "5"}),
        FakeResp(200, "", json_data=[{"NORAD_CAT_ID": "25544"}]),
    ])
    client, clock = _client(http)
    out = await client.query("gp", NORAD_CAT_ID=25544)
    assert out == [{"NORAD_CAT_ID": "25544"}]
    assert client.throttles == 1
    assert 5.0 in clock.sleeps                       # honored Retry-After


@pytest.mark.asyncio
async def test_503_backs_off_then_succeeds():
    http = FakeHttp(get_responses=[FakeResp(503, ""), FakeResp(200, "", json_data=[])])
    client, clock = _client(http)
    out = await client.query("gp", NORAD_CAT_ID=25544)
    assert out == [] and client.throttles == 1
    assert any(s > 0 for s in clock.sleeps)


@pytest.mark.asyncio
async def test_network_error_retries_then_succeeds():
    http = FakeHttp(get_responses=[ConnectionError("boom"), FakeResp(200, "", json_data=[])])
    client, _ = _client(http)
    out = await client.query("gp", NORAD_CAT_ID=25544)
    assert out == [] and client.retries == 1


@pytest.mark.asyncio
async def test_401_triggers_single_relogin():
    http = FakeHttp(get_responses=[FakeResp(401, ""), FakeResp(200, "", json_data=[])])
    client, _ = _client(http)
    out = await client.query("gp", NORAD_CAT_ID=25544)
    assert out == []
    assert client.logins == 2          # initial + exactly one re-login
    assert len(http.posts) == 2


# ---- query shape -----------------------------------------------------------

def test_query_url_is_basicspacedata_shape():
    client, _ = _client(FakeHttp())
    url = client._build_url("gp", "json", {"NORAD_CAT_ID": 25544})
    assert url == ("https://www.space-track.org/basicspacedata/query/"
                   "class/gp/NORAD_CAT_ID/25544/format/json")


# ---- Alpha-5 correctness in the data layer (FIXED in S2c) -------------------

def test_parse_3le_decodes_alpha5_six_digit_id():
    """S2c fix: parse_3le decodes the Alpha-5 catalog field via alpha5_to_int, so
    a 6-digit id round-trips instead of being silently dropped. (Was a strict-
    xfail in S2a documenting the gap; now passes.)"""
    from tracker_satellites import parse_3le
    alpha5_3le = (
        "STARLINK-99999\r\n"
        "1 A0001U 24001A   26194.50000000  .00000000  00000+0  00000+0 0  9990\r\n"
        "2 A0001  53.0000 100.0000 0001000  90.0000 270.0000 15.50000000    01\r\n"
    )
    els = parse_3le(alpha5_3le)
    assert len(els) == 1                 # no longer dropped
    assert els[0][0] == 100001           # A0001 -> 100001
    # a classic 5-digit id still parses unchanged
    classic = ("ISS (ZARYA)\r\n"
               "1 25544U 98067A   26194.50000000 0 0 0 0 9990\r\n"
               "2 25544  51.6 171.7 0006 289 70 15.5 01\r\n")
    assert parse_3le(classic)[0][0] == 25544
