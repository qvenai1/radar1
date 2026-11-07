/**
 * ResultCards (REDESIGN) — the monitor workspace's results as a responsive card
 * grid, replacing the 340px-column lists. Entity-agnostic: callers map their own
 * summary shape (VesselSummary / AircraftSummary / … / AreaEntry / later Alert)
 * to CardItem[]; the fetch/resolution engines are untouched — this is presentation
 * only. Opening a card drives the same selection App already owns (and, in the
 * redesign, summons the on-demand map).
 */

import { T } from "../lib/strings";

export interface CardBadge {
  label: string;
  tone?: "high" | "warn" | "info";
}

/** UI-1: severity tier, rendered as visual WEIGHT (a leading accent rule) so urgency
 *  is legible before any text is read. Tokens only — no new colours. */
export type CardTone = "high" | "medium" | "low" | "info";

export interface CardItem {
  id: string;
  title: string;
  sub?: string | null;
  badges?: CardBadge[];
  // D5 alert cards carry two extra honesty lines (search/area cards omit them):
  //   note — the severity_basis (WHY this severity: keeps info/low honest)
  //   foot — the liveness/vintage (so a stale/frozen signal reads as its age)
  //   UI-1: `note` is now null on cards whose basis was hoisted to the group header.
  note?: string | null;
  foot?: string | null;
  /** UI-1: severity tier → the card's accent rule (search/area cards omit it) */
  tone?: CardTone;
  /**
   * DASH-CARD — the SECOND LAYER, present only on cards that have the Arabic register.
   *
   * ⛔ `caveat` is not optional within it and is never truncated: it is the project's
   * written commitment about what the signal does NOT mean, and a card that ranks a hull
   * without carrying it would be making the claim without the qualifier.
   *
   * Supplying this changes the card's ROOT ELEMENT (see below) — cards without it keep the
   * exact `<button>` markup the other three callers already render.
   */
  expand?: { why: string; caveat: string } | null;
  /** DASH-CARD — the always-visible meta line (registration flag · last seen). */
  meta?: string | null;
  /** DASH-CARD — the severity badge's ⓘ explainer, shown as a title on the badge. */
  toneHint?: string | null;
}

interface Props {
  items: CardItem[];
  selected: string | null;
  onOpen: (id: string) => void;
  empty: string;
  aria: string;
}

export default function ResultCards({ items, selected, onOpen, empty, aria }: Props) {
  if (items.length === 0) {
    return <div className="filter-note">{empty}</div>;
  }
  return (
    <div className="card-grid" role="list" aria-label={aria}>
      {items.map((it) => {
        const body = (
          <>
            <span className="rc-title">{it.title}</span>
            {it.sub ? <span className="rc-sub">{it.sub}</span> : null}
            {it.meta ? <span className="rc-meta">{it.meta}</span> : null}
            {it.badges && it.badges.length > 0 ? (
              <span className="rc-badges">
                {it.badges.map((b, i) => (
                  <span
                    key={i}
                    className={`badge rc-badge${b.tone ? " " + b.tone : ""}`}
                    title={i === 0 && it.toneHint ? it.toneHint : undefined}
                  >
                    {/* ⛔ DASH-IDLE — the label is an ELEMENT, not a bare text node: the badge is
                        an inline-flex track, and a text-only lead child is invisible to the
                        "nearest child on the line" measurement (G2), which then read the ⓘ
                        hint — offset by the label's width — as the badge's start. */}
                    <span>{b.label}</span>
                    {i === 0 && it.toneHint ? <i className="rc-hint" aria-hidden="true">ⓘ</i> : null}
                  </span>
                ))}
              </span>
            ) : null}
            {it.note ? <span className="rc-note">{it.note}</span> : null}
            {it.foot ? <span className="rc-foot">{it.foot}</span> : null}
          </>
        );

        /* ⛔ TWO ROOTS, ON PURPOSE. A disclosure inside the card's <button> would nest
           interactive elements, which is invalid and breaks keyboard traversal. So a card
           WITH a second layer becomes a <div> holding its own open-button plus a <details>,
           while a card without one keeps the exact <button> the other three callers (search,
           area, dashboard) already render — those are left byte-identical rather than
           migrated along for tidiness. */
        if (!it.expand) {
          return (
            <button
              key={it.id}
              type="button"
              role="listitem"
              className={`card result-card${it.tone ? ` sev-${it.tone}` : ""}`}
              aria-selected={it.id === selected}
              onClick={() => onOpen(it.id)}
            >
              {body}
              <span className="rc-open" aria-hidden="true">{T.entitySearch.showTrack}</span>
            </button>
          );
        }
        return (
          <div
            key={it.id}
            role="listitem"
            className={`card result-card rc-two${it.tone ? ` sev-${it.tone}` : ""}`}
            aria-selected={it.id === selected}
          >
            <button type="button" className="rc-main" onClick={() => onOpen(it.id)}>
              {body}
              <span className="rc-open" aria-hidden="true">{T.entitySearch.showTrack}</span>
            </button>
            {/* native <details>: keyboard- and screen-reader-correct with no state to own,
                and it collapses by default so the card's folded height is unchanged */}
            <details className="rc-more">
              <summary>{T.dashHome.whyLook}</summary>
              <div className="rc-why">{it.expand.why}</div>
              {/* ⛔ the caveat is rendered in full, always, whenever the layer is open */}
              <div className="rc-caveat">
                <span aria-hidden="true">⚠ </span>{it.expand.caveat}
              </div>
            </details>
          </div>
        );
      })}
    </div>
  );
}
