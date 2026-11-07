/**
 * SeatFreshness (DASH-BAND) — the one-line freshness bar under the map seat.
 *
 * ⛔ WHY IT EXISTS. The square seat leaves a band under the map (--below-map-h was 0), and
 * the token's own rule is «content first, height second» — never a reserved-empty strip.
 * This bar is the content that claims the band: per-domain freshness, from state App
 * ALREADY holds on this surface. Nothing here fetches; every input is a prop.
 *
 * ⛔ WHY THE SEA/SPACE ROWS READ ALERT VINTAGE, NOT FEED STATUS — measured, not preferred.
 * The maritime poll and the satellite catalogue are gated OFF outside their own surfaces
 * and the monitor's تفصيلي mode (App: seaFeedOn / spaceFeedOn), so in the idle state
 * `seaSources` is empty and `satFetchedAt` is null. Printing «بلا بيانات» there would say
 * "no data" when the truth is "not polled on this surface" — the dishonest kind of empty.
 * What this surface DOES hold for those domains is its own alert feed: `countryData`
 * is fetched for the idle card on exactly this view, and each domain stat carries a
 * `vintage`. So the sea/space rows state that fact precisely — «آخر تنبيه منذ …» — and
 * never borrow a liveness word they have not earned. The air row keeps the real feed
 * verdict, because the LiveFeed runs unconditionally.
 *
 * ⛔ THE WORD CARRIES THE VERDICT, the dot only repeats it (UI-FRESH).
 *
 * ⚠ THE HEIGHT IS A TOKEN. --below-map-h must equal this bar's rendered height + the gutter
 * above it or the band lies: the token shrinks --map-seat-h to make room, so a taller bar
 * would overflow the viewport and a shorter one would leave the band reserved-empty again.
 * The value is measured on the rendered DOM and recorded in tokens.css.
 */

import type { FeedStatus } from "../api/client";
import type { AlertDomainStat } from "../api/types";
import { ageSeconds, isoAge, isoAgeSeconds } from "../lib/format";
import { T } from "../lib/strings";

export interface SeatFreshnessProps {
  air: {
    status: FeedStatus;
    /** the live snapshot's own timestamp (payload.ts) — null until the first snapshot */
    snapshotTs: string | null;
  };
  /** the alert feed's per-domain stats (countryData?.domains) — the ONLY freshness this
   *  surface holds for sea/space while their feeds are gated off (see the header). */
  alertDomains: AlertDomainStat[] | null;
}

function airVerdict(status: FeedStatus): { cls: string; word: string } {
  if (status.transport === "ws") return { cls: "ws", word: T.seatFresh.live };
  if (status.transport === "polling" && status.live) return { cls: "polling", word: T.seatFresh.polling };
  if (status.transport === "polling") return { cls: "error", word: T.seatFresh.error };
  return { cls: "", word: T.seatFresh.connecting };
}

function alertAge(domains: AlertDomainStat[] | null, domain: string): string {
  const v = domains?.find((d) => d.domain === domain)?.vintage ?? null;
  return v ? T.seatFresh.alertAge(isoAge(v)) : T.seatFresh.none;
}

export default function SeatFreshness({ air, alertDomains }: SeatFreshnessProps) {
  const S = T.seatFresh;
  const airV = airVerdict(air.status);
  const airAge = air.snapshotTs ? ageSeconds(isoAgeSeconds(air.snapshotTs)) : null;

  return (
    <div className="seat-fresh" role="status" aria-label={S.aria}>
      <span className="sf-item">
        <i className={`dot ${airV.cls}`} aria-hidden="true" />
        <b>{T.country.airLabel}</b> {airV.word}
        {airAge && <span className="sf-age"> · {airAge}</span>}
      </span>
      <span className="sf-item">
        <i className="dot" aria-hidden="true" />
        <b>{T.country.seaLabel}</b> {alertAge(alertDomains, "sea")}
      </span>
      <span className="sf-item">
        <i className="dot" aria-hidden="true" />
        <b>{T.country.spaceLabel}</b> {alertAge(alertDomains, "space")}
      </span>
    </div>
  );
}
