/**
 * تنبيهات الطوارئ: سكواك 7500 (خطف) / 7600 (عطل اتصالات) / 7700 (طوارئ).
 * يظهر فوق بلاطات الإحصاء فقط عند وجود حالة نشطة؛ الضغط يختار الطائرة.
 */

import type { StateVector } from "../api/types";
import { callsign } from "../lib/format";
import { T } from "../lib/strings";

export const EMERGENCY_SQUAWKS: Record<string, string> = {
  "7500": T.alerts.hijack,
  "7600": T.alerts.radioFail,
  "7700": T.alerts.emergency,
};

export function findEmergencies(states: StateVector[]): StateVector[] {
  return states.filter((s) => s.squawk in EMERGENCY_SQUAWKS);
}

interface Props {
  emergencies: StateVector[];
  onSelect: (icao24: string) => void;
}

export default function AlertBar({ emergencies, onSelect }: Props) {
  if (emergencies.length === 0) return null;
  return (
    <div className="alert-bar" role="alert" aria-label={T.alerts.aria}>
      <div className="alert-title">⚠ {T.alerts.title}</div>
      {emergencies.map((s) => (
        <button key={s.icao24} className="alert-row" onClick={() => onSelect(s.icao24)}>
          <span className="cs">{callsign(s.callsign)}</span>
          <span className="sq">{s.squawk}</span>
          <span className="meaning">{EMERGENCY_SQUAWKS[s.squawk]}</span>
        </button>
      ))}
    </div>
  );
}
