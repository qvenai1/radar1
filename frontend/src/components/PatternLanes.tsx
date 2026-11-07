/**
 * PatternLanes (DASH-7) — one lane per pattern, twenty-four cells per lane, one axis.
 *
 * ⛔ EXTRACTED SO THERE IS ONE DEFINITION, NOT TWO. The fourth proposal draws these lanes on
 * BOTH of its content surfaces — inside the expanded row on المراقبة, and under the claims on
 * الكيان («الأنماط عبر النافذة — التزامن يُقرأ حيث تصطفّ الخلايا»). They were built inside
 * `EntityRows`, so the entity surface had no lanes at all and copying them there would have
 * created a second renderer of the composite's central claim, free to drift from the first.
 *
 * ⛔ CELLS, NOT BARS, AND THAT IS THE WHOLE POINT. Co-occurrence is read by scanning DOWN a
 * column: cells line up, continuous bars at arbitrary sub-pixel offsets do not. The proposal
 * says it in one sentence — «التزامن يُقرأ حيث تصطفّ الخلايا» — and it is the reason the
 * rejected 300px shelf failed at 7px a cell, under the 24px Heer, Kong & Agrawala (CHI 2009)
 * measured as optimal ("estimation times decrease as charts get smaller, though accuracy also
 * decreases" — smaller felt faster while being wronger).
 * MEASURED ON THE RELEASED STAGE: the track is 1224px at 1440 and 1684px at 1900, so 24 cells
 * with a 2px gap render **49.1px** and **68.3px**. Above the threshold at both gate widths.
 *
 * ⚠ THE BUCKETS SPAN THE ROW'S OWN EXTENT, NEVER A SHARED WINDOW. The mock's axis reads
 * «الآن ← −٢٤س» because its sample is one 24-hour window; ours is a hull's envelope, measured
 * up to 42 days (DASH-4b, `ZEYNEP I`). Twenty-four cells over that extent is a RESOLUTION, not
 * a claim of hours — and the axis under the stack names both ends precisely so no shared
 * timescale is implied between one row and the next.
 *
 * ⚠ AND NON-CO-OCCURRENCE IS NOW VISIBLE, which the bars hid. `ZEYNEP I`'s three patterns fall
 * on 26 July, 3 August and 24 August: three lit cells in three different columns. The old
 * continuous bars drew that as three smears a reader could easily take for one episode.
 */

import type { Alert, PatternSpan } from "../api/types";
import { T } from "../lib/strings";

/** Domain → the glyph token the rest of the app already uses for it. */
export const DOMAIN_DOT: Record<string, string> = {
  air: "var(--glyph-air)", sea: "var(--glyph-vessel)", space: "var(--glyph-sat)",
};

/** Pattern → its lane colour. ⚠ Reuses the DOMAIN glyph tokens rather than inventing a
 *  per-pattern palette: a new colour axis would compete with severity, which is the only
 *  ranking this surface may show. GNSS keeps its own token because the app already gives it
 *  one (`--glyph-gnss`) and it is the one pattern about the SIGNAL, not the vessel. */
const LANE_COLOUR: Record<string, string> = { gnss_anomaly: "var(--glyph-gnss)" };

export const laneColour = (type: string, domain: string) =>
  LANE_COLOUR[type] ?? DOMAIN_DOT[domain] ?? "var(--ink-dim)";

export const patternLabel = (t: string) => T.vessel.cxEvents[t] ?? t;

export const ms = (iso: string | null | undefined) => (iso ? Date.parse(iso) : NaN);

const CELLS = 24;

interface LaneProps { spans: PatternSpan[]; domain: string; from: number; to: number; predicted: boolean }

function Lane({ spans, domain, from, to, predicted }: LaneProps) {
  const span = Math.max(to - from, 1);
  const type = spans[0].type;
  const on = new Array<boolean>(CELLS).fill(false);
  for (const s of spans) {
    const a0 = ms(s.start);
    if (!Number.isFinite(a0)) continue;
    const b0 = s.end ? ms(s.end) : to;          // an OPEN interval runs to the row's end
    const i0 = Math.max(0, Math.min(CELLS - 1, Math.floor(((a0 - from) / span) * CELLS)));
    const i1 = Math.max(i0, Math.min(CELLS - 1, Math.floor(((Math.max(b0, a0) - from) / span) * CELLS)));
    for (let i = i0; i <= i1; i++) on[i] = true;
  }
  return (
    <div className="er-lane">
      <span className="er-lane-label">{patternLabel(type)}</span>
      <span className="er-lane-track" style={{ ["--c" as string]: laneColour(type, domain) }}>
        {/* ⚠ An INSTANT lights exactly one cell. The old bar needed a 1.5 % minimum width to
            be visible, which was a width that meant nothing; the cell IS the unit, and being
            in it is the whole claim. */}
        {on.map((lit, i) => (
          <i key={i} className={`er-cell${lit ? (predicted ? " pred" : " on") : ""}`} />
        ))}
      </span>
    </div>
  );
}

/** The spans of one alert, grouped by type and ordered by first occurrence, with the extent
 *  they are drawn on. Exported so a caller can ask whether there is anything to draw. */
export function lanesOf(a: Alert) {
  const spans = a.pattern_spans ?? [];
  const byType = new Map<string, PatternSpan[]>();
  for (const s of spans) {
    const list = byType.get(s.type);
    if (list) list.push(s); else byType.set(s.type, [s]);
  }
  const lanes = [...byType.values()].sort((x, y) => ms(x[0].start) - ms(y[0].start));
  const starts = spans.map((s) => ms(s.start)).filter(Number.isFinite);
  const ends = spans.map((s) => (s.end ? ms(s.end) : NaN)).filter(Number.isFinite);
  const from = starts.length ? Math.min(...starts) : ms(a.phenomenon_start);
  const to = Math.max(ends.length ? Math.max(...ends) : -Infinity,
                      starts.length ? Math.max(...starts) : -Infinity,
                      ms(a.phenomenon_end));
  return { lanes, from, to: Number.isFinite(to) && to > from ? to : from + 3600_000 };
}

export default function PatternLanes({ alert }: { alert: Alert }) {
  const { lanes, from, to } = lanesOf(alert);
  if (!lanes.length) return null;
  const cap = (alert.n_pattern_spans ?? 0) > (alert.pattern_spans?.length ?? 0);
  return (
    <div className="er-lanes">
      {lanes.map((spans) => (
        <Lane key={spans[0].type} spans={spans} domain={alert.domain} from={from} to={to}
              predicted={alert.is_prediction === true} />
      ))}
      {/* ⛔ THE AXIS NAMES ITS OWN SPAN. Every row has a DIFFERENT extent — a hull's envelope,
          not the feed's window — so an unlabelled axis would imply a shared timescale between
          rows that does not exist. */}
      <div className="er-axis">
        <span />
        <span className="er-axis-t">
          <span>{new Date(to).toISOString().slice(0, 16).replace("T", " ")}</span>
          <span>{new Date(from).toISOString().slice(0, 16).replace("T", " ")}</span>
        </span>
      </div>
      {cap && (
        /* ⚠ A CAP THAT DOES NOT SAY SO reads as "this is everything". */
        <div className="er-cap">
          {T.entityRows.capped(alert.pattern_spans!.length, alert.n_pattern_spans!)}
        </div>
      )}
    </div>
  );
}
