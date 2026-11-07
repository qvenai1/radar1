/**
 * Search + filters. The values feed /api/v1/search (server-side filtering of
 * the CURRENT snapshot — contract) via App; this component is presentation.
 * Altitude inputs are in FEET for the operator and converted to meters in
 * App through lib/format constants (the API takes meters).
 */

import { T } from "../lib/strings";

export interface Filters {
  q: string;
  onGround: "any" | "air" | "ground";
  minAltFt: string; // raw input; "" = unset
  maxAltFt: string;
}

export const EMPTY_FILTERS: Filters = { q: "", onGround: "any", minAltFt: "", maxAltFt: "" };

export function filtersActive(f: Filters): boolean {
  return f.q.trim() !== "" || f.onGround !== "any" || f.minAltFt !== "" || f.maxAltFt !== "";
}

interface Props {
  filters: Filters;
  onChange: (f: Filters) => void;
  showing: number;
  total: number;
}

const GROUND_LABELS = { any: T.search.any, air: T.search.air, ground: T.search.ground } as const;

export default function SearchBar({ filters, onChange, showing, total }: Props) {
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  const active = filtersActive(filters);
  return (
    <div>
      <div className="section-label">{T.search.section}</div>
      <div className="search-row">
        <input
          className="input"
          type="text"
          placeholder={T.search.placeholder}
          value={filters.q}
          onChange={(e) => set({ q: e.target.value })}
          aria-label={T.search.ariaQuery}
        />
      </div>
      <div className="search-filters">
        <div className="seg segment" role="group" aria-label={T.search.ariaGroundGroup}>
          {(["any", "air", "ground"] as const).map((v) => (
            <button
              key={v}
              aria-pressed={filters.onGround === v}
              onClick={() => set({ onGround: v })}
            >
              {GROUND_LABELS[v]}
            </button>
          ))}
        </div>
        <input
          className="input"
          type="number"
          placeholder={T.search.minFt}
          value={filters.minAltFt}
          onChange={(e) => set({ minAltFt: e.target.value })}
          aria-label={T.search.ariaMin}
        />
        <input
          className="input"
          type="number"
          placeholder={T.search.maxFt}
          value={filters.maxAltFt}
          onChange={(e) => set({ maxAltFt: e.target.value })}
          aria-label={T.search.ariaMax}
        />
      </div>
      <div className="filter-note" aria-live="polite">
        {active ? T.search.showing(showing, total) : T.search.total(total)}
        {active && (
          <>
            {" · "}
            <button style={{ border: "none", padding: 0, textDecoration: "underline" }} onClick={() => onChange(EMPTY_FILTERS)}>
              {T.search.clear}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
