/**
 * EntityRows — DASH-4b-2. The monitoring surface: ONE ROW PER ENTITY, patterns as lanes.
 *
 * ⛔ WHAT THIS REPLACES, AND WHY THE OLD SHAPE WAS WRONG. The idle surface listed ALERTS as
 * cards grouped by `signal_type`. A hull carrying three co-occurring behaviours therefore
 * appeared as one card whose headline flattened them into prose, and two proposals before
 * this one split the same vessel across separate rows entirely. Co-occurrence is the whole
 * claim a maritime composite makes; a layout that cannot show it is arguing against its own
 * data.
 *
 * ⛔ AND THE LANES ARE THE CLAIM, NOT DECORATION. Splunk ES gives every event category its own
 * swim lane on the same object for exactly this reason: co-occurrence is then READ as vertical
 * alignment rather than ASSERTED as a score. That distinction is not stylistic — ODNI ICD 203
 * forbids combining a likelihood and a confidence in one expression, and a single blended
 * "risk 78" collapses precisely those two axes. Nothing here is summed.
 *
 * ⛔ THE AXIS IS THE HULL'S OWN ENVELOPE, NOT THE FEED'S WINDOW, and the live data is why.
 * Measured on the real feed: ZEYNEP I's three patterns fall on 26 Jul, 3 Aug and 24 Aug — an
 * envelope of 29 days inside a 24-hour alert window. Drawing those against the feed's window
 * would put every bar off-scale; drawing them against the hull ENVELOPE without saying so
 * would imply a shared timescale between rows that does not exist. So each row states its own
 * span in words, and the axis ends are labelled with real dates.
 *
 * ⚠ AND THE ENVELOPE ITSELF IS NOT A LANE. `phenomenon_start`/`_end` on the alert is
 * first-event → last-event; rendered as a bar it says all three patterns ran for 29 days.
 * `pattern_spans` (DASH-4b-2, one window per pattern) exists because that lie was the
 * alternative.
 *
 * ⚠ `end: null` MEANS OPEN, never zero-length: `maritime_events.end_ts IS NULL` is a gap that
 * has not closed. Such a bar is drawn to the axis end and marked, never as a point.
 */
import { useMemo, useState } from "react";

import type { Alert } from "../api/types";
import { num } from "../lib/format";
import PatternLanes, { DOMAIN_DOT, lanesOf, patternLabel } from "./PatternLanes";
import { T } from "../lib/strings";

/* ⛔ DASH-7 — THE LANES MOVED TO `PatternLanes.tsx`, AND THE HELPERS WITH THEM. The fourth
 * proposal draws the same lanes on الكيان as on المراقبة; keeping the renderer here would have
 * meant a second copy on the entity surface, free to drift from this one. The composite's
 * central claim gets ONE renderer. */
/** «منذ ٤ ساعات» / «غداً ٠٥:٢٨» — age is REQUIRED on a live row (Kay et al., CHI 2016:
 *  freshness is "one of the only available signals for expert users to assess risk").
 *  ⚠ A NEGATIVE age is a FUTURE phenomenon (a predicted pass) and is rendered as such, never
 *  clamped to zero — the sign is the information. */
function ageText(seconds: number | null | undefined): { text: string; future: boolean } {
  if (seconds == null || !Number.isFinite(seconds)) return { text: "—", future: false };
  const future = seconds < 0;
  const s = Math.abs(seconds);
  if (s < 90) return { text: T.dashHome.ago(`${Math.round(s)} ث`), future };
  if (s < 5400) return { text: T.dashHome.ago(`${Math.round(s / 60)} د`), future };
  if (s < 172800) return { text: T.dashHome.ago(`${(s / 3600).toFixed(1)} س`), future };
  return { text: T.dashHome.ago(`${Math.round(s / 86400)} ي`), future };
}

interface Props {
  alerts: Alert[];
  /** Opening an entity is a SELECTION, not navigation — the caller decides what a surface
   *  change means (DASH-4b-3 gives the entity its own full-width surface). */
  onOpen?: (a: Alert) => void;
}

export default function EntityRows({ alerts, onOpen }: Props) {
  const [open, setOpen] = useState<string | null>(null);

  const rows = useMemo(() => alerts.map((a) => ({ a, ...lanesOf(a) })), [alerts]);

  if (!alerts.length) return null;

  return (
    <div className="entity-rows" role="list" aria-label={T.entityRows.aria}>
      {rows.map(({ a, lanes }) => {
        const key = `${a.signal_type}:${a.entity_ref.id}`;
        const isOpen = open === key;
        const age = ageText(a.observed_age_s);
        return (
          <div className={`entity-row${isOpen ? " open" : ""}`} role="listitem" key={key}>
            <button
              type="button"
              className="er-head"
              aria-expanded={isOpen}
              onClick={() => (isOpen && onOpen ? onOpen(a) : setOpen(isOpen ? null : key))}
            >
              <span className="er-dot" style={{ background: DOMAIN_DOT[a.domain] }} aria-hidden="true" />
              <span className="er-name">
                {a.entity_ref.label}
                {a.summary ? <small>{a.summary}</small> : null}
              </span>
              {/* ⛔ DASH-7 — WHERE AND WHAT ARE TWO COLUMNS NOW. Both used to be one: `headline`
                  is a whole Arabic sentence, and on the released stage it ran the width of the
                  screen while the co-occurrence claim — the reason the row exists — had no
                  column of its own at all. The place is the alert's own `countries[]`, which
                  every family already carries (DASH-1); the sentence stays reachable on الكيان,
                  where there is room to read it. */}
              <span className="er-where">
                {a.countries?.length
                  ? a.countries.map((c) => T.country.names[c] ?? c).join(" · ")
                  : (a.headline || a.lede)}
              </span>
              {/* the co-occurrence, stated as a count rather than implied by a sentence */}
              <span className="er-pat">
                {lanes.length > 1
                  ? <><b>{num(lanes.length)}</b> {T.entityRows.coPatterns}</>
                  : lanes.length === 1 ? patternLabel(lanes[0][0].type) : ""}
              </span>
              {a.triage ? (
                <span className={`er-triage${a.triage_stale ? " stale" : ""} t-${a.triage}`}>
                  {T.entityRows.triage[a.triage]}{a.triage_stale ? T.entityRows.staleSuffix : ""}
                </span>
              ) : <span />}
              <span className={`er-sev s-${a.severity}`}>{T.dashHome.severity[a.severity] ?? a.severity}</span>
              <span className={`er-age${age.future ? " future" : ""}`}>{age.text}</span>
            </button>

            {/* ⛔ ONE RENDERER FOR THE CO-OCCURRENCE CLAIM — the same component الكيان
                draws under its claims. See PatternLanes.tsx. */}
            {isOpen && <PatternLanes alert={a} />}

          </div>
        );
      })}
    </div>
  );
}
