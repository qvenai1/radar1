/**
 * TimeWindowPicker — the ONE shared time-window control, used across ALL entity
 * searches AND area-search. D4 mirrored these presets inline in AreaPanel; this
 * generalises that pattern into a single component (the extensible-shell discipline —
 * no per-entity one-off).
 *
 * Presentation only: the caller owns the window value (hours) and passes it to each
 * endpoint's EXISTING time param — airport `hours`, the vessel/aircraft track
 * since/until|from/to, and area since/until. Presets hour / 6h / 24h / 7d + a custom
 * hours input clamped to `max`. `disabled` + `note` cover an entity that has no
 * time-bounded data to window (satellite — no stored fixes; the ground-track is one
 * orbital period), keeping the control present but honest.
 */

import { T } from "../lib/strings";

export const WINDOW_PRESETS = [1, 6, 24, 168] as const;

interface Props {
  value: number; // hours
  onChange: (h: number) => void;
  max?: number; // clamp for the custom input (default 168h = 7d, the unified ceiling)
  disabled?: boolean;
  note?: string | null;
}

export default function TimeWindowPicker({ value, onChange, max = 168, disabled, note }: Props) {
  const W = T.timeWindow;
  const isPreset = (WINDOW_PRESETS as readonly number[]).includes(value);
  return (
    <div className={`time-window${disabled ? " disabled" : ""}`}>
      <div className="section-label">{W.label}</div>
      <div className="seg time-window-seg" role="group" aria-label={W.label}>
        {WINDOW_PRESETS.map((h) => (
          <button
            key={h}
            aria-pressed={value === h}
            disabled={disabled}
            onClick={() => onChange(h)}
          >
            {T.dashboard.windows[String(h)]}
          </button>
        ))}
        <input
          type="number"
          min={1}
          max={max}
          step={1}
          className="input tw-custom"
          aria-label={W.customAria}
          placeholder={W.custom}
          value={isPreset ? "" : String(value)}
          disabled={disabled}
          onChange={(e) => {
            const n = Math.round(Number(e.target.value));
            if (Number.isFinite(n) && n >= 1) onChange(Math.min(n, max));
          }}
        />
      </div>
      {note ? <div className="layer-hint">{note}</div> : null}
    </div>
  );
}
