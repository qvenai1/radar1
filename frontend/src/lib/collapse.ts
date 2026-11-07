/**
 * useCollapsed (UI-2 step 2) — per-section collapse state, persisted.
 *
 * WHY A HOOK AND NOT A COMPONENT. UI-2 deliberately does NOT extract <AnalysisPanel>
 * (that is UI-3's primitive), so each analysis panel keeps its own markup. But the
 * collapse state cannot live in App: App does not own these panels' state — each one
 * owns its own fetch and its own loading/error/empty state — and lifting five booleans
 * into App would be exactly the state relocation this phase forbids. So the state stays
 * panel-local, and only the PERSISTENCE logic is shared here. No markup, no props, no
 * structure: UI-3's <AnalysisPanel> absorbs this call unchanged.
 *
 * Reuses the existing `scope-*` localStorage convention (scope-gnss, scope-dark,
 * scope-dark-region, scope-gnss-variant) so the analyst's panel layout persists the
 * same way their map layers already do — someone who works gaps daily opens that
 * section once and it stays open.
 */

import { useCallback, useState } from "react";

const PREFIX = "scope-collapse-";

/**
 * @param key   stable section id (becomes `scope-collapse-<key>`)
 * @param initiallyCollapsed default when nothing is stored — UI-2 ships `true` for
 *        every analysis section, so the panel opens on the live picture and analysis
 *        is opt-in.
 * @returns [collapsed, toggle]
 */
export function useCollapsed(
  key: string,
  initiallyCollapsed = true,
): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(PREFIX + key);
      // an unset key falls back to the default; only an explicit value overrides it
      return v === null ? initiallyCollapsed : v === "1";
    } catch {
      return initiallyCollapsed; // private mode / storage disabled — never throw
    }
  });

  const toggle = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(PREFIX + key, next ? "1" : "0");
      } catch {
        /* storage unavailable — the session still toggles, it just won't persist */
      }
      return next;
    });
  }, [key]);

  return [collapsed, toggle];
}
