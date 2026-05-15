"""
seed_egx_news.py
================
Creates the egx_news/ directory structure under data_cache/ and populates it
with realistic bilingual (Arabic + English) sample news CSVs spanning 2022–2025.

News entries are generated per quarter so that any backtesting window between
2022-Q4 and 2025-Q4 will always find relevant articles.

Run from project root:
    python scripts/seed_egx_news.py

Directory layout created:
    data_cache/egx_news/
        csv/        {TICKER}_news.csv          (company-specific)
        global/     egx_market_news.csv        (market-wide)
        text/       COMI_news_{DATE}.txt       (text format sample)
"""

import os
import sys
import csv
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from tradingagents.dataflows.config import DATA_DIR

NEWS_BASE_DIR = os.path.join(DATA_DIR, "egx_news")


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _quarter_dates(year: int, quarter: int):
    """
    Return three dates spread across the given quarter:
    beginning, mid, and end of the quarter.
    """
    starts = {1: (1, 5), 2: (4, 5), 3: (7, 5), 4: (10, 5)}
    mids   = {1: (2, 15), 2: (5, 15), 3: (8, 15), 4: (11, 15)}
    ends   = {1: (3, 20), 2: (6, 20), 3: (9, 20), 4: (12, 20)}
    s = datetime(year, starts[quarter][0], starts[quarter][1]).strftime("%Y-%m-%d")
    m = datetime(year, mids[quarter][0],   mids[quarter][1]).strftime("%Y-%m-%d")
    e = datetime(year, ends[quarter][0],   ends[quarter][1]).strftime("%Y-%m-%d")
    return s, m, e


# ---------------------------------------------------------------------------
# Company news templates
# Each entry: (headline_en, body_en, headline_ar, body_ar, source_en, source_ar)
# One set per quarter — we cycle through to keep variety across years.
# ---------------------------------------------------------------------------

