/**
 * CountryEvents (DASH-2e) — the country card, and the home's lead panel.
 *
 * ⛔ DASH-6a — THIS FILE BELONGS TO جوّي · بحري · فضائي ALONE, AND THAT IS NOW STRUCTURAL.
 * The dashboard used to mount this same component and switch its card list off with a
 * `hideList` prop. The prop is gone, and so is every branch that read it: the dashboard has
 * its own chrome in `MonitorConsole.tsx`. Nothing in this file is conditional on which
 * surface renders it, because only one surface does.
 * ⚠ THE REASON IS A LEAK THAT HAPPENED TWICE WITH THE CONSTRAINT WRITTEN DOWN BOTH TIMES:
 * `8b6b42c` reverted four CSS rules authored "for the dashboard" that repainted all three
 * domain tabs, and `96b7f29` reverted THEME-3, which repainted them through the global token
 * block while `verify:scope` reported 6/6 green (its fingerprint was geometry-only). A shared
 * surface is what made both edits reach here; removing the sharing is what stops the third.
 * ⇒ Do not add a prop that makes this component behave differently for another surface. If a
 * surface needs different behaviour, it needs its own component — that is the lesson, and it
 * cost two reverts.
 *
 * Replaces the temporary tenant (DashboardHome) that 2c′ lifted and 2d″ parked here. The
 * question this surface answers is "what is happening in ONE country right now?", so every
 * number on it is country-scoped and says so.
 *
 * ⛔ THE FOUR CONSUMER WARNINGS (§6.11) ARE ENFORCED BY CONSTRUCTION, not by care:
 *
 *  1. `countries_filtered` is read BEFORE any number renders. If the backend did not apply
 *     a country, the scope line says so instead of labelling global numbers with a flag.
 *     A scoped number without its scope is a lie waiting to be quoted.
 *  2. The space count is NEVER called "tracked satellites". The element catalogue carries
 *     no country; this number is objects with a computed PASS over the country in the
 *     window. The label and its title both say that.
 *  3. The air count names its population (OpenSky) and no /adsb total is placed beside it.
 *     They are different communities and adding them manufactures a number that does not
 *     add up.
 *  4. `importance` orders the list and nothing else — no colour derives from it, no tile
 *     sums it, no badge reads it. `severity` + `severity_basis` remain the only register
 *     for risk, so a military card leads the list and is still INFO-badged.
 *
 * ⚠ WHAT THIS DELIBERATELY DOES NOT SHOW. The three urgency tiles from UI-1 stay (high /
 * medium / total). The four inventory tiles do NOT come back — UI-1 cut seven numbers to
 * three on purpose, and the inventory board is 2g's surface. Nor does the four-family
 * count line: it is inventory too, and this panel is 340px wide with a length gate.
 *
 * Colour comes from the project's semantic tokens only (--sev-*), never literals, so
 * flipping the identity later repaints this card with everything else (THEME-2).
 */

import { useMemo, useState } from "react";
import type { Alert, AlertDomainStat, AlertsResponse, AoiCountry } from "../api/types";
import { isoAge, num } from "../lib/format";
import { T } from "../lib/strings";
import {
  AOI_COUNTRIES, SEV_LEGEND_ORDER, SEV_TONE, alertFoot, alertKey, hasArabicRegister, isoFlag,
  planHoist, rankOn, splitHeadline,
} from "../lib/alertCards";
import Caveat from "./Caveat";
import Flag from "./Flag";
import ResultCards from "./ResultCards";
import type { CardItem, CardTone } from "./ResultCards";
import TimeWindowPicker from "./TimeWindowPicker";

