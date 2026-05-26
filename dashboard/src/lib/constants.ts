import type { Ticker, Sector } from "@/types/api";

export const SECTORS: Sector[] = [
  "Banks",
  "Real Estate",
  "Industrial",
  "Telecom",
  "Financial Services",
  "Food & Beverage",
];

export const EGX_TICKERS: Ticker[] = [
  { symbol: "COMI.CA", name_en: "Commercial International Bank", name_ar: "البنك التجاري الدولي", sector: "Banks" },
  { symbol: "ADIB.CA", name_en: "Abu Dhabi Islamic Bank Egypt", name_ar: "مصرف أبوظبي الإسلامي مصر", sector: "Banks" },
  { symbol: "CIEB.CA", name_en: "Credit Agricole Egypt", name_ar: "كريدي أجريكول مصر", sector: "Banks" },
  { symbol: "EXPA.CA", name_en: "Export Development Bank", name_ar: "بنك التنمية الصناعية", sector: "Banks" },
  { symbol: "HDBK.CA", name_en: "Housing & Development Bank", name_ar: "بنك التعمير والإسكان", sector: "Banks" },
  { symbol: "QNBA.CA", name_en: "QNB Alahli", name_ar: "بنك قطر الوطني الأهلي", sector: "Banks" },
  { symbol: "SAUD.CA", name_en: "Suez Canal Bank", name_ar: "بنك قناة السويس", sector: "Banks" },
  { symbol: "TMGH.CA", name_en: "Talaat Moustafa Group", name_ar: "مجموعة طلعت مصطفى", sector: "Real Estate" },
  { symbol: "HELI.CA", name_en: "Heliopolis Housing", name_ar: "مصر الجديدة للإسكان", sector: "Real Estate" },
  { symbol: "PHDC.CA", name_en: "Palm Hills Developments", name_ar: "بالم هيلز للتعمير", sector: "Real Estate" },
  { symbol: "OCDI.CA", name_en: "Six of October Development", name_ar: "السادس من أكتوبر للتنمية", sector: "Real Estate" },
  { symbol: "ORAS.CA", name_en: "Orascom Construction", name_ar: "أوراسكوم للإنشاء", sector: "Real Estate" },
  { symbol: "EMFD.CA", name_en: "Emaar Misr", name_ar: "إعمار مصر", sector: "Real Estate" },
  { symbol: "EAST.CA", name_en: "Eastern Company", name_ar: "الشرقية للدخان", sector: "Industrial" },
  { symbol: "ESRS.CA", name_en: "Ezz Steel", name_ar: "حديد عز", sector: "Industrial" },
  { symbol: "SWDY.CA", name_en: "Elsewedy Electric", name_ar: "السويدي إليكتريك", sector: "Industrial" },
  { symbol: "ABUK.CA", name_en: "Abou Kir Fertilizers", name_ar: "أبو قير للأسمدة", sector: "Industrial" },
  { symbol: "MFPC.CA", name_en: "Misr Fertilizers (MOPCO)", name_ar: "موبكو", sector: "Industrial" },
  { symbol: "EGAL.CA", name_en: "Egypt Aluminum", name_ar: "مصر للألومنيوم", sector: "Industrial" },
  { symbol: "EGCH.CA", name_en: "Egyptian Chemical Industries", name_ar: "كيما", sector: "Industrial" },
  { symbol: "EFIC.CA", name_en: "Egyptian Financial Industrial", name_ar: "المالية الصناعية المصرية", sector: "Industrial" },
  { symbol: "ETEL.CA", name_en: "Telecom Egypt", name_ar: "المصرية للاتصالات", sector: "Telecom" },
  { symbol: "FWRY.CA", name_en: "Fawry for Banking Tech", name_ar: "فوري", sector: "Financial Services" },
  { symbol: "EFIH.CA", name_en: "EFG Hermes Holding", name_ar: "هيرميس القابضة", sector: "Financial Services" },
  { symbol: "RAYA.CA", name_en: "Raya Holding", name_ar: "راية القابضة", sector: "Financial Services" },
  { symbol: "HRHO.CA", name_en: "Hermes Holding", name_ar: "هيرميس", sector: "Financial Services" },
  { symbol: "BTFH.CA", name_en: "Beltone Financial", name_ar: "بلتون المالية", sector: "Financial Services" },
  { symbol: "CICH.CA", name_en: "CI Capital Holding", name_ar: "سي آي كابيتال", sector: "Financial Services" },
  { symbol: "JUFO.CA", name_en: "Juhayna Food Industries", name_ar: "جهينة للصناعات الغذائية", sector: "Food & Beverage" },
  { symbol: "EFID.CA", name_en: "Edita Food Industries", name_ar: "إديتا للصناعات الغذائية", sector: "Food & Beverage" },
  { symbol: "DOMT.CA", name_en: "Domty", name_ar: "دومتي", sector: "Food & Beverage" },
];

export const AGENT_PIPELINE: string[] = [
  "Data Prefetcher",
  "Market Analyst",
  "Fundamentals Analyst",
  "News Analyst",
  "Social Media Analyst",
  "Bull Researcher",
  "Bear Researcher",
  "Research Manager",
  "Trader",
  "Risk Scorer",
  "Risk Debators",
  "Risk Manager",
];

export const DECISION_COLORS = {
  BUY: { bg: "bg-primary", text: "text-primary-foreground", ring: "ring-primary" },
  SELL: { bg: "bg-destructive", text: "text-destructive-foreground", ring: "ring-destructive" },
  HOLD: { bg: "bg-warning", text: "text-warning-foreground", ring: "ring-warning" },
} as const;
