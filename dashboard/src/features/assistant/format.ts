// Locale-aware number/currency formatting for the Portfolio Assistant.
//
// Bilingual by design (design §10): Arabic uses the `ar-EG` locale (Eastern
// Arabic numerals + ج.م.‏ currency), English uses `en-EG`. Money is always EGP.
// Percentages in the wire contract come in two shapes — keep them straight:
//   * weight_pct / cash_pct / *_weight_pct / metric cash_pct  → already a percent
//     (e.g. 16.0 means 16%). Use `fmtPct(v)`.
//   * vol / hhi / confidence / expected_return_view            → a fraction in
//     [0,1] (e.g. 0.28 means 28%). Use `fmtPct(v, { frac: true })` or `fmtNum`.

export type Loc = "en" | "ar";

const localeTag = (l: Loc) => (l === "ar" ? "ar-EG" : "en-EG");

export function fmtEgp(value: number, loc: Loc = "en", digits = 0): string {
  return new Intl.NumberFormat(localeTag(loc), {
    style: "currency",
    currency: "EGP",
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  }).format(value);
}

export function fmtNum(value: number, loc: Loc = "en", digits = 2): string {
  return new Intl.NumberFormat(localeTag(loc), {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  }).format(value);
}

export function fmtInt(value: number, loc: Loc = "en"): string {
  return new Intl.NumberFormat(localeTag(loc), { maximumFractionDigits: 0 }).format(value);
}

/** Format a percentage. Pass `frac:true` when the input is a [0,1] fraction. */
export function fmtPct(
  value: number,
  { loc = "en", frac = false, digits = 1, signed = false }: {
    loc?: Loc; frac?: boolean; digits?: number; signed?: boolean;
  } = {},
): string {
  const pct = frac ? value * 100 : value;
  const body = new Intl.NumberFormat(localeTag(loc), {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(pct);
  return `${body}%`;
}

/** Signed fraction → percent, for deltas (e.g. vol −0.03 → "−3.0%"). */
export function fmtDeltaPct(value: number, loc: Loc = "en", frac = false, digits = 1): string {
  return fmtPct(value, { loc, frac, digits, signed: true });
}