COMPANY_TEMPLATES = {
    "COMI": [
        (
            "CIB posts record quarterly net profit on loan growth",
            "Commercial International Bank reported a strong quarterly net profit driven by robust loan portfolio expansion and improved net interest margins. Return on equity remained above 25%.",
            "البنك التجاري الدولي يحقق أرباحاً قياسية بفضل نمو محفظة القروض",
            "سجل البنك التجاري الدولي أرباحاً صافية قياسية بفضل نمو ملحوظ في محفظة القروض وتحسن هوامش الفائدة الصافية. وظل العائد على حقوق الملكية فوق مستوى 25%.",
            "Enterprise.news", "Mubasher"
        ),
        (
            "CIB capital adequacy ratio comfortably above CBE minimum",
            "Commercial International Bank maintains a Capital Adequacy Ratio well above the Central Bank of Egypt's 12.5% requirement, supporting continued credit growth.",
            "نسبة كفاية رأس مال CIB تتجاوز الحد الأدنى للبنك المركزي المصري",
            "يحافظ البنك التجاري الدولي على نسبة كفاية رأس مال تتجاوز الحد الأدنى المطلوب من البنك المركزي المصري البالغ 12.5%، مما يدعم استمرار نمو الائتمان.",
            "Enterprise.news", "Mubasher"
        ),
        (
            "CIB expands digital banking with new mobile features",
            "CIB launched upgraded mobile banking services targeting retail and SME customers, with AI-assisted financial advisory features and instant transfer capabilities.",
            "البنك التجاري الدولي يطلق ميزات جديدة للخدمات المصرفية الرقمية",
            "أطلق البنك التجاري الدولي خدمات مصرفية رقمية محسّنة تستهدف عملاء التجزئة والشركات الصغيرة والمتوسطة، مع ميزات استشارات مالية بالذكاء الاصطناعي.",
            "Enterprise.news", "Mubasher"
        ),
        (
            "CIB cash dividend declared for fiscal year",
            "CIB board of directors approved a cash dividend distribution for the fiscal year, reflecting the bank's strong profitability and commitment to shareholder returns.",
            "البنك التجاري الدولي يعلن توزيع أرباح نقدية",
            "وافق مجلس إدارة البنك التجاري الدولي على توزيع أرباح نقدية للعام المالي، مما يعكس الربحية القوية للبنك والتزامه بعوائد المساهمين.",
            "Enterprise.news", "Mubasher"
        ),
    ],
    "HRHO": [
        (
            "EFG Hermes reports strong investment banking revenue",
            "EFG Hermes posted robust investment banking fees on the back of multiple MENA-region IPOs and M&A advisory mandates.",
            "هيرميس تسجل إيرادات قوية من الخدمات المصرفية الاستثمارية",
            "سجلت هيرميس رسوم خدمات مصرفية استثمارية قوية بفضل طرحات عامة أولية متعددة في منطقة الشرق الأوسط وشمال أفريقيا وصفقات اندماج واستحواذ.",
            "Enterprise.news", "Mubasher"
        ),
        (
            "EFG Hermes brokerage leads EGX trading volumes",
            "EFG Hermes brokerage maintained its leading position in Egyptian Exchange trading volumes, capturing a significant share of both local and foreign investor activity.",
            "هيرميس تتصدر أحجام التداول في البورصة المصرية",
            "حافظت شركة هيرميس للوساطة على مكانتها الرائدة في أحجام تداول البورصة المصرية، محتلةً نصيباً كبيراً من نشاط المستثمرين المحليين والأجانب.",
            "Enterprise.news", "Mubasher"
        ),
        (
            "EFG Hermes wealth management AUM grows amid market rally",
            "Assets under management in EFG Hermes wealth management division grew materially, benefiting from rising EGX equity valuations and new client inflows.",
            "أصول هيرميس للإدارة تنمو في ظل ارتفاع السوق",
            "نمت الأصول الخاضعة للإدارة في قسم إدارة الثروات بهيرميس بشكل ملموس، مستفيدةً من ارتفاع تقييمات أسهم البورصة المصرية وتدفقات عملاء جدد.",
            "Enterprise.news", "Mubasher"
        ),
    ],
    "EAST": [
        (
            "Eastern Company raises cigarette prices to offset cost pressures",
            "Eastern Company announced price increases across key product lines citing rising raw material and logistics costs. The company maintains over 70% market share in Egypt.",
            "الشرقية للدخان ترفع أسعار السجائر لمواجهة ضغوط التكاليف",
            "أعلنت الشركة الشرقية للدخان عن رفع أسعار منتجاتها الرئيسية استجابةً لارتفاع تكاليف المواد الخام والخدمات اللوجستية. تحتفظ الشركة بأكثر من 70% من حصة السوق في مصر.",
            "Enterprise.news", "Mubasher"
        ),
        (
            "Eastern Company net income rises on volume growth and pricing",
            "Eastern Company reported net income growth driven by a combination of volume expansion and price adjustments, sustaining strong profitability margins.",
            "الشرقية للدخان ترفع صافي أرباحها بفضل نمو الحجم والتسعير",
            "أعلنت الشركة الشرقية للدخان عن نمو صافي الأرباح مدعوماً بمزيج من توسع الحجم وتعديلات الأسعار، مع الحفاظ على هوامش ربحية قوية.",
            "Enterprise.news", "Mubasher"
        ),
        (
            "Eastern Company exports grow in African markets",
            "Eastern Company expanded its export footprint across several African markets, benefiting from its strong brand recognition and competitive pricing.",
            "صادرات الشرقية للدخان تنمو في الأسواق الأفريقية",
            "وسّعت الشركة الشرقية للدخان بصمتها التصديرية في عدة أسواق أفريقية، مستفيدةً من قوة علامتها التجارية وتنافسية أسعارها.",
            "Enterprise.news", "Mubasher"
        ),
    ],
    "EFIH": [
        (
            "e-finance fintech revenue surges on digital payments adoption",
            "e-finance for Digital and Financial Investments reported strong fintech revenue growth driven by accelerating digital payments adoption and government e-payment mandates.",
            "إي فاينانس تسجل نمواً قوياً في إيرادات التكنولوجيا المالية",
            "سجلت شركة إي فاينانس نمواً قوياً في إيرادات التكنولوجيا المالية مدفوعاً بالتبني المتسارع للمدفوعات الرقمية وتفويضات الحكومة للدفع الإلكتروني.",
            "Enterprise.news", "Mubasher"
        ),
        (
            "e-finance wins new government contract for electronic collection",
            "e-finance signed a new multi-year contract with Egyptian government entities for electronic collection systems, expanding its recurring revenue base.",
            "إي فاينانس تفوز بعقد حكومي جديد لمنظومة التحصيل الإلكتروني",
            "أبرمت شركة إي فاينانس عقداً جديداً متعدد السنوات مع جهات حكومية مصرية لأنظمة التحصيل الإلكتروني، مما يوسع قاعدتها من الإيرادات المتكررة.",
            "Enterprise.news", "Mubasher"
        ),
    ],
    "SWDY": [
        (
            "Elsewedy Electric wins major power infrastructure contract",
            "Elsewedy Electric was awarded a significant power infrastructure contract in Africa, reinforcing its position as a leading electrical equipment and energy solutions provider.",
            "السويدي إليكتريك تفوز بعقد بنية تحتية كبير للطاقة",
            "فازت السويدي إليكتريك بعقد مهم للبنية التحتية للطاقة في أفريقيا، مما يعزز مكانتها كمزود رائد للمعدات الكهربائية وحلول الطاقة.",
            "Enterprise.news", "Mubasher"
        ),
        (
            "Elsewedy Electric expands renewable energy capacity",
            "Elsewedy Electric announced new investments in solar and wind energy projects, targeting a significant increase in its renewable energy generation capacity.",
            "السويدي إليكتريك توسّع طاقتها في مجال الطاقة المتجددة",
            "أعلنت السويدي إليكتريك عن استثمارات جديدة في مشاريع الطاقة الشمسية وطاقة الرياح، استهدافاً لزيادة ملموسة في طاقتها الإنتاجية من مصادر متجددة.",
            "Enterprise.news", "Mubasher"
        ),
    ],
    "FWRY": [
        (
            "Fawry merchant network expands across Egypt",
            "Fawry for Banking Technology and Electronic Payment continued expanding its merchant network nationwide, strengthening its position as Egypt's leading e-payment platform.",
            "فوري توسّع شبكة نقاط البيع في أنحاء مصر",
            "واصلت شركة فوري للتكنولوجيا المصرفية والدفع الإلكتروني توسيع شبكة نقاط البيع التابعة لها على المستوى الوطني، مما يعزز مكانتها كمنصة الدفع الإلكتروني الرائدة في مصر.",
            "Enterprise.news", "Mubasher"
        ),
        (
            "Fawry launches digital lending product for SMEs",
            "Fawry introduced a new digital lending product for small and medium enterprises in partnership with Egyptian banks, targeting financial inclusion for underserved businesses.",
            "فوري تطلق منتج إقراض رقمي للمشروعات الصغيرة والمتوسطة",
            "أطلقت فوري منتجاً جديداً للإقراض الرقمي للمشروعات الصغيرة والمتوسطة بالتعاون مع بنوك مصرية، بهدف تعزيز الشمول المالي للشركات غير المستفيدة.",
            "Enterprise.news", "Mubasher"
        ),
    ],
    "TMGH": [
        (
            "TMG Holding launches new residential project in New Cairo",
            "Talaat Moustafa Group unveiled a major mixed-use development in New Cairo, featuring residential units, commercial space, and green areas, with total investment exceeding EGP 30 billion.",
            "مجموعة طلعت مصطفى تطلق مشروعاً سكنياً جديداً في القاهرة الجديدة",
            "كشفت مجموعة طلعت مصطفى القابضة عن مشروع تطوير متعدد الاستخدامات في القاهرة الجديدة، يضم وحدات سكنية ومساحات تجارية ومناطق خضراء، باستثمارات تتجاوز 30 مليار جنيه.",
            "Enterprise.news", "Mubasher"
        ),
        (
            "TMG pre-sales hit record levels on strong housing demand",
            "Talaat Moustafa Group recorded record pre-sales figures driven by strong demand for residential units amid inflationary pressures pushing Egyptians toward real estate as an investment.",
            "مجموعة طلعت مصطفى تسجل مبيعات تعاقدية قياسية",
            "سجلت مجموعة طلعت مصطفى أرقاماً قياسية في المبيعات التعاقدية مدعومةً بطلب قوي على الوحدات السكنية في ظل ضغوط التضخم التي تدفع المصريين نحو العقارات كاستثمار.",
            "Enterprise.news", "Mubasher"
        ),
    ],
    "ABUK": [
        (
            "Abu Qir Fertilizers benefits from improving fertilizer prices",
            "Abu Qir Fertilizers reported improved margins as global nitrogen and potash fertilizer prices recovered, supporting stronger profitability for the Egyptian producer.",
            "أبو قير للأسمدة تستفيد من تحسن أسعار الأسمدة",
            "أعلنت أبو قير للأسمدة عن تحسن في هوامشها مع انتعاش أسعار الأسمدة النيتروجينية والبوتاسية عالمياً، مما يدعم ربحية المنتج المصري.",
            "Enterprise.news", "Mubasher"
        ),
    ],
    "PHDC": [
        (
            "Palm Hills Development reports strong pre-sales growth",
            "Palm Hills Development reported double-digit growth in pre-sales for its premium residential projects in West Cairo and on Egypt's North Coast.",
            "بالم هيلز للتعمير تسجل نمواً قوياً في المبيعات التعاقدية",
            "سجلت بالم هيلز للتعمير نمواً بنسب مزدوجة في مبيعاتها التعاقدية لمشاريعها السكنية الفاخرة في غرب القاهرة وعلى الساحل الشمالي لمصر.",
            "Enterprise.news", "Mubasher"
        ),
    ],
    "ORWE": [
        (
            "Oriental Weavers expands carpet exports to new European markets",
            "Oriental Weavers Group increased its carpet and flooring export volumes to Europe, entering new markets and growing its international revenue base.",
            "الشرقية للمفروشات توسّع صادرات السجاد إلى أسواق أوروبية جديدة",
            "رفعت مجموعة الشرقية للمفروشات حجم صادراتها من السجاد والأرضيات إلى أوروبا، لتدخل أسواقاً جديدة وتنمّي قاعدة إيراداتها الدولية.",
            "Enterprise.news", "Mubasher"
        ),
    ],
}


