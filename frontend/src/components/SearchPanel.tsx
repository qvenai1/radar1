/**
 * SearchPanel — the reusable shell of the search/monitoring tab, deliberately
 * ENTITY-AGNOSTIC so E2 (aircraft), E3 (satellite), E4 (airport) plug in
 * without a rebuild.
 *
 * The extensibility seam (the key E1 design decision):
 *  - `SearchEntity` is a union with ONE member today ("vessel"). E2–E4 add
 *    "aircraft" | "satellite" | "airport" to the union and to the `entities`
 *    array the caller passes — the entity segment renders them automatically.
 *  - This shell owns only what every entity shares: the entity-type segment,
 *    the (debounced-in-App) query input, and the results/detail SLOT (children).
 *    It hardcodes NO vessel fields.
 *  - The per-entity result list + detail live outside this shell and are passed as
 *    `children`, so a vessel's summary shape never leaks here. Adding an entity = add
 *    its branch in App (fetch + list + detail) and its label — NOT touching this shell.
 *    (The list side is now the shared `ResultCards` grid; the per-entity `*SearchList`
 *    components it replaced were deleted in UI-3 after the redesign orphaned them.)
 *
 * Presentation only (like SearchBar): the query debounce + fetch live in App.
 */

import { T } from "../lib/strings";

/** Searchable entity types. E1 vessel → E2 aircraft → E3 satellite → E4 airport. */
export type SearchEntity = "vessel" | "aircraft" | "satellite" | "airport";

interface Props {
  entity: SearchEntity;
  /** entity types offered in the segment (E1: just ["vessel"]) */
  entities: readonly SearchEntity[];
  onEntity: (e: SearchEntity) => void;
  query: string;
  onQuery: (q: string) => void;
  loading: boolean;
  /** match count for the current query, or null before any query is typed */
  count: number | null;
  children: React.ReactNode;
}

export default function SearchPanel({
  entity, entities, onEntity, query, onQuery, loading, count, children,
}: Props) {
  const E = T.entitySearch;
  return (
    <>
      <div>
        <div className="section-label">{E.section}</div>
        {entities.length > 1 && (
          <div className="seg segment entity-seg" role="group" aria-label={E.entityAria}>
            {entities.map((e) => (
              <button key={e} aria-pressed={entity === e} onClick={() => onEntity(e)}>
                {E.entities[e] ?? e}
              </button>
            ))}
          </div>
        )}
        <div className="search-row">
          <input
            className="input"
            type="text"
            placeholder={E.placeholder}
            value={query}
            onChange={(ev) => onQuery(ev.target.value)}
            aria-label={E.ariaQuery}
            autoFocus
          />
        </div>
        <div className="filter-note" aria-live="polite">
          {loading ? E.loading : query.trim() === "" ? E.prompt : count == null ? "" : E.count(count)}
        </div>
      </div>
      {children}
    </>
  );
}
