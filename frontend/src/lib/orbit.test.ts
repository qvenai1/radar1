/**
 * Sub-point parity with the backend (tech-stack review finding 2).
 *
 * The backend derives stored orbit class / pass windows with Python sgp4
 * (backend/passes_derive.py); the browser renders live positions with
 * satellite.js. Their agreement used to be asserted by a docstring only.
 * This test pins THIS side to the same independent reference the Python
 * side is pinned to — the skyfield-verified (lat, lon) triplet in
 * tests/test_passes.py::test_subpoint_matches_reference, same ISS TLE,
 * same epochs, same 0.01° tolerance. If either implementation drifts from
 * the reference, its own suite fails; they can no longer drift apart
 * silently.
 *
 * ⛔ Keep these constants byte-identical to tests/test_passes.py.
 */
import { describe, expect, it } from "vitest";
import { makeSatRec, propagate } from "./orbit";

const ISS_L1 = "1 25544U 98067A   26194.12129675  .00004316  00000+0  86456-4 0  9991";
const ISS_L2 = "2 25544  51.6304 171.7447 0006685 289.3803  70.6462 15.48996109575778";

// minutes after TLE epoch -> skyfield-verified (lat, lon)
const REF: Array<[number, number, number]> = [
  [0, -0.0, -162.9325],
  [30, 44.8175, -42.0299],
  [90, -8.8518, 167.1536],
];

const UNIX_EPOCH_JD = 2440587.5;

describe("satellite.js sub-point matches the skyfield reference", () => {
  const sat = makeSatRec({
    norad_id: 25544, name: "ISS (ZARYA)", tle1: ISS_L1, tle2: ISS_L2, epoch: null,
  });
  it("propagates the reference TLE at all three epochs within 0.01°", () => {
    expect(sat).not.toBeNull();
    const jdEpoch = (sat!.rec as unknown as { jdsatepoch: number }).jdsatepoch;
    expect(jdEpoch).toBeGreaterThan(2400000); // sanity: a real Julian date
    for (const [dm, rlat, rlon] of REF) {
      const when = new Date((jdEpoch - UNIX_EPOCH_JD + dm / 1440) * 86400000);
      const fix = propagate(sat!, when);
      expect(fix, `propagation failed at +${dm}min`).not.toBeNull();
      expect(Math.abs(fix!.lat - rlat), `lat at +${dm}min`).toBeLessThan(0.01);
      expect(Math.abs(fix!.lon - rlon), `lon at +${dm}min`).toBeLessThan(0.01);
    }
  });
});
