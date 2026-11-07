/**
 * EntityPayload (DASH-2f) — the home's LEFT panel: one entity's live payload, on demand.
 *
 * 2d″ built this panel as a titled shell that admitted it was not wired. This wires it.
 *
 * ⛔ ONE ENTITY, ONE FETCH. The view gates are deliberately NOT widened. Measured in
 * DASH-2-pre-ب: vessels are emptied by `view !== "maritime"` and satellites by
 * `view !== "satellites"`, and widening those pulls ~2,800 vessels to show one. So this
 * panel owns its own single-entity fetch keyed by `live_key`, and it cancels in flight so
 * a fast second click can never paint the previous entity's payload.
 *
 * ⛔ FOUR HONESTY STATES, each rendered as itself and never as an empty panel:
 *  1. `live_key` is null → "no payload key for this entity". Measured 0/213 today, but the
 *     contract permits null (nothing heard from any of a hull's MMSIs) so the branch is
 *     real, not defensive decoration.
 *  2. the key resolves to nothing live → "not transmitting now", with whatever IS known
 *     carried beside it and labelled. DASH-1 measured two of three entities in the live
 *     snapshot and the third silent; that is a normal state, not an error.
 *  3. a display needs something the backend does not offer → say so. It is never computed
 *     here. The governing rule from 2c′/2e: "not observed" ≠ "does not exist".
 *  4. (DASH-2f′) the key was NEVER heard of → a 404, rendered as that exact statement.
 *     Distinct from 2: "never observed" is not "not observed right now", and neither is
 *     a failed request. A generic error here would blame the transport for an answer the
 *     backend gave precisely.
 *
 * ✅ THE VESSEL GAP THIS FILE USED TO CARRY IS CLOSED (DASH-2f′ consuming DASH-4).
 * 2f shipped with a measured asymmetry: aircraft and satellites had single-entity routes
 * to their LIVE contract, vessels did not — the live `Vessel` existed only inside
 * `/maritime`'s full list — so the vessel case fetched `/search/vessel` (the SUMMARY),
 * hand-built a `Vessel` from it, and NAMED four fields as unreachable. All of that is
 * gone. `/maritime/vessel?mmsi=` returns the same `Vessel` `/maritime` serves
 * (backend-verified byte-identical), so:
 *   · the hand-built object is deleted — with its synthesised `source:"vessel_states"` /
 *     `data_origin:"search/vessel"`, values no collector ever emitted;
 *   · the hard-coded absence list is deleted — `not_reported` is computed PER VESSEL, so a
 *     hull that broadcasts its draught stops reading as missing it;
 *   · `state` distinguishes a live payload from a `last_known` one, and the last-known
 *     label is printed ABOVE the payload, because a real position from hours ago is only
 *     misleading when it is read as current.
 *
 * ⚠ 19 fields, not 20 — the backend model drops `length_m`/`beam_m`. The four fields 2f
 * named (heading · destination · nav_status · draught) are all inside the 19 and all
 * render: three as `FieldGrid` cells, `destination` in the strip head.
 *
 * Reuses the three existing strips (UI-3 unified their field grid); no fourth view.
 */

import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type {
  AircraftEnrichment, SatelliteElement, SatelliteEnrichment, StateVector, Vessel,
  VesselLiveResponse,
} from "../api/types";
import { ageSeconds, utcShort } from "../lib/format";
import { T } from "../lib/strings";
import FlightStrip from "./FlightStrip";
import SatelliteStrip from "./SatelliteStrip";
import VesselStrip from "./VesselStrip";

export interface EntitySel {
  kind: "vessel" | "aircraft" | "satellite";
  /** the key that addresses the LIVE payload (MMSI | icao24 | NORAD) — may be null */
  liveKey: string | null;
  /** the hull's other MMSIs, most-recent first — HISTORY, never a silent substitute */
  altKeys: string[];
  label: string | null;
  /**
   * DASH-2g″ — this entity's identity in the MAP LAYER, when a map click supplied it.
   *
   * ⚠ NOT a duplicate of `liveKey`, and measured so. The vessel layer is keyed by
   * `vesselKey` — MMSI for AIS, `ship_id` for MarineTraffic — while the payload is addressed
   * by MMSI only. On the live blend those two spaces mostly DISAGREE: measured 490 AIS rows
   * with an MMSI and no ship_id against **2,616 MarineTraffic rows with `mmsi: null`**, so
   * for most of the layer the map key is the only handle that exists and the payload key is
   * legitimately absent (honesty state 1).
   *
   * Undefined when the selection came from the country card, which knows the payload key
   * and not the layer; App resolves that direction itself. It addresses the RING only —
   * ⛔ never the fetch, which uses `liveKey` or renders its absence.
   */
  mapKey?: string | null;
}

