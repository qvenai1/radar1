/**
 * FieldGrid (UI-3) — the shared key/value field grid used by every detail panel.
 *
 * The `.strip-grid` / `.strip-cell` / `.k` / `.v` markup was hand-rolled in 9 places
 * across 6 components, always identically: build `Array<[label, value]>`, map it to a
 * cell, print the label small-caps above the value in the data font. The CSS contract
 * already existed and was already shared — only the component was missing.
 *
 * Presentation only, and deliberately dumb: it owns no state and formats nothing.
 * Callers keep building their own cell list, because WHICH fields a panel shows (and
 * how each value is formatted) is domain knowledge that belongs to the panel.
 *
 * Extraction is behaviour-preserving: the emitted DOM is byte-identical to what each
 * call site emitted before, so no stylesheet changes and no visual diff.
 */

/** one cell: [label, value]. Values arrive pre-formatted — see lib/format. */
export type Field = [string, string];

interface Props {
  cells: Field[];
  /**
   * Optional per-cell tooltip, keyed off the label. FlightStrip is the only caller
   * that needs it (it marks an implausible baro reading), so it stays opt-in rather
   * than widening `Field` for every other panel.
   */
  cellTitle?: (label: string) => string | undefined;
}

export default function FieldGrid({ cells, cellTitle }: Props) {
  return (
    <div className="strip-grid">
      {cells.map(([k, v]) => (
        <div className="strip-cell" key={k} title={cellTitle?.(k)}>
          <div className="k">{k}</div>
          <div className="v">{v}</div>
        </div>
      ))}
    </div>
  );
}
