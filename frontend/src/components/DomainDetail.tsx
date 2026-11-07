/**
 * DomainDetail (DASH-2g′ commit 2) — the three domains' detail, in its new home inside لوحة.
 *
 * WHY IT EXISTS. The owner moved the domain detail off the map tabs: those now carry the map
 * as the hero with the country card and the entity payload floating over it, and everything
 * that used to fill their right-hand sidebars lives here instead. The three sidebar shells
 * (`Sidebar` / `MaritimeSidebar` / `SatelliteSidebar`) are gone with the surfaces that mounted
 * them — ⛔ nothing was left orphaned, which is the UI-3 / 2e lesson applied rather than quoted.
 *
 * ⚠ THIS IS A REBUILD, NOT A COMPONENT MOVE. No `<Sidebar>` was ever mounted on لوحة, so the
 * four inventory tiles could not simply be relocated: what they compute was read out of
 * `Sidebar.tsx:29-33` and rebuilt here against the same input. They are derived from
 * `displayStates` — sourced from the UNCONDITIONAL `LiveFeed`, which runs for every view — so
 * they are correct on this surface for the same reason they were correct on the old one.
 *
 * ⛔ THE TILES COUNT AIRCRAFT, AND SAY SO, ON EVERY DOMAIN. They do not change when the picker
 * changes, because their population never changes. An unlabelled "٤٢" beside a vessel list
 * would read as forty-two vessels — the §6.11 warning-3 failure exactly. The population is
 * printed under them rather than left to be inferred from which tab you happen to be on.
 *
 * ⛔ THREE LISTS, NEVER ONE TABLE. The picker switches between `ContactList`, `VesselList` and
 * `SatelliteList` — each already routed through the shared `DataTable` (UI-3 extracted it for
 * exactly this) and each keeping its OWN columns. Merging them would build a table whose rows
 * do not share a population or a schema: the /adsb-versus-opensky trap, one column of numbers
 * that cannot be added up. The picker changes which list you read, not what a row means.
 */

import type { ReactNode } from "react";
import type { FeedStatus } from "../api/client";
import type { LivePayload, StateVector } from "../api/types";
import { M_TO_FT, ft, num, plausibleBaro, utcClock } from "../lib/format";
import { T } from "../lib/strings";

export type DetailDomain = "air" | "sea" | "space";

const DOMAINS: readonly DetailDomain[] = ["air", "sea", "space"];

interface Props {
  domain: DetailDomain;
  onDomain: (d: DetailDomain) => void;
  /** the live aircraft feed — the tiles' input, and the feed-status line */
  payload: LivePayload | null;
  displayStates: StateVector[];
  status: FeedStatus;
  /** each domain's own content, composed by App from the state it already owns */
  air: ReactNode;
  sea: ReactNode;
  space: ReactNode;
}

function statusText(status: FeedStatus): { cls: string; text: string } {
  if (status.transport === "ws") return { cls: "ws", text: T.feed.ws };
  if (status.transport === "polling" && status.live) return { cls: "polling", text: T.feed.polling };
  if (status.transport === "polling") return { cls: "error", text: T.feed.error };
  return { cls: "", text: T.feed.connecting };
}

export default function DomainDetail({
  domain, onDomain, payload, displayStates, status, air, sea, space,
}: Props) {
  const D = T.dashboard;
  // Rebuilt from Sidebar.tsx:29-33, same inputs, same exclusions: a bogus barometric
  // reading is kept out of the ceiling KPI rather than allowed to set it.
  const airborne = displayStates.filter((s) => !s.on_ground).length;
  const ground = displayStates.length - airborne;
  let maxAlt: number | null = null;
  for (const s of displayStates) {
    const alt = plausibleBaro(s.baro_altitude);
    if (alt != null && (maxAlt == null || alt > maxAlt)) maxAlt = alt;
  }
  const st = statusText(status);

  return (
    <div className="domain-detail">
      {/* the domain picker — the existing `.seg` grammar, same as the workspace's own
          mode toggle, so this reads as a sibling control rather than a new idiom */}
      <div className="seg segment" role="group" aria-label={D.domainAria}>
        {DOMAINS.map((d) => (
          <button key={d} aria-pressed={domain === d} onClick={() => onDomain(d)}>
            {d === "air" ? D.domainAir : d === "sea" ? D.domainSea : D.domainSpace}
          </button>
        ))}
      </div>

      {/* the four inventory tiles + the feed line, rebuilt here. Rendered for every domain
          because their source is the ungated aircraft feed — and labelled for the same reason. */}
      <div className="stat-grid" role="group" aria-label={T.stats.aria}>
        <div className="metric accent">
          <div className="n">{num(displayStates.length)}</div>
          <div className="l">{T.kpi.contacts}</div>
        </div>
        <div className="metric">
          <div className="n">{num(airborne)}</div>
          <div className="l">{T.kpi.airborne}</div>
        </div>
        <div className="metric">
          <div className="n">{num(ground)}</div>
          <div className="l">{T.kpi.ground}</div>
        </div>
        <div className="metric">
          <div className="n">{maxAlt == null ? "—" : num(Math.round(maxAlt * M_TO_FT))}</div>
          <div className="l">{T.kpi.maxAlt}</div>
        </div>
      </div>
      <div className="filter-note">{D.tilesPop}</div>

      <div>
        <div className="section-label">{T.feed.section}</div>
        <div className="feed-row">
          <span className={`dot ${st.cls}`} aria-hidden="true" />
          <span role="status">{st.text}</span>
        </div>
        <div className="feed-meta">
          {T.feed.snapshot} <b>{payload ? utcClock(payload.snapshot_time) : "—"}</b>
          {" · "}{T.feed.source}{" "}
          <b>{payload ? (payload.source === "db" ? T.feed.sourceDb : T.feed.sourceArchive) : "—"}</b>
          <br />
          {T.feed.ceiling} <b>{ft(maxAlt)}</b>
        </div>
      </div>

      {domain === "air" ? air : domain === "sea" ? sea : space}
    </div>
  );
}