GLOBAL_TEMPLATES = [
    (
        "EGX30 index posts gains on foreign investor inflows",
        "The EGX30 benchmark index rose as foreign institutional investors increased their positions in Egyptian equities, driven by improving macroeconomic signals and EGP stability.",
        "مؤشر EGX30 يرتفع بدعم من تدفقات المستثمرين الأجانب",
        "ارتفع مؤشر EGX30 القياسي مع زيادة المستثمرين المؤسسيين الأجانب لمراكزهم في الأسهم المصرية، مدفوعاً بتحسن المؤشرات الاقتصادية الكلية واستقرار الجنيه المصري.",
        "EGX Official", "Mubasher"
    ),
    (
        "CBE holds interest rates steady at policy meeting",
        "The Central Bank of Egypt's Monetary Policy Committee voted to maintain benchmark interest rates unchanged, citing progress on inflation while monitoring global economic conditions.",
        "البنك المركزي المصري يثبت أسعار الفائدة في اجتماع السياسة النقدية",
        "صوّتت لجنة السياسة النقدية للبنك المركزي المصري على الإبقاء على أسعار الفائدة الرئيسية دون تغيير، مستشهدةً بالتقدم المحرز في مكافحة التضخم مع مراقبة الأوضاع الاقتصادية العالمية.",
        "Enterprise.news", "Mubasher"
    ),
    (
        "Egypt GDP growth beats analyst estimates",
        "Egypt's real GDP growth exceeded analyst forecasts, driven by strong performance in manufacturing, tourism, and the ICT sectors.",
        "نمو الناتج المحلي الإجمالي لمصر يتجاوز توقعات المحللين",
        "تجاوز نمو الناتج المحلي الإجمالي الحقيقي لمصر توقعات المحللين، مدعوماً بأداء قوي لقطاعات التصنيع والسياحة وتكنولوجيا المعلومات والاتصالات.",
        "Enterprise.news", "EGX Official"
    ),
    (
        "FRA announces simplified EGX listing rules to boost IPO pipeline",
        "The Financial Regulatory Authority introduced updated listing rules designed to attract more companies to list on the Egyptian Exchange, with several IPOs expected in coming months.",
        "الرقابة المالية تعلن تبسيط قواعد القيد في البورصة لتشجيع الطرح العام",
        "أعلنت هيئة الرقابة المالية عن قواعد قيد محدّثة تهدف إلى جذب مزيد من الشركات للإدراج في البورصة المصرية، مع توقع عدة طروحات عامة أولية في الأشهر القادمة.",
        "Enterprise.news", "EGX Official"
    ),
    (
        "Egypt inflation declines as monetary tightening takes effect",
        "Annual urban inflation in Egypt declined for the second consecutive month as the Central Bank's tightening cycle began to filter through the economy.",
        "انخفاض التضخم في مصر مع بدء تأثير تشديد السياسة النقدية",
        "تراجع معدل التضخم الحضري السنوي في مصر للشهر الثاني على التوالي مع بدء تأثير دورة التشديد النقدي للبنك المركزي في الاقتصاد.",
        "Enterprise.news", "Mubasher"
    ),
    (
        "Foreign investors net buyers on EGX for multiple consecutive weeks",
        "Foreign institutional investors maintained net buying positions on the Egyptian Exchange, contributing to upward pressure on blue-chip stocks including CIB and Eastern Company.",
        "المستثمرون الأجانب يواصلون الشراء الصافي في البورصة المصرية",
        "واصل المستثمرون المؤسسيون الأجانب مراكز الشراء الصافية في البورصة المصرية، مساهمين في ضغط تصاعدي على أسهم الشركات الكبرى مثل التجاري الدولي والشرقية للدخان.",
        "Enterprise.news", "Mubasher"
    ),
]


