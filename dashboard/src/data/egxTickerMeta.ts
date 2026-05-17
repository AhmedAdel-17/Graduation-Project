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

// Source: EGX listings + Mubasher/Investing.com cross-reference. If a ticker
// is missing here it will still appear under "Other" with the symbol as name.
export const EGX_TICKER_META: EgxTickerMeta[] = [
  // ── Banks ────────────────────────────────────────────────────────────────
  { symbol: "COMI", apiTicker: "COMI.CA", nameEn: "Commercial International Bank",        nameAr: "البنك التجاري الدولي",            sector: "Banks", logoDomain: "cibeg.com" },
  { symbol: "ADIB", apiTicker: "ADIB.CA", nameEn: "Abu Dhabi Islamic Bank – Egypt",        nameAr: "مصرف أبوظبي الإسلامي – مصر",       sector: "Banks", logoDomain: "adib.eg" },
  { symbol: "CIEB", apiTicker: "CIEB.CA", nameEn: "Crédit Agricole Egypt",                 nameAr: "كريدي أجريكول مصر",                sector: "Banks", logoDomain: "ca-egypt.com" },
  { symbol: "EXPA", apiTicker: "EXPA.CA", nameEn: "Export Development Bank of Egypt",      nameAr: "بنك تنمية الصادرات المصري",        sector: "Banks", logoDomain: "edbebank.com" },
  { symbol: "HDBK", apiTicker: "HDBK.CA", nameEn: "Housing & Development Bank",            nameAr: "بنك التعمير والإسكان",             sector: "Banks", logoDomain: "hdb-egy.com" },
  { symbol: "QNBA", apiTicker: "QNBA.CA", nameEn: "QNB Al Ahli",                           nameAr: "بنك قطر الوطني الأهلي",            sector: "Banks", logoDomain: "qnbalahli.com" },
  { symbol: "SAUD", apiTicker: "SAUD.CA", nameEn: "Suez Canal Bank",                       nameAr: "بنك قناة السويس",                  sector: "Banks", logoDomain: "scbank.com.eg" },

  // ── Real Estate ──────────────────────────────────────────────────────────
  { symbol: "TMGH", apiTicker: "TMGH.CA", nameEn: "Talaat Moustafa Group Holding",         nameAr: "مجموعة طلعت مصطفى القابضة",        sector: "Real Estate", logoDomain: "talaatmoustafa.com" },
  { symbol: "HELI", apiTicker: "HELI.CA", nameEn: "Heliopolis Housing & Development",      nameAr: "مصر الجديدة للإسكان والتعمير",     sector: "Real Estate", logoDomain: "heliopoliscompany.com" },
  { symbol: "PHDC", apiTicker: "PHDC.CA", nameEn: "Palm Hills Developments",               nameAr: "بالم هيلز للتعمير",                sector: "Real Estate", logoDomain: "palmhillsdevelopments.com" },
  { symbol: "OCDI", apiTicker: "OCDI.CA", nameEn: "SODIC (Six of October Development)",    nameAr: "سوديك (مدينة 6 أكتوبر للتنمية)",   sector: "Real Estate", logoDomain: "sodic.com" },
  { symbol: "ORAS", apiTicker: "ORAS.CA", nameEn: "Orascom Construction",                  nameAr: "أوراسكوم كونستراكشن",              sector: "Real Estate", logoDomain: "orascom.com" },
  { symbol: "EMFD", apiTicker: "EMFD.CA", nameEn: "Emaar Misr for Development",            nameAr: "إعمار مصر للتنمية",                sector: "Real Estate", logoDomain: "emaarmisr.com" },
  { symbol: "ORHD", apiTicker: "ORHD.CA", nameEn: "Orascom Development Egypt",             nameAr: "أوراسكوم للتنمية – مصر",           sector: "Real Estate", logoDomain: "orascomdh.com" },

  // ── Industry & Materials ─────────────────────────────────────────────────
  { symbol: "EAST", apiTicker: "EAST.CA", nameEn: "Eastern Company (tobacco)",             nameAr: "الشرقية للدخان",                   sector: "Industry & Materials", logoDomain: "easterncompany-eg.com" },
  { symbol: "ESRS", apiTicker: "ESRS.CA", nameEn: "Ezz Steel",                             nameAr: "حديد عز",                          sector: "Industry & Materials", logoDomain: "ezzsteel.com" },
  { symbol: "SWDY", apiTicker: "SWDY.CA", nameEn: "Elsewedy Electric",                     nameAr: "السويدي إليكتريك",                 sector: "Industry & Materials", logoDomain: "elsewedyelectric.com" },
  { symbol: "ABUK", apiTicker: "ABUK.CA", nameEn: "Abu Qir Fertilizers",                   nameAr: "أبو قير للأسمدة",                  sector: "Industry & Materials", logoDomain: "abuqir.com" },
  { symbol: "MFPC", apiTicker: "MFPC.CA", nameEn: "Misr Fertilizers Production (Mopco)",   nameAr: "موبكو – مصر لإنتاج الأسمدة",       sector: "Industry & Materials", logoDomain: "mopco-eg.com" },
  { symbol: "EGAL", apiTicker: "EGAL.CA", nameEn: "Egypt Aluminium",                       nameAr: "مصر للألومنيوم",                    sector: "Industry & Materials", logoDomain: "egyptalum.com" },
  { symbol: "EGCH", apiTicker: "EGCH.CA", nameEn: "Egyptian Chemical Industries (Kima)",   nameAr: "كيما – الصناعات الكيماوية المصرية", sector: "Industry & Materials", logoDomain: "kima-egy.com" },
  { symbol: "EFIC", apiTicker: "EFIC.CA", nameEn: "Egyptian Financial & Industrial",       nameAr: "المالية والصناعية المصرية",        sector: "Industry & Materials", logoDomain: "efic.com.eg" },
  { symbol: "ORWE", apiTicker: "ORWE.CA", nameEn: "Oriental Weavers",                      nameAr: "النساجون الشرقيون",                sector: "Industry & Materials", logoDomain: "orientalweavers.com" },
  { symbol: "AMOC", apiTicker: "AMOC.CA", nameEn: "Alexandria Mineral Oils (AMOC)",        nameAr: "الإسكندرية للزيوت المعدنية",       sector: "Industry & Materials", logoDomain: "amoc.com.eg" },
  { symbol: "SKPC", apiTicker: "SKPC.CA", nameEn: "Sidi Kerir Petrochemicals",             nameAr: "سيدي كرير للبتروكيماويات",         sector: "Industry & Materials", logoDomain: "sidpec.com" },
  { symbol: "ISPH", apiTicker: "ISPH.CA", nameEn: "Ibnsina Pharma",                        nameAr: "ابن سينا فارما",                   sector: "Industry & Materials", logoDomain: "ibnsina-pharma.com" },
  { symbol: "RMDA", apiTicker: "RMDA.CA", nameEn: "Rameda Pharmaceuticals",                nameAr: "راميدا للصناعات الدوائية",         sector: "Industry & Materials", logoDomain: "rameda.com" },

  // ── Telecom & Tech ───────────────────────────────────────────────────────
  { symbol: "ETEL", apiTicker: "ETEL.CA", nameEn: "Telecom Egypt",                         nameAr: "المصرية للاتصالات",                sector: "Telecom & Tech", logoDomain: "te.eg" },
  { symbol: "FWRY", apiTicker: "FWRY.CA", nameEn: "Fawry for Banking & Payment Technology",nameAr: "فوري لتكنولوجيا الدفع الإلكتروني", sector: "Telecom & Tech", logoDomain: "fawry.com" },
  { symbol: "EFIH", apiTicker: "EFIH.CA", nameEn: "e-finance Investment Group",            nameAr: "إي فاينانس للاستثمارات المالية",   sector: "Telecom & Tech", logoDomain: "efinance.com.eg" },
  { symbol: "RAYA", apiTicker: "RAYA.CA", nameEn: "Raya Holding",                          nameAr: "راية القابضة",                     sector: "Telecom & Tech", logoDomain: "rayacorp.com" },

  // ── Financial Services ───────────────────────────────────────────────────
  { symbol: "HRHO", apiTicker: "HRHO.CA", nameEn: "EFG Hermes Holding",                    nameAr: "المجموعة المالية هيرميس القابضة",  sector: "Financial Services", logoDomain: "efghermes.com" },
  { symbol: "BTFH", apiTicker: "BTFH.CA", nameEn: "Beltone Financial Holding",             nameAr: "بلتون المالية القابضة",            sector: "Financial Services", logoDomain: "beltonefinancial.com" },
  { symbol: "CICH", apiTicker: "CICH.CA", nameEn: "CI Capital Holding",                    nameAr: "سي آي كابيتال القابضة",            sector: "Financial Services", logoDomain: "cicapital.com" },
  { symbol: "CCAP", apiTicker: "CCAP.CA", nameEn: "Qalaa Holdings",                        nameAr: "القلعة القابضة",                   sector: "Financial Services", logoDomain: "qalaaholdings.com" },
  { symbol: "ARCC", apiTicker: "ARCC.CA", nameEn: "Arab Cotton Ginning",                   nameAr: "العربية لحليج الأقطان",            sector: "Financial Services", logoDomain: "arabcotton.com" },
  { symbol: "GBCO", apiTicker: "GBCO.CA", nameEn: "GB Corp (GB Auto)",                     nameAr: "جي بي كورب (جي بي أوتو)",          sector: "Financial Services", logoDomain: "ghabbour.com" },

  // ── Food & Beverage ──────────────────────────────────────────────────────
  { symbol: "JUFO", apiTicker: "JUFO.CA", nameEn: "Juhayna Food Industries",               nameAr: "جهينة للصناعات الغذائية",          sector: "Food & Beverage", logoDomain: "juhayna.com" },
  { symbol: "EFID", apiTicker: "EFID.CA", nameEn: "Edita Food Industries",                 nameAr: "إديتا للصناعات الغذائية",          sector: "Food & Beverage", logoDomain: "edita.com.eg" },
  { symbol: "DOMT", apiTicker: "DOMT.CA", nameEn: "Domty (Arabian Food Industries)",       nameAr: "دومتي للصناعات الغذائية",          sector: "Food & Beverage", logoDomain: "domty.com" },
  { symbol: "DSCW", apiTicker: "DSCW.CA", nameEn: "Dice Sport & Casual Wear",              nameAr: "دايس للملابس الرياضية والكاجوال",  sector: "Food & Beverage", logoDomain: "dicegroup.com" },
  { symbol: "VLMR", apiTicker: "VLMR.CA", nameEn: "Valu (Misr Financial Investments)",     nameAr: "فاليو لخدمات التمويل",             sector: "Food & Beverage", logoDomain: "valu.com.eg" },
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
