/**
 * The single API client. REST fetchers for every v1 endpoint plus the
 * WS-with-polling-fallback live feed (LiveFeed).
 *
 * Transport contract (from README-backend.md):
 *  - WS /api/v1/stream sends {"type":"snapshot","data":<live payload>};
 *    current snapshot arrives on connect; a snapshot MAY be re-pushed with
 *    the same snapshot_time and a fuller states set → REPLACE WHOLESALE.
 *  - If WS is down, poll GET /api/v1/live every 30 s and reconnect the WS
 *    with exponential backoff.
 */

import type {
  AdsbResponse,
  AircraftEnrichment,
  // UI-3 — the seven coverage-bearing contracts
  AirCountryDaily,
  AirCountrySeries,
  CoverageResponse,
  FlagDistributionResponse,
  MaritimeSeries,
  RendezvousPointsResponse,
  Alert,
  CaseItem,
  CompositeResponse,
  CrossrefResponse,
  DarkVesselsResponse,
  GapsResponse,
  GnssAnomaliesResponse,
  JammingZonesResponse,
  HealthResponse,
  IdentityChangesResponse,
  MaritimeResponse,
  MilitaryResponse,
  RendezvousResponse,
  VesselEnrichment,
  VesselLiveResponse,
  SatellitesResponse,
  SatelliteGroupsResponse,
  SatelliteEnrichment,
  ReentryResponse,
  HistoryResponse,
  LivePayload,
  SearchResponse,
  StatsBucketSize,
  StatsResponse,
  StreamMessage,
  AircraftSearchResponse,
  AirportSearchResponse,
  AreaSearchResponse,
  AlertsResponse,
  AlertSort,
  AoiCountry,
  SatelliteSearchResponse,
  VesselSearchResponse,
  VesselTrackResponse,
} from "./types";

const API = "/api/v1";
const POLL_MS = 30_000; // collector cadence — the documented fallback rate
const WS_BACKOFF_MIN = 1_000;
const WS_BACKOFF_MAX = 30_000;

export class ApiError extends Error {
  constructor(
    public status: number,
    detail: string,
  ) {
    super(detail);
  }
}

async function get<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const url = new URL(API + path, window.location.origin);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined) url.searchParams.set(k, String(v));
  }
  const k = apiKey();
  const res = await fetch(url, { cache: "no-store", headers: k ? { "x-atheer-key": k } : undefined });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

/* ======================================================================================
 * DASH-4b-4 — THE SHARED WRITE TOKEN.
 *
 * ⛔ IT IS NOT AUTHENTICATION AND THIS FILE MUST NOT IMPLY IT IS. A browser bundle cannot hold
 * a secret from the person operating it. What the token stops is everything that reaches the
 * API port WITHOUT opening the dashboard — a scanner, a stray service, another LAN device —
 * which matters because `server.py` runs with `--host 0.0.0.0` so several analysts on
 * different machines share one screen. The server refuses every write when it is unset
 * (503, fail closed).
 *
 * ⚠ A SHARED passphrase for the team, matching the decision that there is no per-analyst
 * identity. It identifies nobody; the record of who did what is `alert_triage.note`.
 *
 * ⛔ localStorage, WITH ITS FAILURE MODES ACCEPTED RATHER THAN HIDDEN: WebKit's ITP deletes
 * script-writable storage after seven days without interaction, any browser may evict it under
 * storage pressure, a private window loses it on close, and http/https or a hostname change
 * forks the store. Every one of those losses is SILENT. That is survivable here only because
 * losing it costs a re-entry, never data — and because the alternative (embedding it in the
 * build) would put the token in every copy of the bundle with no way to change it.
 * ⚠ Wrapped in try/catch: a browser configured to block site data THROWS on access rather
 * than returning null, and an unreadable token must degrade to "not set", never to a crash.
 * ====================================================================================== */
const TOKEN_KEY = "atheer-write-token";

/* Read-side API key — the mirror of the write token, for deployments that set
 * ATHEER_API_KEY on the server (opt-in; unset server = open reads, key ignored).
 * Same storage contract and the same caveats as the write token above. */
const API_KEY_KEY = "atheer-api-key";

