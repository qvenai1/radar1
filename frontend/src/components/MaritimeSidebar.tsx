/**
 * Sidebar for the maritime tab — the sea-going parallel of Sidebar.tsx.
 * Vessel KPIs + honest feed status + composed children (search + vessel list
 * + vessel strip). Kept separate from the aircraft sidebar because vessels
 * are a different domain (MMSI, knots, ship types), not aircraft.
 */

import type { ReactNode } from "react";
import type { SourceVintage, Vessel } from "../api/types";
import { ageSeconds as ageText, isoAge, num } from "../lib/format";
import { T } from "../lib/strings";

interface Props {
  vessels: Vessel[];
  available: boolean;
  ageSeconds: number | null;
  /** UI-FRESH: the authoritative per-source freshness (MaritimeResponse.sources) */
  sources: SourceVintage[];
  /** worst-case payload vintage — the redefined data_vintage. NOT "the" vintage. */
  dataVintage: string | null;
  /** the MarineTraffic opt-in overlay (off by default, persisted by App) */
  mtOn: boolean;
  onMt: () => void;
  children: ReactNode;
}

export default function MaritimeSidebar({
  vessels, available, ageSeconds, sources, dataVintage, mtOn, onMt, children,
}: Props) {
  /* UI-2 step 3 — PROVENANCE ON THE HEADLINE NUMBER.
   *
   * `/maritime?source=all` merges AISStream + MarineTraffic, and these tiles used to be
   * computed over the blend: «3,038 السفن» was 2,616 AIS + 422 MT, with the split only
   * on the line below and no mark on the number itself. This project's foundation is
   * per-fact provenance, and an analyst reading a headline figure must be reading ONE
   * source.
   *
   * Fixed on the DISPLAY side — what is fetched is unchanged, so the map and the vessel
   * list keep the merged feed with their existing per-row MT badges and amber glyphs.
   * Every tile here now counts AIS only (the primary source); the MarineTraffic
   * contribution is a separate, amber-marked figure, never folded into a tile. */
  const ais = vessels.filter((v) => v.source !== "marinetraffic");
  const mtCount = vessels.length - ais.length;
  const moving = ais.filter((v) => (v.speed ?? 0) > 0.5).length;
  const stationary = ais.length - moving;
  const types = new Set(ais.map((v) => v.ship_type).filter(Boolean)).size;

  const feed = !available
    ? { cls: "", text: T.vessel.feedOff }
    : { cls: "ws", text: T.vessel.feedLive };

  /* UI-FRESH — read sources[], never data_vintage, as "the" freshness.
   *
   * The scalar previously reported the FRESHEST ts across tables, so a payload that was
   * ~92% four days old declared an ~11 s vintage. The backend redefined it to the OLDEST
   * contributing source (worst case) and added sources[] as the authoritative structure.
   * The strip below shows each source its OWN age and stale flag, so a stale source reads
   * as stale instead of being averaged away by a single number. */
  const mtSource = sources.find((s) => s.source === "marinetraffic") ?? null;
  const contributing = sources.filter((s) => s.rows > 0);

  return (
    <aside className="side">
      <div className="brand">
        <h1>
          <img src="./logo1.png" alt="" aria-hidden="true" className="brand-mark" />
          <span>
            Atheer <span className="tail">RADAR</span>
          </span>
        </h1>
        <div className="sub">{T.vessel.brandSub}</div>
      </div>

      {/* all four tiles count AIS only — the title says so, and the MT contribution
          rides the primary tile as its own marked figure */}
      <div
        className="stat-grid"
        role="group"
        aria-label={T.vessel.kpiAria}
        title={T.vessel.kpiAisOnly}
      >
        <div className="metric accent">
          <div className="n">{num(ais.length)}</div>
          <div className="l">{T.vessel.kpiTotal}</div>
          <div className="stat-src">
            <span className="src-ais">{T.vessel.kpiSrcAis}</span>
            {mtCount > 0 && (
              <span className="mt-mark" title={T.vessel.kpiAisOnly}>
                {T.vessel.kpiSrcMt(num(mtCount))}
              </span>
            )}
          </div>
        </div>
        <div className="metric">
          <div className="n">{num(moving)}</div>
          <div className="l">{T.vessel.kpiMoving}</div>
        </div>
        <div className="metric">
          <div className="n">{num(stationary)}</div>
          <div className="l">{T.vessel.kpiStationary}</div>
        </div>
        <div className="metric">
          <div className="n">{num(types)}</div>
          <div className="l">{T.vessel.kpiTypes}</div>
        </div>
      </div>

      <div>
        {/* PER-SOURCE freshness. This REPLACES the old single-source feed row rather than
            sitting beside it: that row stated one liveness verdict and one age for the
            whole payload, which is exactly the thing MT-FRESH found dishonest. Each row
            below carries its own age and its own live/stale verdict, so a stale source
            cannot be averaged away — strictly more information, in less height. */}
        <div className="section-label">{T.vessel.srcSection}</div>
        {!available && (
          <div className="feed-row">
            <span className={`dot ${feed.cls}`} aria-hidden="true" />
            <span role="status">{feed.text}</span>
          </div>
        )}
        {/* An empty tab that does not say why reads as a broken app. When the primary feed
            is down AND the second source is switched off, the map is blank for a reason the
            operator has no way to see — so name the reason and the thing to try.
            ⚠ It says "try", never "MarineTraffic has data": with the layer off the client
            only requested aisstream, so it genuinely does not know. */}
        {!available && !mtOn && (
          <div className="filter-note" role="status">{T.vessel.mtPrimaryDown}</div>
        )}
        <div className="src-strip" role="group" aria-label={T.vessel.srcAria}>
          {contributing.length === 0 ? (
            <div className="filter-note">{T.vessel.srcNone}</div>
          ) : (
            contributing.map((s) => (
              <div
                key={s.source}
                className={`src-row${s.stale ? " stale" : ""}${
                  s.source === "marinetraffic" ? " mt" : ""
                }`}
                title={s.last_fix ?? undefined}
              >
                <span className={`dot ${s.stale ? "error" : "ws"}`} aria-hidden="true" />
                <span className="src-name">{T.vessel.srcNames[s.source] ?? s.source}</span>
                <span className="src-rows">{T.vessel.srcRows(num(s.rows))}</span>
                <span className="src-verdict">{s.stale ? T.vessel.srcStale : T.vessel.srcLive}</span>
                <span className="src-age">
                  {T.vessel.srcAge(ageText(s.age_seconds))}
                  {/* the AIS snapshot's own file age, where the feed row used to state it */}
                  {!s.stale && s.source === "aisstream" && available && ageSeconds != null
                    ? ` · ${Math.round(ageSeconds)}s`
                    : ""}
                </span>
              </div>
            ))
          )}
          {/* The redefined data_vintage, labelled as the worst case it now is. Shown ONLY
              for a blend — with one contributing source the worst case IS that source's
              age, so the line would just repeat the row above it. */}
          {dataVintage && contributing.length > 1 && (
            <div className="src-worst" title={T.vessel.srcWorstCaseTitle}>
              {T.vessel.srcWorstCase(isoAge(dataVintage))}
            </div>
          )}
        </div>

        {/* The MarineTraffic OPT-IN overlay — the same layer-btn affordance GNSS and
            dark-vessels use: the button carries the state, the explanation lives in its
            title. (An always-visible hint here cost ~78px and pushed VesselStrip below the
            fold, undoing UI-2's target.) Off by default: the store is static/stale and its
            rows carry no MMSI, so they cannot be investigated.
            The age comes from sources[], which the backend populates only for REQUESTED
            sources — so it appears on the label once the layer is on. */}
        <div className="analysis-layer mt-layer">
          <button
            className={`layer-btn mt${mtOn ? " on" : ""}`}
            aria-pressed={mtOn}
            onClick={onMt}
            title={
              mtOn
                ? T.vessel.mtLayerOnHint(ageText(mtSource?.age_seconds ?? null))
                : T.vessel.mtLayerOffHint
            }
          >
            <span className="layer-dot" aria-hidden="true" />
            {T.vessel.mtLayer}
            {mtOn && mtSource ? ` · ${T.vessel.srcAge(ageText(mtSource.age_seconds))}` : ""}
            {mtOn && mtSource?.stale ? ` · ${T.vessel.srcStale}` : ""}
          </button>
        </div>
      </div>

      {children}
    </aside>
  );
}