/** what the fetch produced, plus what it could not produce */
interface Loaded {
  vessel?: Vessel | null;
  /** DASH-2f′: the single-vessel envelope — `state`, the two ages, and the PER-VESSEL
   *  `not_reported`. Kept whole rather than unpacked, because its fields only mean
   *  anything together: a position without its `state` and age is exactly the "old number
   *  read as current" this panel exists to prevent. */
  live?: VesselLiveResponse | null;
  state?: StateVector | null;
  element?: SatelliteElement | null;
  satEnrich?: SatelliteEnrichment | null;
  /** honesty state 3: a named list of what this kind cannot supply per-entity */
  unavailable?: string[];
  /** honesty state 2: the key resolved, but nothing is live right now */
  silent: boolean;
  /** DASH-2f′ honesty state 4: the store has never heard this key at all (404).
   *  Distinct from `silent` — "never observed" is not "not observed right now". */
  unknownKey?: boolean;
}

interface Props {
  sel: EntitySel | null;
  onClose: () => void;
}

export default function EntityPayload({ sel, onClose }: Props) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);
  /* ---------- PAYLOAD-ENRICH ----------
   * A SECOND, LATER fetch with its OWN sequence guard. Two reasons it is not folded into the
   * base one:
   *  · the base payload must paint immediately. Enrichment is slower and fails more often (it
   *    reaches third-party providers), so awaiting it would hold the whole panel hostage to
   *    the least reliable part of it.
   *  · ⛔ its own guard, not the base one. The 2g″ race is the precedent: request B at
   *    +17,900ms and reply A at +19,004ms must not paint. A shared counter would let a slow
   *    enrichment for the PREVIOUS entity land on the NEW entity's card — the exact defect,
   *    one layer deeper.
   * `enrichState` is a 3-state, never a bare null: "loading" and "unavailable" are different
   * facts and a silent blank would conflate them. */
  const enrichSeq = useRef(0);
  const [airEnrich, setAirEnrich] = useState<AircraftEnrichment | null>(null);
  const [satEnrich, setSatEnrich] = useState<SatelliteEnrichment | null>(null);
  const [enrichState, setEnrichState] = useState<"idle" | "loading" | "done" | "unavailable">("idle");
  const E = T.entityPayload;

  useEffect(() => {
    setLoaded(null);
    if (!sel || !sel.liveKey) return;
    const key = sel.liveKey;
    const mine = ++seq.current;
    setLoading(true);
    const done = (l: Loaded) => { if (mine === seq.current) setLoaded(l); };
    const fail = () => { if (mine === seq.current) setLoaded({ silent: true }); };
    const stop = () => { if (mine === seq.current) setLoading(false); };

    if (sel.kind === "aircraft") {
      // /search over the live snapshot returns the LIVE contract (StateVector[])
      api.search({ q: key, limit: 5 })
        .then((r) => {
          const s = r.states.find((x) => x.icao24 === key) ?? null;
          done({ state: s, silent: !s });
        })
        .catch(fail).finally(stop);
    } else if (sel.kind === "satellite") {
      api.searchSatellite(key)
        .then((r) => {
          const m = r.matches.find((x) => String(x.norad_id) === key) ?? null;
          const el: SatelliteElement | null =
            m && m.tle1 && m.tle2
              ? { norad_id: m.norad_id, name: m.object_name ?? key, tle1: m.tle1, tle2: m.tle2, epoch: m.tle_epoch }
              : null;
          done({
            element: el,
            silent: !el,
            // ⛔ honesty 3: the ground track is an SGP4 computation, not a served contract.
            // It is not computed here to fill the panel.
            unavailable: el ? [E.unavailTrack] : undefined,
          });
        })
        .catch(fail).finally(stop);
    } else {
      // DASH-2f′: the gap 2f had to work around is closed. /maritime/vessel returns the
      // SAME `Vessel` contract /maritime serves (backend-verified byte-identical), so the
      // hand-built summary→Vessel object is gone — with it the synthesised provenance
      // (source:"vessel_states", data_origin:"search/vessel"), values no collector ever
      // emitted and which a contract field must never carry.
      api.maritimeVessel(key)
        .then((r) => { done({ vessel: r.vessel, live: r, silent: false }); })
        .catch((e) => {
          if (mine !== seq.current) return;
          // ⛔ 404 is an OUTCOME with a name, not a failed fetch: this MMSI has never been
          // heard. Rendering it as a generic error would say "something went wrong" about a
          // question the backend answered precisely.
          if (e instanceof ApiError && e.status === 404) setLoaded({ silent: false, unknownKey: true });
          else fail();
        })
        .finally(stop);
    }
  }, [sel, E.unavailTrack]);

  /* the enrichment pass — after the base payload, never blocking it */
  useEffect(() => {
    setAirEnrich(null); setSatEnrich(null); setEnrichState("idle");
    if (!sel || !sel.liveKey) return;
    /* ⛔ VESSELS ARE NOT CALLED, and that is measured, not an oversight. `/vessel/{mmsi}`
     * serves HTTP 200 but returns only the mmsi it was given — 1 of 3 fields, on three real
     * MMSIs from the live feed. There is no photo and no licence to render, so issuing a
     * request per selection would cost a round trip to learn nothing. `photo={null}` below now
     * has a reason behind it instead of a stub. Wire it the day the provider is populated. */
    if (sel.kind === "vessel") return;
    const key = sel.liveKey;
    const mine = ++enrichSeq.current;
    setEnrichState("loading");
    const settle = (ok: boolean) => { if (mine === enrichSeq.current) setEnrichState(ok ? "done" : "unavailable"); };
    if (sel.kind === "aircraft") {
      api.aircraft(key)
        .then((r) => {
          if (mine !== enrichSeq.current) return;
          setAirEnrich(r);
          // ⚠ `photo` is the only field populated today (measured: registration/type/operator/
          // route all null on three real icao24s), so "done" means the photo arrived, not that
          // the aircraft is described.
          settle(Boolean(r && (r.photo || r.registration || r.aircraft_type || r.operator)));
        })
        .catch(() => settle(false));
    } else {
      api.satellite(Number(key))
        .then((r) => {
          if (mine !== enrichSeq.current) return;
          setSatEnrich(r);
          settle(Boolean(r && r.available));
        })
        .catch(() => settle(false));
    }
  }, [sel]);

  /* ---- no entity chosen: the panel says so instead of sitting blank ---- */
  if (!sel) {
    return (
      <aside className="home-panel entity" aria-label={T.home.entityAria}>
        <div className="section-label">{T.home.entityTitle}</div>
        <div className="home-slot-note">{E.noSelection}</div>
      </aside>
    );
  }

  /* ⚠ DASH-2g″ — THE HEAD NAMES THE ENTITY, in all four states.
   *
   * 2f could leave it out because the payload states each render a strip that carries the
   * name, and the three empty states were reached ~never from a card. The map changed that
   * by measurement: 2,616 of the 3,106 hulls on the maritime layer are MarineTraffic rows
   * with `mmsi: null`, so clicking one lands in honesty state 1 — and a panel that said only
   * «سفينة · لا مفتاح حمولة» would not tell the analyst WHICH ship they just clicked. The
   * label is the one thing every source knows, so it is stated before anything is fetched. */
  const head = (
    <div className="section-label ep-head">
      {T.home.entityTitle}
      <span className="dim"> · {E.kinds[sel.kind] ?? sel.kind}</span>
      {sel.label && <span className="ep-label">{sel.label}</span>}
      <button className="dash-cap-btn" onClick={onClose}>{E.close}</button>
    </div>
  );

  /* ---- honesty state 1: there is no key to open ---- */
  if (!sel.liveKey) {
    return (
      <aside className="home-panel entity" aria-label={T.home.entityAria}>
        {head}
        <div className="filter-note">{E.noLiveKey}</div>
        <div className="home-slot-note">{E.noLiveKeyWhy}</div>
        <AltKeys keys={sel.altKeys} />
      </aside>
    );
  }

  return (
    <aside className="home-panel entity" aria-label={T.home.entityAria}>
      {head}
      {loading && !loaded ? (
        <div className="filter-note">{E.loading}</div>
      ) : !loaded ? (
        <div className="filter-note">{E.loading}</div>
      ) : loaded.unknownKey ? (
        /* ---- DASH-2f′ honesty state 4: a 404, rendered as the precise answer it is ---- */
        <>
          <div className="filter-note">{E.unknownKey}</div>
          <div className="home-slot-note">{E.unknownKeyWhy(sel.liveKey)}</div>
        </>
      ) : loaded.silent ? (
        /* ---- honesty state 2: the key is valid, nothing is live right now ---- */
        <>
          <div className="filter-note">{E.notTransmitting}</div>
          <div className="home-slot-note">{E.notTransmittingWhy(sel.liveKey)}</div>
        </>
      ) : (
        <>
          {/* ⛔ DASH-2f′: the LABEL COMES FIRST, above the payload. A last-known position is
              real data from a real fix — it is only misleading when it is read as current,
              so the claim is stated before the numbers are shown, never as a footnote. */}
          {/* ⛔ PAYLOAD-PROV: the LIVE claim gets its date too. `state: "live"` asserts the fix
              is inside the 180 s window — an assertion whose only evidence is the snapshot's
              own age, which arrived on the envelope and was rendered nowhere (0 references,
              measured in PAYLOAD-AUDIT). Stating "live" without it is the same omission the
              last-known branch below was careful to avoid. */}
          {loaded.live?.state === "live" && loaded.live.snapshot_age_seconds != null && (
            <div className="home-slot-note">
              {E.snapshotAge(ageSeconds(loaded.live.snapshot_age_seconds))}
            </div>
          )}
          {loaded.live?.state === "last_known" && (
            <>
              <div className="filter-note">{E.stateLastKnown}</div>
              <div className="home-slot-note">
                {E.stateLastKnownWhy(ageSeconds(loaded.live.age_seconds), loaded.live.n_fixes)}
              </div>
              {loaded.live.identity_as_of && (
                <div className="home-slot-note">
                  {E.identityAsOf(utcShort(loaded.live.identity_as_of))}
                </div>
              )}
            </>
          )}
          {sel.kind === "vessel" && loaded.vessel && (
            <VesselStrip vessel={loaded.vessel} photo={null} license={null}
                         photoLoading={false} onClear={onClose} />
          )}
          {/* ⛔ DASH-2f′: per-vessel absence, computed by the backend, replacing 2f's
              hand-written four-field list. A hull that broadcasts its draught no longer
              reads as missing it; one that withholds it is still marked. ship_id/flag are
              excluded upstream as structural nulls and must not be added back here. */}
          {loaded.live?.not_reported.length ? (
            <div className="ep-unavail home-slot-note">
              <b>{E.notReportedTitle}</b>{" "}
              <span>
                {loaded.live.not_reported
                  .map((f) => E.notReportedNames[f] ?? f)
                  .join(" · ")}
              </span>
              <div>{E.notReportedWhy}</div>
            </div>
          ) : null}
          {sel.kind === "aircraft" && loaded.state && (
            <FlightStrip state={loaded.state} enrichment={airEnrich}
                         enrichLoading={enrichState === "loading"} onClear={onClose} />
          )}
          {sel.kind === "satellite" && loaded.element && (
            <SatelliteStrip element={loaded.element} enrichment={satEnrich} onClear={onClose} />
          )}
          {/* ⛔ PAYLOAD-ENRICH: the enrichment's own state, said out loud. "loading" and
              "unavailable" are different facts and a blank would conflate them — the same rule
              the panel already applies to `live` vs `last_known`. Vessels are `idle` (never
              requested, measured empty), so they show neither. */}
          {enrichState === "loading" && (
            <div className="home-slot-note">{E.enrichLoading}</div>
          )}
          {enrichState === "unavailable" && (
            <div className="home-slot-note">{E.enrichUnavailable}</div>
          )}
          {/* ---- honesty state 3: what this kind cannot supply per-entity, named ---- */}
          {loaded.unavailable?.length ? (
            <div className="ep-unavail home-slot-note">
              <b>{E.unavailTitle}</b>
              {loaded.unavailable.map((u) => <div key={u}>{u}</div>)}
            </div>
          ) : null}
        </>
      )}
      <AltKeys keys={sel.altKeys} />
    </aside>
  );
}

/** the hull's earlier identities — HISTORY, labelled, never a silent stand-in */
function AltKeys({ keys }: { keys: string[] }) {
  if (!keys.length) return null;
  const E = T.entityPayload;
  return (
    <div className="ep-alt home-slot-note">
      <b>{E.altTitle}</b>{" "}
      <span className="mono" dir="ltr">{keys.join(" · ")}</span>
      <div>{E.altWhy}</div>
    </div>
  );
}