/**
 * How many events this panel shows before "show all".
 *
 * ⚠ NOT the shared DEFAULT_CAP of 15. That number was measured for the full-width
 * workspace, where the card grid is FIVE columns — 15 cards is three tidy rows there. In
 * this 340px floating panel the grid is ONE column, so 15 cards is 15 rows, and text
 * wraps more, making each card ~191px instead of ~159px.
 *
 * Measured across all seven countries against 2d″'s 3,042px length gate: at 15 the panel
 * ran 3,133 (TR) · 3,156 (LB) · 3,192 (CY) · 3,204 (IL) — four of seven OVER the gate.
 * SY sat at 2,821 with 13. Dropping three cards at ~191px each brings the worst case to
 * ≈2,630, which clears the gate with headroom rather than by a hair. The full set stays
 * one click away, named, exactly as the cap pattern requires.
 *
 * ⚠ RE-MEASURED AND LOWERED 12 → 11 IN DASH-CARD. The two-layer card is taller than the one
 * it replaced: it gained the registration/last-seen meta line and the disclosure summary,
 * against only the English `note` it dropped. Measured on IL (the densest maritime country,
 * 9 maritime + 3 other cards): the panel folded at **3,172px** with a cap of 12 — 130px OVER
 * the 3,042 gate. This is the structural fix the gate asks for, not a tolerance: one card
 * out brings it back under. Re-measure this number whenever the card's height changes again;
 * it is a consequence of the card, not a preference.
 *
 * ⚠ RE-MEASURED AND LOWERED 11 → 10 AFTER DASH-CARD-2. Consuming all five families put the
 * Arabic headline on every card, and DASH-CARD-2 answered the resulting overflow with a FOLD
 * (`.rc-two .rc-sub` clamps to two lines while the card is closed) rather than a lower cap.
 * The fold recovered six of the seven countries but not the seventh: measured at 1440×900
 * with a cap of 11, the lead panel runs **3,062 (SY)** · 3,040 (JO) · 3,007 (TR/IL/CY) ·
 * 2,974 (LB) · 1,716 (IQ) — سوريا **20px OVER** the gate, and JO 2px under it.
 *
 * ⛔ 20px is not a tolerance to absorb. The two frozen constraints leave no other lever: the
 * gate itself is frozen, and «عرض المسار» stays on the card. A clamp already took the only
 * cheap height out of the card, so the remaining lever is the cap.
 *
 * RE-MEASURED AT 10, same viewport, all seven: **2,851 (SY)** · 2,829 (JO) · 2,796 (TR/IL/CY)
 * · 2,763 (LB) · 1,716 (IQ). Every country under the gate, worst case **191px clear** of it
 * — a real margin rather than the 2px hairline JO was holding. SY fell 3,062 → 2,851, i.e.
 * exactly one measured card pitch (199px card + 12px grid gap = 211px), which is the check
 * that the cap is what moved the panel and not something else. The full set stays one named
 * click away, exactly as the cap pattern requires.
 */
const PANEL_CAP = 10;

/** the three scoped domain numbers, each with the population it counts */
const DOMAIN_META: Record<string, { label: string; pop: string; title: string }> = {
  air: { label: T.country.airLabel, pop: T.country.airPop, title: T.country.airTitle },
  sea: { label: T.country.seaLabel, pop: T.country.seaPop, title: T.country.seaTitle },
  space: { label: T.country.spaceLabel, pop: T.country.spacePop, title: T.country.spaceTitle },
};

interface Props {
  data: AlertsResponse | null;
  loading: boolean;
  country: AoiCountry;
  onCountry: (c: AoiCountry) => void;
  windowH: number;
  onWindow: (h: number) => void;
  onOpenAlert: (a: Alert) => void;
  /**
   * DASH-2g′ — which domain tab this card is currently pinned to.
   *
   * ⛔ THIS IS A DISPLAY ORDER, NOT A FILTER AND NOT A JUDGEMENT. The card shows ALL THREE
   * domains in full on every tab; `activeDomain` only decides which section is read first.
   * ZERO information is hidden, dropped, capped differently, or de-ranked by it.
   *
   * WHY ORDER AND NOT FILTER. The card answers "what is happening in this country right
   * now?", and that question is cross-domain by construction — a maritime analyst who
   * cannot see the military air picture beside their vessels has been given a worse answer,
   * not a tidier one. Filtering by the tab would also make the three urgency tiles and the
   * three domain counts disagree with the list beneath them, which is the scoped/global
   * confusion §6.11 warning 1 exists to prevent.
   *
   * It also never touches `severity` or `importance`: the backend owns rank, this reorders
   * whole sections around it. A leading section is nearer, not more dangerous.
   *
   * ⛔ DASH-IDLE — OPTIONAL SINCE THE CARD RETURNED TO «لوحة». The monitor's idle state
   * mounts this same component with NO active domain (the surface is cross-domain by
   * definition), so `undefined` means: keep the backend's order untouched — no lead-band
   * partition at all. Omitting the prop is the neutral case; passing it behaves exactly as
   * before on the three domain tabs.
   */
  activeDomain?: "air" | "sea" | "space";
}

