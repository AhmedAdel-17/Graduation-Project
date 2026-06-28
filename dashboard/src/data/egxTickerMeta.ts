// Ticker metadata for the EGX universe — English + Arabic names, sector.
// Single source of truth for the picker UI; backend ticker IDs stay as `XYZ.CA`.

export type EgxSector =
  | "Banks"
  | "Real Estate"
  | "Industry & Materials"
  | "Telecom & Tech"
  | "Financial Services"
  | "Food & Beverage"
  | "Other";

export interface EgxTickerMeta {
  symbol: string;        // without .CA (e.g. "COMI")
  apiTicker: string;     // with .CA (e.g. "COMI.CA") — sent to backend
  nameEn: string;        // English company name
  nameAr: string;        // Arabic company name
  sector: EgxSector;
  // Logo domain — fed to Clearbit's free logo API
  // (https://logo.clearbit.com/<domain>). Unknown / 404 domains fall back to
  // the ticker initials in the UI.
  logoDomain?: string;
}

// Source: EGX-30 listings cross-referenced against
// data/egx30_fundamentals/_slug_map.json (the company identities the fundamentals
// were actually fetched under). Must mirror tradingagents/default_config.EGX_TICKERS.
// If a ticker is missing here it will still appear under "Other" with the symbol as name.
export const EGX_TICKER_META: EgxTickerMeta[] = [
  // ── Banks ────────────────────────────────────────────────────────────────
  { symbol: "COMI", apiTicker: "COMI.CA", nameEn: "Commercial International Bank",        nameAr: "البنك التجاري الدولي",            sector: "Banks", logoDomain: "cibeg.com" },
  { symbol: "ADIB", apiTicker: "ADIB.CA", nameEn: "Abu Dhabi Islamic Bank – Egypt",        nameAr: "مصرف أبوظبي الإسلامي – مصر",       sector: "Banks", logoDomain: "adib.eg" },

  // ── Real Estate ──────────────────────────────────────────────────────────
  { symbol: "TMGH", apiTicker: "TMGH.CA", nameEn: "T M G Holding",                         nameAr: "مجموعة طلعت مصطفى القابضة",        sector: "Real Estate", logoDomain: "talaatmoustafa.com" },
  { symbol: "HELI", apiTicker: "HELI.CA", nameEn: "Misr El Gadida for Housing & Development", nameAr: "مصر الجديدة للإسكان والتعمير",  sector: "Real Estate", logoDomain: "heliopoliscompany.com" },
  { symbol: "PHDC", apiTicker: "PHDC.CA", nameEn: "Palm Hills Development",                nameAr: "بالم هيلز للتعمير",                sector: "Real Estate", logoDomain: "palmhillsdevelopments.com" },
  { symbol: "ORAS", apiTicker: "ORAS.CA", nameEn: "Orascom Construction",                  nameAr: "أوراسكوم كونستراكشن",              sector: "Real Estate", logoDomain: "orascom.com" },
  { symbol: "EMFD", apiTicker: "EMFD.CA", nameEn: "Emaar Misr for Development",            nameAr: "إعمار مصر للتنمية",                sector: "Real Estate", logoDomain: "emaarmisr.com" },
  { symbol: "ORHD", apiTicker: "ORHD.CA", nameEn: "Orascom Hotels and Development",        nameAr: "أوراسكوم للفنادق والتنمية",        sector: "Real Estate", logoDomain: "orascomdh.com" },

  // ── Industry & Materials ─────────────────────────────────────────────────
  { symbol: "ABUK", apiTicker: "ABUK.CA", nameEn: "Abu Qir Fertilizers",                   nameAr: "أبو قير للأسمدة",                  sector: "Industry & Materials", logoDomain: "abuqir.com" },
  { symbol: "EAST", apiTicker: "EAST.CA", nameEn: "Eastern Tobacco",                       nameAr: "الشرقية للدخان",                   sector: "Industry & Materials", logoDomain: "easterncompany-eg.com" },
  { symbol: "EGAL", apiTicker: "EGAL.CA", nameEn: "Egypt Aluminum",                        nameAr: "مصر للألومنيوم",                    sector: "Industry & Materials", logoDomain: "egyptalum.com" },
  { symbol: "EGCH", apiTicker: "EGCH.CA", nameEn: "Egyptian Chemical Industries (Kima)",   nameAr: "كيما – الصناعات الكيماوية المصرية", sector: "Industry & Materials", logoDomain: "kima-egy.com" },
  { symbol: "ORWE", apiTicker: "ORWE.CA", nameEn: "Oriental Weavers",                      nameAr: "النساجون الشرقيون",                sector: "Industry & Materials", logoDomain: "orientalweavers.com" },
  { symbol: "AMOC", apiTicker: "AMOC.CA", nameEn: "Alexandria Mineral Oils (AMOC)",        nameAr: "الإسكندرية للزيوت المعدنية",       sector: "Industry & Materials", logoDomain: "amoc.com.eg" },
  { symbol: "MCQE", apiTicker: "MCQE.CA", nameEn: "Misr Cement (Qena)",                    nameAr: "مصر للأسمنت – قنا",                sector: "Industry & Materials" },
  { symbol: "ARCC", apiTicker: "ARCC.CA", nameEn: "Arabian Cement",                        nameAr: "الأسمنت العربية",                  sector: "Industry & Materials", logoDomain: "arabiancement.com" },
  { symbol: "ISPH", apiTicker: "ISPH.CA", nameEn: "Ibnsina Pharma",                        nameAr: "ابن سينا فارما",                   sector: "Industry & Materials", logoDomain: "ibnsina-pharma.com" },
  { symbol: "RMDA", apiTicker: "RMDA.CA", nameEn: "Tenth of Ramadan Pharma (Rameda)",      nameAr: "العاشر من رمضان للأدوية (راميدا)", sector: "Industry & Materials", logoDomain: "rameda.com" },
  { symbol: "GBCO", apiTicker: "GBCO.CA", nameEn: "GB Corp (GB Auto)",                     nameAr: "جي بي كورب (جي بي أوتو)",          sector: "Industry & Materials", logoDomain: "ghabbour.com" },

  // ── Telecom & Tech ───────────────────────────────────────────────────────
  { symbol: "ETEL", apiTicker: "ETEL.CA", nameEn: "Telecom Egypt",                         nameAr: "المصرية للاتصالات",                sector: "Telecom & Tech", logoDomain: "te.eg" },
  { symbol: "FWRY", apiTicker: "FWRY.CA", nameEn: "Fawry for Banking & Payment Technology",nameAr: "فوري لتكنولوجيا الدفع الإلكتروني", sector: "Telecom & Tech", logoDomain: "fawry.com" },
  { symbol: "EFIH", apiTicker: "EFIH.CA", nameEn: "e-finance for Digital & Financial Investments", nameAr: "إي فاينانس للاستثمارات المالية", sector: "Telecom & Tech", logoDomain: "efinance.com.eg" },
  { symbol: "RAYA", apiTicker: "RAYA.CA", nameEn: "Raya Holding",                          nameAr: "راية القابضة",                     sector: "Telecom & Tech", logoDomain: "rayacorp.com" },
  { symbol: "OIH",  apiTicker: "OIH.CA",  nameEn: "Orascom Investment Holding",            nameAr: "أوراسكوم للاستثمار القابضة",       sector: "Telecom & Tech" },

  // ── Financial Services ───────────────────────────────────────────────────
  { symbol: "HRHO", apiTicker: "HRHO.CA", nameEn: "EFG Hermes Holding",                    nameAr: "المجموعة المالية هيرميس القابضة",  sector: "Financial Services", logoDomain: "efghermes.com" },
  { symbol: "BTFH", apiTicker: "BTFH.CA", nameEn: "Beltone Financial Holding",             nameAr: "بلتون المالية القابضة",            sector: "Financial Services", logoDomain: "beltonefinancial.com" },
  { symbol: "CCAP", apiTicker: "CCAP.CA", nameEn: "Qalaa Holdings",                        nameAr: "القلعة القابضة",                   sector: "Financial Services", logoDomain: "qalaaholdings.com" },
  { symbol: "VLMR", apiTicker: "VLMR.CA", nameEn: "Valmore Holding (ex-Egypt Kuwait Holding)", nameAr: "فالمور القابضة (مصر الكويت سابقًا)", sector: "Financial Services" },

  // ── Food & Beverage ──────────────────────────────────────────────────────
  { symbol: "JUFO", apiTicker: "JUFO.CA", nameEn: "Juhayna Food Industries",               nameAr: "جهينة للصناعات الغذائية",          sector: "Food & Beverage", logoDomain: "juhayna.com" },
  { symbol: "EFID", apiTicker: "EFID.CA", nameEn: "Edita Food Industries",                 nameAr: "إديتا للصناعات الغذائية",          sector: "Food & Beverage", logoDomain: "edita.com.eg" },
];

// Quick lookup: apiTicker (with .CA) → meta
export const EGX_TICKER_BY_API: Record<string, EgxTickerMeta> = Object.fromEntries(
  EGX_TICKER_META.map((t) => [t.apiTicker, t])
);

export const EGX_SECTOR_ORDER: EgxSector[] = [
  "Banks",
  "Real Estate",
  "Industry & Materials",
  "Telecom & Tech",
  "Financial Services",
  "Food & Beverage",
  "Other",
];

export function getTickerMeta(apiTicker: string): EgxTickerMeta {
  const meta = EGX_TICKER_BY_API[apiTicker];
  if (meta) return meta;
  const symbol = apiTicker.replace(/\.CA$/i, "");
  return {
    symbol,
    apiTicker,
    nameEn: symbol,
    nameAr: symbol,
    sector: "Other",
  };
}
