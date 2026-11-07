/**
 * Flag — the country glyph for the seven-country AOI picker.
 *
 * ⛔ SYRIA IS DRAWN, NOT DERIVED, AND THAT IS THE WHOLE POINT OF THIS FILE (FLAG-1).
 * Every other AOI country still comes from the Unicode regional-indicator emoji, which
 * DASH_PRE §5 chose for costing zero files and zero licence. Syria cannot use it: the
 * country readopted the green-white-black flag with three red stars in 2025, and which
 * of the two flags 🇸🇾 paints is decided by the VIEWER'S emoji font vintage — a thing
 * this app does not control and cannot feature-detect. Measured on the dev host: Noto
 * Color Emoji 2.048 renders the current flag correctly; any font predating the change
 * renders the superseded red-white-black two-star flag instead. "Correct on the machine
 * we happened to build on" does not satisfy a requirement that the old flag never show,
 * so the one country where the answer must be certain is shipped as geometry.
 *
 * The SVG is inline rather than a file in public/ so it cannot 404, needs no build step
 * and no fetch, and stays inside the component that documents why it exists. Geometry is
 * the 2:3 independence-flag spec: three equal bands, three five-pointed stars centred in
 * the white band at 1/4, 1/2 and 3/4 of the width. Colours are the Pan-Arab values
 * (#007A3D / #CE1126) — ⚠ literals here are deliberate and are NOT a THEME-2 violation:
 * a national flag is not themed chrome, and repainting it with the identity would make it
 * a different flag.
 */

/** The seven AOI countries render at the picker's 12px type size; 2:3 keeps 18×12. */
const H = 12;

export default function Flag({ iso }: { iso: string }) {
  const cc = iso.toUpperCase();

  if (cc === "SY") {
    return (
      <svg
        className="cc-flag-svg"
        viewBox="0 0 90 60"
        height={H}
        width={H * 1.5}
        role="img"
        aria-label="سوريا"
      >
        <rect width="90" height="20" y="0" fill="#007A3D" />
        <rect width="90" height="20" y="20" fill="#FFFFFF" />
        <rect width="90" height="20" y="40" fill="#000000" />
        <polygon fill="#CE1126" points="22.500,23.000 24.072,27.837 29.157,27.837 25.043,30.826 26.614,35.663 22.500,32.674 18.386,35.663 19.957,30.826 15.843,27.837 20.928,27.837" />
        <polygon fill="#CE1126" points="45.000,23.000 46.572,27.837 51.657,27.837 47.543,30.826 49.114,35.663 45.000,32.674 40.886,35.663 42.457,30.826 38.343,27.837 43.428,27.837" />
        <polygon fill="#CE1126" points="67.500,23.000 69.072,27.837 74.157,27.837 70.043,30.826 71.614,35.663 67.500,32.674 63.386,35.663 64.957,30.826 60.843,27.837 65.928,27.837" />
      </svg>
    );
  }

  return <span className="cc-flag">{isoFlagEmoji(cc)}</span>;
}

/** The regional-indicator pair for an alpha-2 code. Kept local: the string-context
 *  caller in alertCards.ts has its own copy with its own SY policy documented there. */
function isoFlagEmoji(cc: string): string {
  return [...cc].map((c) => String.fromCodePoint(0x1f1e6 + c.charCodeAt(0) - 65)).join("");
}