export default function CountryEvents({
  data, loading, country, onCountry, windowH, onWindow, onOpenAlert, activeDomain,
}: Props) {
  const [showAll, setShowAll] = useState(false);
  const C = T.country;
  const name = C.names[country] ?? country;

  /* The backend owns the order (alerts.sort_by_importance: importance asc, then recency
   * within a band) and echoes which axis produced it. We re-apply the SAME axis rather
   * than forming a second opinion — a stable sort over an already-sorted list is a no-op
   * — and we key off `sorted_by`, never off what we requested, so a payload in flight is
   * never ranked by an axis it was not built with (the DASH-2b rule). */
  const axis = data?.sorted_by ?? "importance";
  const sorted = useMemo(() => {
    const a = data?.alerts ?? [];
    return [...a].sort((x, y) => rankOn(axis, x) - rankOn(axis, y));
  }, [data, axis]);

  /* severity counts over the WHOLE scoped window, not the capped slice — the tiles
   * describe the country's situation, not what happens to be visible. */
  const sev = useMemo(() => {
    const c: Record<string, number> = { high: 0, medium: 0, low: 0, info: 0 };
    for (const a of sorted) c[a.severity] = (c[a.severity] ?? 0) + 1;
    return c;
  }, [sorted]);

  const visible = showAll ? sorted : sorted.slice(0, PANEL_CAP);

  // group by signal_type (NOT family): the military family carries two signal types with
  // DIFFERENT constant bases, so family-grouping would hoist a note that is not shared.
  const groups = useMemo(() => {
    const order: string[] = [];
    const bySignal = new Map<string, Alert[]>();
    for (const a of visible) {
      let g = bySignal.get(a.signal_type);
      if (!g) { g = []; bySignal.set(a.signal_type, g); order.push(a.signal_type); }
      g.push(a);
    }
    return order.map((signalType) => {
      const items = bySignal.get(signalType)!;
      const hoist = planHoist(signalType, items.map((a) => a.severity_basis));
      /* ⛔ DASH-CARD — TWO CARD SHAPES, CHOSEN BY WHAT THE ALERT ACTUALLY CARRIES, and
       * deliberately NOT by one ambiguous expression that tries to serve both.
       *
       * WITH the Arabic register (maritime today, 106/106 measured): `headline` is the lead
       * line and the English `summary` is NOT RENDERED AT ALL — it is unreadable here and
       * measured dishonest on 71/400, listing only the patterns it counted while
       * `n_pattern_types` counted more. `why` + `caveat` become the second layer.
       *
       * WITHOUT it (implausible/military, 0/218 have any): the card is left EXACTLY as it
       * was — `summary` in the sub line — until those families are generalised to.
       * ⛔ The empty case is never filled by falling back to English on a maritime row, and
       * never by inventing text: null means "no Arabic record yet", not "no warning applies".
       */
      const cards: CardItem[] = items.map((a) => {
        const base = {
          id: alertKey(a),
          title: a.entity_ref.label || a.entity_ref.id,
          badges: [
            { label: T.dashHome.severity[a.severity] ?? a.severity, tone: SEV_TONE[a.severity] },
            { label: T.dashHome.domainBadge[a.domain] ?? a.domain },
          ],
          foot: alertFoot(a),
          tone: a.severity as CardTone,
          // the ⓘ explainer rides on the severity badge; the badge's own colour and its
          // second, non-colour channel are THEME-2's and untouched here.
          //
          // ⛔ DASH-CARD-2: the hint is this alert's OWN basis, not one fixed sentence. The
          // old text said «مرتفع = تزامن نمطين», which is true of a maritime composite and
          // false of the rest — an emergency squawk is high BY DEFINITION and military is INFO
          // off the danger axis. `why` is the backend's `severity_basis` rendered in Arabic
          // (BE-SUMMARY-1 documents it as exactly that), so this shows the basis as-is in the
          // language this card speaks rather than the English field verbatim.
          //
          // ⛔ RANK-S2 removed the high-only branch. `sevHintHigh` claimed «مرتفع = تزامن
          // نمطين أو أكثر», and two pattern types ALONE rank MEDIUM — so it was false for the
          // maritime family too, not only for the other four. One fallback now, claiming
          // nothing about the cause; the alert's own `why` still wins whenever it exists.
          toneHint: a.why || T.dashHome.sevHint,
        };
        if (!hasArabicRegister(a)) {
          // unchanged shape for the families that have no Arabic record yet
          return { ...base, sub: a.summary || null, note: hoist.perCard(a.severity_basis) };
        }
        const { main, flagCountry } = splitHeadline(a.headline!);
        /* ⛔ A FORWARD-DATED alert never gets «آخر رصد منذ». Read from the contract, not from
         * a family name, so it generalises: `ts` is when the signal is from or as-of, and a
         * satellite pass carries a FUTURE one (measured: ts = tomorrow, vintage = the TLE epoch
         * from two days ago). Labelling that vintage "last observed" would put past tense over
         * an event that has not happened and call an element's age an observation of the object.
         * A pass whose start has already gone by flips back to the past-tense line on its own —
         * which is correct, and is why this tests the timestamp rather than the signal type. */
        const forward = a.ts ? Date.parse(a.ts) > Date.now() : false;
        /* ⚠ MID-ISO-1 changed what is possible here. DASH-CARD recorded that no ISO field
         * existed on this contract, so no flag emoji could be drawn; measured now, `flag_iso`
         * is present at 114/114 on maritime and 106/107 on implausible (and correctly absent on
         * military, which has no vessel). So the glyph is added — ⛔ BESIDE the words, never
         * instead of them: «علم التسجيل» must stay, because a flag glyph reads as a LOCATION
         * faster than a name does and registry is an administrative fact, not a position.
         *
         * ⚠ FLAG-1: `isoFlag` returns "" for SY on purpose (see its docstring — the emoji
         * font decides which Syrian flag paints, and this is a string, so no SVG can go
         * here). Hence the `.filter(Boolean)` below rather than a template — it is what
         * keeps a Syrian-registered hull from rendering a stray leading space. */
        const meta = [
          flagCountry
            ? [a.flag_iso ? isoFlag(a.flag_iso) : "", T.dashHome.flagOfRegistry(flagCountry)]
                .filter(Boolean).join(" ")
            : null,
          forward ? T.dashHome.forwardPass
                  : (a.vintage ? T.dashHome.lastSeen(isoAge(a.vintage)) : null),
        ].filter(Boolean).join(" · ");
        return {
          ...base,
          sub: main,
          meta: meta || null,
          // `why` already carries the severity_basis in readable Arabic, so the English
          // per-card `note` would restate it in the language this card is removing.
          note: null,
          expand: { why: a.why!, caveat: a.caveat! },
        };
      });
      return { signalType, items, hoist, cards };
    });
  }, [visible]);

  /* DASH-2g′ — the active tab's domain leads; everything else keeps the backend's order
   * behind it. A STABLE partition, not a sort: `Array.prototype.sort` is stable in every
   * engine this ships to, and the comparator only ever returns 0 or ±1 on the lead test, so
   * within each of the two bands the backend's importance/recency order survives untouched.
   *
   * A group is keyed by signal_type and CAN span domains (military carries an air signal and
   * a satellite one — §6.11), so the test is "does this group contain any alert of the active
   * domain", not "is this group's domain X". A mixed group leads when it is partly relevant,
   * which is the honest reading: it does contain what the analyst came for.
   *
   * ⛔ Nothing is removed here. `groups` in === `groups` out, same members, same cards.
   *
   * ⛔ DASH-IDLE — with no `activeDomain` (the monitor's idle state) there is no lead band
   * to form: the backend's importance/recency order IS the display order. */
  const orderedGroups = useMemo(() => {
    if (!activeDomain) return groups;
    const leads = (g: { items: Alert[] }) => (g.items.some((a) => a.domain === activeDomain) ? 0 : 1);
    return [...groups].sort((x, y) => leads(x) - leads(y));
  }, [groups, activeDomain]);

  const byKey = useMemo(() => new Map(sorted.map((a) => [alertKey(a), a] as const)), [sorted]);

  /* ⚠ WARNING 1, enforced here. `countries_filtered` is the backend's echo of what it
   * actually applied. Until it matches the picker, the numbers below are NOT this
   * country's and must not be presented as though they were. */
  const appliedTo = data?.countries_filtered ?? null;
  const scopeHonest = appliedTo === country;

  return (
    <div className="country-card">
      {/* ---- header: the seven-country picker + the window ---- */}
      <div className="section-label">{C.section}</div>
      <div className="sat-groups country-picker" role="group" aria-label={C.pickerAria}>
        {AOI_COUNTRIES.map((c) => (
          <button key={c} aria-pressed={c === country} onClick={() => onCountry(c)}>
            <Flag iso={c} />
            {C.names[c] ?? c}
          </button>
        ))}
      </div>
      <TimeWindowPicker value={windowH} onChange={onWindow} note={null} />

      {!data ? (
        <div className="filter-note">{loading ? T.dashHome.loading : T.dashHome.empty}</div>
      ) : (
        <>
          {/* ---- WARNING 1: the scope line, before any number ---- */}
          <div className={`filter-note cc-scope${scopeHonest ? "" : " cc-scope-warn"}`}>
            {scopeHonest ? C.scopedFor(name) : C.unscopedWarn}
          </div>

          {/* ---- the three scoped domain numbers, each naming its population ---- */}
          <div className="dash-secondary" role="group" aria-label={C.numbersAria}>
            {data.domains.map((d: AlertDomainStat) => {
              const meta = DOMAIN_META[d.domain];
              if (!meta) return null;
              return (
                <span key={d.domain} className="ds-item" title={meta.title}>
                  {/* ⛔ DASH-IDLE — the label is an ELEMENT, not a bare text node. This row is
                      a flex line; a text-only lead child is invisible to any "nearest child
                      starts on the container's line" measurement, which then read the COUNT
                      `<b>` — offset by the label's width — as the row's start and failed G2.
                      Same pixels, measurable children. */}
                  <span>{meta.label}</span> <b>{num(d.count)}</b>
                  <i className="dash-fresh">{meta.pop}</i>
                  {d.vintage ? <i className="dash-fresh">{T.dashHome.ago(isoAge(d.vintage))}</i> : null}
                </span>
              );
            })}
          </div>

          {/* ---- the THREE urgency tiles from UI-1. The four inventory tiles stay gone. ---- */}
          <div className="dash-stats triage" role="group" aria-label={T.dashHome.tileAria}>
            <div className="metric dash-stat fam high">
              <span className="n">{num(sev.high)}</span>
              <span className="l">{T.dashHome.tileHigh}</span>
            </div>
            <div className="metric dash-stat fam warn">
              {/* ⚠ THE KNOWN MISLABEL, AND IT IS STILL DELIBERATE. This tile is labelled
                  «قيد المراجعة» — a workflow state — and shows the count of MEDIUM-SEVERITY
                  alerts. DASH-4a fixed that for the dashboard; `96b7f29` scoped the fix
                  behind a `hideList` prop because this component also renders on جوّي · بحري
                  · فضائي and the owner's constraint is that nothing outside لوحة may change.
                  ⛔ DASH-6a REMOVED THE PROP AND NOT THE DECISION. The dashboard now owns
                  `MonitorConsole`, which counts the real triage state; this file is the
                  domain tabs' again, so the branch had nothing left to choose between and
                  the unconditional `sev.medium` IS what those three tabs already displayed.
                  Correcting it here would change them, which is exactly what DASH-6 forbids
                  — so it stays recorded (PROJECT_STATE §6.19) rather than silently carried. */}
              <span className="n">{num(sev.medium)}</span>
              <span className="l">{T.dashHome.tileMedium}</span>
            </div>
            <div className="metric dash-stat">
              <span className="n">{num(sorted.length)}</span>
              <span className="l">{T.dashHome.tileTotal}</span>
              <span className="dash-fresh">{T.dashHome.windowNote(data.window_hours)}</span>
            </div>
          </div>

          {/* ---- RANK-NAME: the badge legend ----
            * Sits between the tiles (which count two tiers) and the cards (which show at most
            * two words), because it is the only place the WHOLE scale appears. Collapsed by
            * default — the panel is already at DASH-2d″'s length gate.
            *
            * ⛔ Rows are ordered by SEVERITY_ORDER, not written out, so `لا تهديد` is last
            * here for the same reason it is last on the cards. */}
          <details className="sev-legend">
            <summary>{T.dashHome.legendToggle}</summary>
            <ul className="sl-list" aria-label={T.dashHome.legendAria}>
              {SEV_LEGEND_ORDER.map((k) => (
                <li key={k} className={`sl-row sev-${k}`}>
                  <b className="sl-word">{T.dashHome.severity[k] ?? k}</b>
                  <span className="sl-gloss">{T.dashHome.legendGloss[k]}</span>
                </li>
              ))}
            </ul>
            <p className="sl-note">{T.dashHome.legendChannel}</p>
          </details>

          {/* ---- the events ---- */}
          <div className="section-label dash-alerts-head">
            {/* ⛔ DASH-IDLE — the section title is an ELEMENT, not a bare text node: this head
                is a flex line, and a text-only lead child is invisible to the "nearest child
                on the container's line" measurement (G2), which then read the axis note as
                the row's start. Same pixels, measurable children. */}
            <span>{C.eventsSection}</span>
            <span className="dim">
              {" · "}
              {/* ⚠ WARNING 4 on the surface itself: the axis is a display order. */}
              {C.orderedByImportance}
            </span>
            {sorted.length > PANEL_CAP && (
              <button className="dash-cap-btn" onClick={() => setShowAll((v) => !v)}>
                {showAll ? T.dashHome.showTop(PANEL_CAP) : T.dashHome.showAll(sorted.length)}
              </button>
            )}
          </div>

          {orderedGroups.length === 0 ? (
            /* ⚠ 2c′'s lesson: "not observed" is not "does not exist". Say which one. */
            <div className="cc-empty">
              <div className="filter-note">{C.emptyForCountry(name)}</div>
              <div className="home-slot-note">{C.emptyNote}</div>
            </div>
          ) : (
            orderedGroups.map((g) => (
              <div className="alert-group" key={g.signalType}>
                <div className="ag-head">
                  <span className="ag-title">
                    {T.dashHome.signals[g.signalType] ?? g.signalType}
                    <span className="ag-count"> · {g.items.length}</span>
                  </span>
                  {g.hoist.header && (
                    <span className="ag-note" title={g.hoist.headerSource ?? undefined}>
                      {g.hoist.header}
                    </span>
                  )}
                </div>
                <ResultCards
                  items={g.cards}
                  selected={null}
                  onOpen={(key) => { const a = byKey.get(key); if (a) onOpenAlert(a); }}
                  empty={T.dashHome.empty}
                  aria={T.dashHome.signals[g.signalType] ?? g.signalType}
                />
              </div>
            ))
          )}

          <Caveat k="alerts" response={data.caveat} className="dash-caveat" />
        </>
      )}
    </div>
  );
}
