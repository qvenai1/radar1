/**
 * MonitorConsole (DASH-7) — the لوحة tab's command line and status line.
 *
 * ⛔ THIS IS THE FOURTH PROPOSAL, AND IT IS THE APPROVED ONE. The owner rejected the built
 * surface as «لا يبدو متماسكاً أو موحّداً» and named the reference: the artifact «لوحة محلّل
 * أثير · المقترح الرابع». Measured on the built app at 1920×1000 before this item, the owner
 * was right in numbers as well as in impression — ONE tab was carrying three content widths
 * and three map sizes:
 *     entity 1872 content / 0 map · area 920 / 768 · detail 920 / 768 · case 920 / 768 · map 0 / 1888
 * and `case` wasted 822px of 1000 vertical, `area` 649px. That is §10.1's "four views, four
 * layout grammars" reproduced one level down, on the surfaces of a single tab.
 *
 * THE PROPOSAL'S THESIS, VERBATIM: «سطح واحد كامل، لا قسمة دائمة» — one full surface, never a
 * permanent split. Its console is three stacked regions, and this file is the first two:
 *
 *   1. `.mon-cmd`   one command line: the query, the scope pills, the SURFACE SWITCHER, the
 *                   case counter. Four controls that were scattered across a mode row, a
 *                   search block, a seven-chip country row and a window row.
 *   2. `.mon-stat`  one status line: the alert counts, the domain counts, and per-domain
 *                   freshness on the inline-end — replacing three large tiles.
 *   3. `.mon-stage` the surface itself (DashboardPanel).
 *
 * ⚠ THE SWITCHER IS THREE, AND THAT IS THE POINT — المراقبة · الكيان · الخريطة. Five surfaces
 * became three by the owner's ruling «التصميم هو الحكم»: «بحث منطقة» folds into الخريطة (the
 * box is drawn ON the map, which is where the question "where" is already being asked) and
 * «تفصيلي» folds into المراقبة (both browse what is already streaming).
 *
 * ⚠ TWO PLACES THE PROPOSAL IS SILENT, ADAPTED RATHER THAN INVENTED — both stated because a
 * silent adaptation is how a mock quietly becomes a different product:
 *   · THE CASE FILE. The mock shows `📁 ١` as a static counter and gives it no surface. DASH-5
 *     shipped a real case file with an export, so the counter is a BUTTON here and opens the
 *     file as a stage — the switcher stays three, and no shipped feature loses its door.
 *   · THE COUNTRY SCOPE. The mock has no country control; its query is «يُوجَّه حسب صيغة
 *     المُعرّف». But `/alerts` is country-scoped and every number on the status line is
 *     `countries_filtered`-dependent (§6.11 warning 1: a scoped number without its scope is a
 *     lie waiting to be quoted). Dropping the control would not drop the scoping — it would
 *     hide it. So it is a PILL, in the proposal's own command-line grammar.
 */

import { useState } from "react";
import type { AlertDomainStat, AlertsResponse, AoiCountry } from "../api/types";
import { AOI_COUNTRIES } from "../lib/alertCards";
import { num } from "../lib/format";
import { T } from "../lib/strings";
import Flag from "./Flag";
import TimeWindowPicker from "./TimeWindowPicker";

/** The three surfaces of the fourth proposal. `case` is reachable, but never from here. */
export type Surface = "mon" | "ent" | "map";

const SURFACES: readonly { k: Surface; label: string }[] = [
  { k: "mon", label: T.console.surfMon },
  { k: "ent", label: T.console.surfEnt },
  { k: "map", label: T.console.surfMap },
];

/**
 * ⛔ «يُوجَّه حسب صيغة المُعرّف» — THE QUERY ROUTES ITSELF, which is what lets one field replace
 * the entity segment the old surface carried. Each identifier space has a shape the others do
 * not, so the string decides which endpoint answers it:
 *
 *   9 digits        MMSI      → vessel      (ITU: MID + 6)
 *   7 digits        IMO       → vessel      (IMO: 6 + check digit)
 *   5 digits        NORAD     → satellite
 *   6 hex           ICAO24    → aircraft    (the 24-bit address, hex)
 *   4 letters       ICAO      → airport     (ICAO location indicator)
 *
 * ⚠ AMBIGUITY IS RESOLVED BY NOT GUESSING. A 6-character all-DIGIT string is a legal ICAO24
 * and nothing else here, but a 6-digit string is far more likely a typo mid-MMSI, so hex
 * routing requires at least one a–f. Anything unmatched is a NAME, and a name keeps whatever
 * entity the analyst last chose — the field never silently changes what is being searched.
 */
export function routeQuery(q: string): "vessel" | "aircraft" | "satellite" | "airport" | null {
  const s = q.trim();
  if (/^\d{9}$/.test(s)) return "vessel";
  if (/^\d{7}$/.test(s)) return "vessel";
  if (/^\d{5}$/.test(s)) return "satellite";
  if (/^[0-9a-f]{6}$/i.test(s) && /[a-f]/i.test(s)) return "aircraft";
  if (/^[A-Za-z]{4}$/.test(s)) return "airport";
  return null;
}

