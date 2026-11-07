"""Session identities and rotation — the anti-ban layer.

Each :class:`Session` pairs a proxy (optional) with a persistent browser profile
directory, so a rotation changes *both* the network egress and the stored
Cloudflare ``cf_clearance``/cookies at once — a fresh identity. :class:`SessionRotator`
holds an ordered ring of them and advances on demand (the orchestrator rotates
when Cloudflare blocks or enrichment stalls).

Pure and side-effect free apart from creating the profile directories, so the
rotation policy is unit-testable without a browser or a network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .exceptions import ConfigError
from .logger import get_logger

_log = get_logger("session")


@dataclass(frozen=True, slots=True)
class Session:
    """One rotatable identity: an egress proxy and a persistent profile."""

    label: str
    proxy: str | None
    profile_dir: str | None


class SessionRotator:
    """An ordered ring of sessions, advanced on block/stall."""

    __slots__ = ("_index", "_rotations", "_sessions")

    def __init__(self, sessions: list[Session]) -> None:
        if not sessions:
            raise ConfigError("SessionRotator requires at least one session")
        self._sessions = sessions
        self._index = 0
        self._rotations = 0

    @property
    def size(self) -> int:
        return len(self._sessions)

    @property
    def rotations(self) -> int:
        return self._rotations

    def current(self) -> Session:
        return self._sessions[self._index]

    def rotate(self) -> Session:
        """Advance to the next identity (wraps). Returns the new current session."""
        if self.size == 1:
            _log.debug("rotate requested but only one session configured; staying put")
            return self.current()
        self._index = (self._index + 1) % self.size
        self._rotations += 1
        nxt = self.current()
        _log.warning("rotated to session %s (rotation #%d)", nxt.label, self._rotations)
        return nxt


def build_sessions(
    *,
    proxies: list[str],
    profile_base: Path | None,
    profile_count: int,
    single_proxy: str | None = None,
    single_profile: str | None = None,
) -> list[Session]:
    """Assemble the session ring from configuration.

    * With a proxy list and/or ``profile_count > 1``, build that many identities,
      pairing ``proxies[i % len]`` with a per-session profile ``session_<i>``.
    * Otherwise fall back to a single session using ``single_proxy`` /
      ``single_profile`` (the plain, non-rotating case).
    """
    if profile_count < 1:
        raise ConfigError("profile_count must be >= 1")

    count = max(profile_count, len(proxies))
    if count <= 1 and not proxies:
        return [Session(label="s0", proxy=single_proxy, profile_dir=single_profile)]

    if profile_base is None:
        raise ConfigError("rotation needs a profile base dir (enable a persistent profile)")

    sessions: list[Session] = []
    for i in range(count):
        proxy = proxies[i % len(proxies)] if proxies else None
        profile = str(profile_base / f"session_{i}")
        Path(profile).mkdir(parents=True, exist_ok=True)
        sessions.append(Session(label=f"s{i}", proxy=proxy, profile_dir=profile))
    _log.info(
        "built %d rotating sessions (%d proxies, %d profiles)", len(sessions), len(proxies), count
    )
    return sessions


def load_proxies(path: Path | None) -> list[str]:
    """Read a proxy list file: one ``scheme://[user:pass@]host:port`` per line.

    Blank lines and ``#`` comments are ignored. A missing path yields an empty
    list (single-session, no proxy).
    """
    if path is None:
        return []
    if not path.exists():
        raise ConfigError(f"proxy list not found: {path}")
    proxies: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            proxies.append(line)
    return proxies