export function apiKey(): string {
  try {
    return localStorage.getItem(API_KEY_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setApiKey(v: string): void {
  try {
    if (v.trim()) localStorage.setItem(API_KEY_KEY, v.trim());
    else localStorage.removeItem(API_KEY_KEY);
  } catch {
    /* blocked site data — the key will not persist; the next read failing with 401
     * is the signal, same degradation contract as setWriteToken. */
  }
}

export function writeToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setWriteToken(v: string): void {
  try {
    if (v.trim()) localStorage.setItem(TOKEN_KEY, v.trim());
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* blocked site data — the token simply will not persist, and the caller learns that from
     * the next write failing with 403 rather than from a silent success here. */
  }
}

/** ⛔ MUST MATCH `notify.identity.dedup_key`, the SAME key the bot ledger, `alert_triage` and
 *  `case_items` all use. A second definition would let them disagree about what "the same
 *  alert" is, invisibly.
 *  ⚠ Mirrored by hand because the rules live in Python; the family whose key includes a
 *  timestamp (`military_satellite`) includes it here too. */
export const caseKey = (a: Alert): string => {
  // ⛔ The SERVER key is authoritative (published on every alert since 2026-09-01) —
  // it is notify.identity.dedup_key itself, so it can never drift from the bot
  // ledger, alert_triage or case_items.
  if (a.dedup_key) return a.dedup_key;
  // Fallback mirror for alerts fetched by an older server. ⛔ MUST stay byte-identical
  // to notify.identity.dedup_key — pinned by caseKey.test.ts ↔ tests/test_case_key_parity.py
  // (shared vectors). The old mirror HAD drifted: maritime_single was missing `:summary`,
  // which merged a ship's rendezvous and zone_dwell into one triage identity while the
  // server kept them apart — a triage write that could never render back.
  const id = a.entity_ref.id ?? "";
  switch (a.signal_type) {
    case "military_satellite":
      return `${a.signal_type}:${id}:${a.ts ?? ""}`;          // the pass IS (norad, start)
    case "maritime_single":
      return `${a.signal_type}:${id}:${a.summary ?? ""}`;     // ship+pattern, NOT ship alone
    case "emergency_squawk":
    case "military_aircraft":
    case "maritime_composite":
    case "implausible_position":
      return `${a.signal_type}:${id}`;
    default:
      // Python's safe-but-noisy unknown-family fallback: can re-send, can never suppress.
      return `${a.signal_type}:${id}:${a.ts ?? ""}`;
  }
};

async function post<T>(path: string, body: unknown): Promise<T> {
  const tok = writeToken();
  const res = await fetch(new URL(API + path, window.location.origin), {
    method: "POST",
    headers: {
      "content-type": "application/json",
      // ⚠ Sent even when empty: the server answers 403 with a readable reason, which is a
      // better signal than the client silently declining to call.
      "x-atheer-write": tok,
      ...(apiKey() ? { "x-atheer-key": apiKey() } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const b = (await res.json()) as { detail?: string };
      if (b.detail) detail = b.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  live: () => get<LivePayload>("/live"),
  /** DASH-5 — the shared case file, newest first. */
  caseItems: () => get<CaseItem[]>("/alerts/case"),
  /** DASH-5 — keep one alert as evidence, or drop it.
   *  ⛔ THE SNAPSHOT TRAVELS WITH THE ADD. The server stores the claim as it was, because the
   *  feed is stateless and a key alone would resolve to nothing once the window rolled past. */
  setCase: (a: Alert, opts?: { remove?: boolean; note?: string }) =>
    post<{ dedup_key: string; removed: boolean }>("/alerts/case",
      opts?.remove
        ? { dedup_key: caseKey(a), remove: true }
        : {
            dedup_key: caseKey(a), label: a.entity_ref.label, domain: a.domain,
            signal_type: a.signal_type, severity: a.severity, headline: a.headline,
            why: a.why, caveat: a.caveat, countries: a.countries ?? [],
            alert_ts: a.ts, note: opts?.note ?? null,
          }),

  /** DASH-4b-4 — set the COLLECTIVE triage state of one alert. `state: null` REOPENS it by
   *  deleting the row: open is the absence of a row, so there is exactly one representation
   *  of it and it cannot drift from a second one.
   *  ⚠ `changeSig` is the signature the analyst was LOOKING AT, sent rather than re-derived —
   *  a server-side re-derivation could disagree with what was on screen, recording a verdict
   *  against a state nobody saw. */
  setTriage: (dedupKey: string, state: "reviewing" | "closed" | null,
              opts?: { note?: string; changeSig?: string }) =>
    post<{ dedup_key: string; state: string | null }>("/alerts/triage", {
      dedup_key: dedupKey, state, note: opts?.note ?? null, change_sig: opts?.changeSig ?? null,
    }),
  history: (
    icao24: string,
    opts?: { from?: number; to?: number; limit?: number; offset?: number; source?: string },
  ) => get<HistoryResponse>("/history", { icao24, ...opts }),
  stats: (opts?: { from?: number; to?: number; bucket?: StatsBucketSize }) =>
    get<StatsResponse>("/stats", { ...opts }),
  search: (opts: { q?: string; min_alt?: number; max_alt?: number; on_ground?: boolean; limit?: number }) =>
    get<SearchResponse>("/search", { ...opts }),
  military: () => get<MilitaryResponse>("/military"),
  adsb: () => get<AdsbResponse>("/adsb"),
  maritime: (source?: "aisstream" | "marinetraffic" | "all") =>
    get<MaritimeResponse>("/maritime", source ? { source } : undefined),
  /** DASH-4 — ONE vessel's LIVE contract by MMSI. The single-entity route vessels lacked.
   *  Throws ApiError(404) when the MMSI was never seen — an explicit outcome the caller is
   *  expected to render, NOT a generic failure (see EntityPayload). 422 = blank/missing key. */
  maritimeVessel: (mmsi: string) =>
    get<VesselLiveResponse>("/maritime/vessel", { mmsi }),
  maritimeGaps: (opts?: { classification?: string; min_hours?: number; limit?: number }) =>
    get<GapsResponse>("/maritime/gaps", { ...opts }),
  maritimeRendezvous: (opts?: { rank?: string; min_minutes?: number; limit?: number }) =>
    get<RendezvousResponse>("/maritime/rendezvous", { ...opts }),
  maritimeIdentityChanges: (opts?: { rank?: string; change_type?: string; limit?: number }) =>
    get<IdentityChangesResponse>("/maritime/identity-changes", { ...opts }),
  maritimeGnssAnomalies: (opts?: { gnss_type?: string; classification?: string; limit?: number }) =>
    get<GnssAnomaliesResponse>("/maritime/gnss-anomalies", { ...opts }),
  maritimeJammingZones: (opts?: { variant?: string; min_vessels?: number; limit?: number }) =>
    get<JammingZonesResponse>("/maritime/jamming-zones", { ...opts }),
  maritimeComposite: (opts?: { min_pattern_types?: number; waters?: string; limit?: number }) =>
    get<CompositeResponse>("/maritime/composite", { ...opts }),
  maritimeCrossref: (opts?: { min_confidence?: string; limit?: number }) =>
    get<CrossrefResponse>("/maritime/crossref", { ...opts }),
  maritimeDarkVessels: (opts?: { region?: string; months?: number; limit?: number }) =>
    get<DarkVesselsResponse>("/maritime/dark-vessels", { ...opts }),
  // D5 — the monitor's resting dashboard: the multi-domain alert feed + domain
  // stats, in one envelope (a single fetch fills the dashboard).
  //
  // DASH-1 added two OPTIONAL params; omitting both is the unchanged behaviour.
  //   country — restrict to alerts TOUCHING one AOI country. Accepts an ISO alpha-2
  //             code or a name, any case; 400 if it is not one of the seven.
  //             ⚠ It scopes `counts` and `domains[]` TOO, since DASH-3 — this comment
  //             said they "stay global", which was true under DASH-1 and is now false.
  //             Measured for SY: air 9 · sea 32 · space 57 against the global
  //             549 · 498 · 803. Read `countries_filtered` before showing any of them.
  //   sort    — "severity" (default, the honest threat order) or "importance" (the
  //             country card's display order). The response echoes `sorted_by`.
  alerts: (opts?: { window_hours?: number; country?: AoiCountry; sort?: AlertSort }) =>
    get<AlertsResponse>("/alerts", { ...opts }),
  vessel: (mmsi: string, opts?: { imo?: string; name?: string }) =>
    get<VesselEnrichment>(`/vessel/${encodeURIComponent(mmsi)}`, {
      imo: opts?.imo || undefined,
      name: opts?.name || undefined,
    }),
  // E1 entity search (vessel): DB-backed lookup + one vessel's full track.
  searchVessel: (q: string) => get<VesselSearchResponse>("/search/vessel", { q }),
  vesselTrack: (
    mmsi: string,
    opts?: { since?: string; until?: string; limit?: number; offset?: number },
  ) => get<VesselTrackResponse>(`/vessel/${encodeURIComponent(mmsi)}/track`, { ...opts }),
  // E2 entity search (aircraft): DB-backed lookup; the track reuses /history.
  searchAircraft: (q: string) => get<AircraftSearchResponse>("/search/aircraft", { q }),
  // E3 entity search (satellite): DB-backed lookup; the ground-track is computed
  // client-side (SGP4, lib/orbit) from the TLE carried in each summary.
  searchSatellite: (q: string) => get<SatelliteSearchResponse>("/search/satellite", { q }),
  // E4 entity search (airport → landings): resolve airport(s) from the gazetteer;
  // `icao` selects which airport's landing candidates to derive (else the top match).
  searchAirport: (q: string, opts?: { icao?: string; hours?: number }) =>
    get<AirportSearchResponse>("/search/airport", { q, icao: opts?.icao || undefined, hours: opts?.hours }),
  // D4 area-search (a MODE, not an entity): draw a box → multi-domain crossings.
  searchArea: (
    box: { lat1: number; lon1: number; lat2: number; lon2: number },
    opts?: { since?: string; until?: string; domains?: string },
  ) => get<AreaSearchResponse>("/search/area", { ...box, ...opts }),
  satellites: (group: string) => get<SatellitesResponse>("/satellites", { group }),
  satelliteGroups: () => get<SatelliteGroupsResponse>("/satellite-groups"),
  satellite: (norad: number) =>
    get<SatelliteEnrichment>(`/satellite/${encodeURIComponent(norad)}`),
  reentry: (region: "all" | "aoi" = "all") =>
    get<ReentryResponse>("/reentry", { region }),
  aircraft: (icao24: string, callsign?: string) =>
    get<AircraftEnrichment>(`/aircraft/${encodeURIComponent(icao24)}`, {
      callsign: callsign?.trim() || undefined,
    }),
  health: () => get<HealthResponse>("/health"),

  /* ------------- UI-3: the seven coverage-bearing contracts, reachable at last -------------
   * ⛔ Measured in DASH-BOARD-UI-pre: all seven shipped in the backend and NONE was referenced
   * here or in types.ts. Nothing is invented — each is the existing `get<T>` over the route the
   * backend already serves. ⛔ And every one of them returns its `coverage`, which is the reason
   * they are grouped: a caller must run the payload through `lib/series/prepare` before drawing,
   * because that is the only place the break-the-line rule is enforced. */

  /** DASH-BOARD-0 — what was actually observed, for every series the dashboard may draw.
   *  ⛔ This precedes every time series. `stream` narrows to one of the registry's streams. */
  coverage: (opts?: { stream?: string }) =>
    get<CoverageResponse>("/coverage", { ...opts }),

  /** ب — is the frequency of AIS outages rising or falling?
   *  ⛔ `split_by: "classification"`; never sum across keys — `outage-excluded` and
   *  `jamming-excluded` are OUR OWN downtime, not vessel behaviour. */
  maritimeSeriesAisGaps: (opts?: { bucket?: string; hours?: number }) =>
    get<MaritimeSeries>("/maritime/series/ais-gaps", { ...opts }),

  /** د — how many hulls carry more than one pattern, over time?
   *  ⛔ `multi_pattern` and `hulls_with_any_event` are numerator and denominator, not two
   *  categories to add. Its coverage is the UI-0 composite: five contributing detectors. */
  maritimeSeriesMultiPattern: (opts?: { bucket?: string; hours?: number }) =>
    get<MaritimeSeries>("/maritime/series/multi-pattern", { ...opts }),

  /** ج — where do rendezvous cluster geographically?
   *  ⛔ Read `density_refused` FIRST: the backend refuses a density surface at this N and says so
   *  in the payload. Points are returned as they are; every one is a candidate lead. */
  maritimeRendezvousPoints: (opts?: { hours?: number; limit?: number }) =>
    get<RendezvousPointsResponse>("/maritime/series/rendezvous-points", { ...opts }),

  /** هـ — the registry-flag distribution of the multi-pattern hulls.
   *  ⛔ Read `denominator` with every `pct`, and `attribution_field` before naming the source. */
  maritimeFlagsMultiPattern: () =>
    get<FlagDistributionResponse>("/maritime/flags/multi-pattern"),

  /** أ — air country × time, ON DEMAND (no stored aggregate). Unit: distinct AIRFRAMES.
   *  ⛔ The key is (country, source); never sum across `source`. ⚠ `hours` drives the cost —
   *  the response carries `cost_note` and `window_hours` for exactly that reason. */
  airSeriesCountry: (opts?: { bucket?: string; hours?: number; country?: string }) =>
    get<AirCountrySeries>("/air/series/country", { ...opts }),

  /** أ (long) — the daily air summary. ⛔ ITS UNIT IS HULL-DAYS, NOT AIRFRAMES: every multi-day
   *  sum counts an airframe once per day it appeared. Never place these beside
   *  `airSeriesCountry`'s distinct counts without the difference being visible. */
  airSeriesCountryDaily: (opts?: { days?: number; country?: string; source?: string }) =>
    get<AirCountryDaily>("/air/series/country-daily", { ...opts }),
};

/** Honest transport state, surfaced in the UI. */
export type FeedStatus =
  | { transport: "ws"; live: true }
  | { transport: "polling"; live: true }
  | { transport: "polling"; live: false; error: string }
  | { transport: "connecting"; live: false };

export type FeedListener = (payload: LivePayload, status: FeedStatus) => void;
export type StatusListener = (status: FeedStatus) => void;

/**
 * WS-first live feed with REST polling fallback. start() → stop().
 * Pausable (a paused feed keeps the socket but drops updates on the floor? No —
 * see pause(): we keep receiving and buffer only the latest, so returning to
 * live is instant and the server's 2-slot queue stays drained).
 */
export class LiveFeed {
  private ws: WebSocket | null = null;
  private pollTimer: number | null = null;
  private backoff = WS_BACKOFF_MIN;
  private stopped = true;
  private paused = false;
  private lastPayload: LivePayload | null = null;
  private status: FeedStatus = { transport: "connecting", live: false };

  constructor(
    private onData: FeedListener,
    private onStatus: StatusListener,
  ) {}

  start(): void {
    this.stopped = false;
    this.connectWs();
    this.startPolling(); // covers the window until the first WS message
  }

  stop(): void {
    this.stopped = true;
    this.stopPolling();
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
  }

  /** Paused: keep the feed hot but don't propagate updates. */
  pause(): void {
    this.paused = true;
  }

  resume(): void {
    this.paused = false;
    if (this.lastPayload) this.emit(this.lastPayload);
  }

  private emit(payload: LivePayload): void {
    this.lastPayload = payload;
    if (!this.paused) this.onData(payload, this.status);
  }

  private setStatus(s: FeedStatus): void {
    this.status = s;
    this.onStatus(s);
  }

  private connectWs(): void {
    if (this.stopped) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    let ws: WebSocket;
    try {
      // Browsers cannot set headers on a WebSocket, so the key rides as a query
      // param — the server checks it before accepting the handshake.
      const k = apiKey();
      ws = new WebSocket(
        `${proto}://${window.location.host}${API}/stream${k ? `?api_key=${encodeURIComponent(k)}` : ""}`,
      );
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.backoff = WS_BACKOFF_MIN;
      this.stopPolling();
      this.setStatus({ transport: "ws", live: true });
    };
    ws.onmessage = (ev: MessageEvent<string>) => {
      let msg: StreamMessage;
      try {
        msg = JSON.parse(ev.data) as StreamMessage;
      } catch {
        return;
      }
      if (msg.type === "snapshot" && msg.data) this.emit(msg.data);
    };
    ws.onclose = () => {
      this.ws = null;
      if (this.stopped) return;
      this.startPolling(); // degrade honestly, keep updating
      this.scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  }

  private scheduleReconnect(): void {
    const wait = this.backoff;
    this.backoff = Math.min(this.backoff * 2, WS_BACKOFF_MAX);
    window.setTimeout(() => this.connectWs(), wait);
  }

  private startPolling(): void {
    if (this.pollTimer !== null || this.stopped) return;
    this.setStatus({ transport: "polling", live: true });
    const tick = async () => {
      try {
        const payload = await api.live();
        if (this.pollTimer !== null) {
          this.setStatus({ transport: "polling", live: true });
          this.emit(payload);
        }
      } catch (e) {
        this.setStatus({
          transport: "polling",
          live: false,
          error: e instanceof Error ? e.message : "feed error",
        });
      }
    };
    void tick();
    this.pollTimer = window.setInterval(tick, POLL_MS);
  }

  private stopPolling(): void {
    if (this.pollTimer !== null) {
      window.clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }
}