interface Props {
  /** rendered at the START of the command line — the app-level رصد/إحصاءات switch, which
   *  DASH-NAV moved into the page and which must not go back to being a second hierarchy. */
  lead?: React.ReactNode;
  query: string;
  onQuery: (q: string) => void;
  routedTo: string | null;
  surface: Surface;
  onSurface: (s: Surface) => void;
  country: AoiCountry;
  onCountry: (c: AoiCountry) => void;
  windowH: number;
  onWindow: (h: number) => void;
  caseCount: number;
  onOpenCase: () => void;
  caseOpen: boolean;
  /** ⛔ THE DOOR TO تفصيلي. The proposal draws no button for the domain browser, but it DOES
   *  draw these three counts — so the number that says «8 طائرة» is what opens the air detail.
   *  ⚠ Written as a real control, not asserted in a comment: an earlier draft of this file
   *  claimed the counts were the door while leaving them as plain spans, which made DomainDetail
   *  unreachable and the comment false. */
  onOpenDomain: (d: "air" | "sea" | "space") => void;
  data: AlertsResponse | null;
  /** the per-domain freshness line — composed by App, rendered on the status line's end */
  freshness: React.ReactNode;
}

export default function MonitorConsole({
  lead, query, onQuery, routedTo, surface, onSurface, country, onCountry,
  windowH, onWindow, caseCount, onOpenCase, caseOpen, onOpenDomain, data, freshness,
}: Props) {
  const [pop, setPop] = useState<"country" | "window" | null>(null);
  const C = T.country;
  const alerts = data?.alerts ?? [];
  const sev = alerts.reduce<Record<string, number>>((a, x) => {
    a[x.severity] = (a[x.severity] ?? 0) + 1; return a;
  }, {});
  const reviewing = alerts.filter((a) => a.triage === "reviewing").length;

  /* ⚠ WARNING 1 (§6.11) SURVIVES THE REDESIGN. The status line's numbers are country-scoped,
   * so the scope must be readable beside them — the pill IS that, and when the backend has not
   * applied what the pill says, the line says so instead of labelling global numbers. */
  const scopeHonest = (data?.countries_filtered ?? null) === country;

  return (
    <div className="mon-console">
      <div className="mon-cmd">
        {lead}
        <label className="mon-q">
          <input
            className="mon-q-in"
            type="text"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder={T.console.queryHint}
            aria-label={T.entitySearch.ariaQuery}
          />
          {/* the routing verdict, stated rather than silently applied */}
          {routedTo && <span className="mon-q-route">{routedTo}</span>}
        </label>

        <div className="mon-pills">
          <button
            className={`mon-pill${pop === "country" ? " open" : ""}${scopeHonest ? "" : " warn"}`}
            aria-expanded={pop === "country"}
            onClick={() => setPop((p) => (p === "country" ? null : "country"))}
          >
            <Flag iso={country} />
            {C.names[country] ?? country}
          </button>
          <button
            className={`mon-pill${pop === "window" ? " open" : ""}`}
            aria-expanded={pop === "window"}
            onClick={() => setPop((p) => (p === "window" ? null : "window"))}
          >
            {T.console.windowLabel(windowH)}
          </button>
        </div>

        <div className="mon-surf" role="group" aria-label={T.console.surfAria}>
          {SURFACES.map((s) => (
            <button
              key={s.k}
              aria-pressed={!caseOpen && surface === s.k}
              onClick={() => onSurface(s.k)}
            >
              {s.label}
            </button>
          ))}
        </div>

        {/* ⚠ A BUTTON, NOT THE MOCK'S STATIC COUNTER — see the file header. */}
        <button
          className={`mon-pill mon-case${caseOpen ? " open" : ""}`}
          aria-pressed={caseOpen}
          onClick={onOpenCase}
          title={T.console.caseTitle}
        >
          <span aria-hidden="true">📁</span>
          <b>{num(caseCount)}</b>
        </button>
      </div>

      {/* the two popovers live under the command line so the line itself never grows */}
      {pop === "country" && (
        <div className="mon-pop" role="group" aria-label={C.pickerAria}>
          {AOI_COUNTRIES.map((c) => (
            <button
              key={c}
              aria-pressed={c === country}
              onClick={() => { onCountry(c); setPop(null); }}
            >
              <Flag iso={c} />
              {C.names[c] ?? c}
            </button>
          ))}
        </div>
      )}
      {pop === "window" && (
        <div className="mon-pop">
          <TimeWindowPicker value={windowH} onChange={(h) => { onWindow(h); setPop(null); }} note={null} />
        </div>
      )}

      <div className="mon-stat" role="group" aria-label={T.dashHome.tileAria}>
        <span className="ms hi"><b>{num(alerts.length)}</b><span>{T.console.statAlerts}</span></span>
        <span className="ms hi"><b>{num(sev.high ?? 0)}</b><span>{T.dashHome.tileHigh}</span></span>
        <span className="ms rv"><b>{num(reviewing)}</b><span>{T.dashHome.tileMedium}</span></span>
        <span className="ms-div" aria-hidden="true" />
        {(data?.domains ?? []).map((d: AlertDomainStat) => (
          <button
            className="ms ms-go"
            key={d.domain}
            title={T.country[`${d.domain}Title` as "airTitle"]}
            onClick={() => onOpenDomain(d.domain as "air" | "sea" | "space")}
          >
            <b>{num(d.count)}</b><span>{T.console.domain[d.domain] ?? d.domain}</span>
          </button>
        ))}
        {!scopeHonest && <span className="ms-warn">{C.unscopedWarn}</span>}
        <span className="ms-rt">{freshness}</span>
      </div>
    </div>
  );
}