# ---------------------------------------------------------------------------
# Generation logic
# ---------------------------------------------------------------------------

def _build_company_articles() -> dict:
    """
    Build per-ticker article lists spanning every quarter from 2022-Q4 to 2025-Q4.
    Returns dict: { ticker: [(date, headline, body, source, language), ...] }
    """
    quarters = []
    for year in range(2022, 2026):
        for q in range(1, 5):
            quarters.append((year, q))

    all_articles = {}

    for ticker, templates in COMPANY_TEMPLATES.items():
        articles = []
        for qi, (year, q) in enumerate(quarters):
            start_d, mid_d, end_d = _quarter_dates(year, q)
            tmpl = templates[qi % len(templates)]
            headline_en, body_en, headline_ar, body_ar, src_en, src_ar = tmpl

            # English article at quarter start
            articles.append((start_d, headline_en, body_en, src_en, "english"))
            # Arabic article at quarter mid
            articles.append((mid_d, headline_ar, body_ar, src_ar, "arabic"))

        all_articles[ticker] = articles

    return all_articles


def _build_global_articles() -> list:
    """
    Build market-wide news spanning every quarter from 2022-Q4 to 2025-Q4.
    Returns list: [(date, headline, body, source, language), ...]
    """
    quarters = []
    for year in range(2022, 2026):
        for q in range(1, 5):
            quarters.append((year, q))

    articles = []
    for qi, (year, q) in enumerate(quarters):
        start_d, mid_d, end_d = _quarter_dates(year, q)
        tmpl = GLOBAL_TEMPLATES[qi % len(GLOBAL_TEMPLATES)]
        headline_en, body_en, headline_ar, body_ar, src_en, src_ar = tmpl

        articles.append((start_d, headline_en, body_en, src_en, "english"))
        articles.append((mid_d, headline_ar, body_ar, src_ar, "arabic"))
        articles.append((end_d, f"EGX weekly summary: Q{q} {year} market review",
                         f"Weekly market review for Q{q} {year}: EGX continued to show resilience amid global headwinds, with banking and real estate sectors leading performance.",
                         src_en, "english"))

    return articles


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def create_directories():
    dirs = [
        os.path.join(NEWS_BASE_DIR, "csv"),
        os.path.join(NEWS_BASE_DIR, "text"),
        os.path.join(NEWS_BASE_DIR, "global"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
        print(f"  [+] Created {d}")


def write_company_csv(ticker: str, articles: list):
    csv_path = os.path.join(NEWS_BASE_DIR, "csv", f"{ticker}_news.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "headline", "body", "source", "language"])
        for row in articles:
            writer.writerow(row)
    print(f"  [+] {ticker}_news.csv — {len(articles)} articles "
          f"({articles[0][0]} to {articles[-1][0]})")


def write_global_csv(articles: list):
    csv_path = os.path.join(NEWS_BASE_DIR, "global", "egx_market_news.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "headline", "body", "source", "language"])
        for row in articles:
            writer.writerow(row)
    print(f"  [+] egx_market_news.csv — {len(articles)} articles "
          f"({articles[0][0]} to {articles[-1][0]})")


def write_sample_text_file():
    """Write a sample text-format news file for COMI (covers 2023-01-05)."""
    ticker   = "COMI"
    date_str = "2023-01-05"
    text_path = os.path.join(NEWS_BASE_DIR, "text", f"{ticker}_news_{date_str}.txt")
    content = f"""SOURCE: Enterprise.news
DATE: {date_str}
LANGUAGE: english
---
CIB announces new ATM partnership program
Commercial International Bank has launched a new ATM sharing partnership with three regional banks, expanding its cash access network across Egypt. The initiative is expected to improve financial inclusion in rural and semi-urban areas.
---
CIB rated among top banks in Africa by Global Finance
Global Finance magazine ranked CIB among the top banks in Africa, citing its digital innovation, strong capital position, and consistent profitability across economic cycles.
"""
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [+] {ticker}_news_{date_str}.txt (sample text file)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  SEED EGX NEWS DATA  (2022-Q4 through 2025-Q4)")
    print("=" * 60)
    print(f"\nData directory : {DATA_DIR}")
    print(f"News directory : {NEWS_BASE_DIR}\n")

    print("[1] Creating directory structure...")
    create_directories()

    print("\n[2] Building article data...")
    company_articles = _build_company_articles()
    global_articles  = _build_global_articles()

    print("\n[3] Writing company news CSVs...")
    for ticker, articles in company_articles.items():
        write_company_csv(ticker, articles)

    print("\n[4] Writing global market news CSV...")
    write_global_csv(global_articles)

    print("\n[5] Writing sample text file...")
    write_sample_text_file()

    total = sum(len(a) for a in company_articles.values()) + len(global_articles)
    print("\n" + "=" * 60)
    print(f"  DONE — {total} total articles across {len(company_articles)} tickers + global")
    print(f"  Date coverage: 2022-01-05 → 2025-12-20")
    print(f"  Any backtest date in 2022–2025 will find news within 7-day lookback.")
    print("=" * 60)


if __name__ == "__main__":
    main()
