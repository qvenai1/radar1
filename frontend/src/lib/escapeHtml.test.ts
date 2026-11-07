import { describe, expect, it } from "vitest";
import { esc } from "./escapeHtml";

describe("esc — the XSS guard on every Leaflet HTML sink", () => {
  it("neutralizes a scripted vessel name (the attack the review named)", () => {
    expect(esc('<img src=x onerror=alert(1)>')).toBe(
      "&lt;img src=x onerror=alert(1)&gt;");
    expect(esc('<script>fetch("/api/v1/live")</script>')).not.toContain("<script>");
  });
  it("escapes all five metacharacters and nothing else", () => {
    expect(esc(`&<>"'`)).toBe("&amp;&lt;&gt;&quot;&#39;");
    expect(esc("MSC OSCAR — طرطوس 42.1kn")).toBe("MSC OSCAR — طرطوس 42.1kn");
  });
  it("stringifies non-strings the way the popups use it", () => {
    expect(esc(367596480)).toBe("367596480");
    expect(esc(null)).toBe("null"); // callers guard null; esc must still not throw
  });
});
