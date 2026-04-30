"""
Cached / Sample Social Media Data for EGX Testing
===================================================
Provides realistic sample social media posts for EGX stocks
in both Arabic and English, enabling offline testing without
live API access.

This module simulates what real EGX social media data looks like:
- Egyptian dialect Arabic (عامية مصرية) 
- Modern Standard Arabic (فصحى)
- English financial discussions
- Mixed Arabic-English posts (code-switching, common in Egyptian finance)

Each stock has 10-15 sample posts across platforms.
"""

from typing import List, Dict, Optional
from datetime import datetime, timedelta
from .schema import SocialPost


# =============================================================================
# EGX Ticker Aliases
# =============================================================================
# Maps EGX tickers to common names used on social media
# Needed because retail investors rarely use official ticker symbols

EGX_TICKER_ALIASES: Dict[str, List[str]] = {
    "COMI": ["CIB", "سي اي بي", "التجاري الدولي", "البنك التجاري", "cib", "commercial international"],
    "HRHO": ["هيرميس", "EFG Hermes", "إي أف جي", "hermes", "هيرمس"],
    "EAST": ["ايسترن", "Eastern Company", "الشرقية", "eastern", "ايسترن كومباني"],
    "EFIH": ["إي فاينانس", "EFinance", "فوري", "efinance", "اي فاينانس"],
    "SWDY": ["السويدي", "Elsewedy", "سويدي إليكتريك", "elsewedy", "السويدى"],
    "TMGH": ["طلعت مصطفى", "TMG", "Talaat Moustafa", "tmg", "طلعت"],
    "ORWE": ["أوراسكوم", "OCI", "Orascom", "اوراسكوم", "orascom"],
    "PHDC": ["فوسفات", "Palm Hills", "بالم هيلز", "palm hills"],
    "MNHD": ["مدينة نصر", "MNHD", "Madinet Nasr", "مدينة نصر للاسكان"],
    "ABUK": ["ابوقير", "Abu Qir", "أبو قير", "abu qir"],
}


def _make_timestamp(days_ago: int, hour: int = 12, anchor_date: str = None) -> str:
    """
    Generate a timestamp N days before anchor_date (defaults to today).

    anchor_date: "YYYY-MM-DD" string. If None, uses datetime.now().
    This allows cached posts to be correctly anchored to the backtest date
    rather than always being relative to the current wall-clock time.
    """
    if anchor_date:
        try:
            base = datetime.strptime(anchor_date, "%Y-%m-%d")
        except ValueError:
            base = datetime.now()
    else:
        base = datetime.now()
    dt = base - timedelta(days=days_ago) + timedelta(hours=hour)
    return dt.isoformat()


def get_cached_social_data(
    ticker: str,
    curr_date: str = None,
    look_back_days: int = 7,
) -> List[SocialPost]:
    """
    Get cached/sample social media data for an EGX stock.

    Posts are re-stamped relative to curr_date so that backtesting on
    historical dates gets correctly anchored timestamps instead of
    timestamps relative to today's wall-clock time.

    Args:
        ticker:         EGX ticker (e.g., "COMI", "HRHO")
        curr_date:      Reference date in "YYYY-MM-DD" (backtest date or today)
        look_back_days: How many days of data (affects data volume)

    Returns:
        List of SocialPost objects with correctly anchored timestamps.
    """
    ticker = ticker.upper().replace(".CA", "").strip()

    source_posts = _STOCK_DATA.get(ticker) or _generate_generic_posts(ticker)
    capped = source_posts[:min(len(source_posts), look_back_days * 3)]

    # Re-anchor timestamps to curr_date so backtest dates are correct.
    # Spread posts evenly across the look_back window (oldest first).
    n = len(capped)
    result: List[SocialPost] = []
    for i, post in enumerate(capped):
        days_ago = max(0, look_back_days - 1 - i) if n > 1 else 0
        hour = 10 + (i % 8)  # Vary hours 10-17 for realism
        new_ts = _make_timestamp(days_ago, hour=hour, anchor_date=curr_date)
        # Return a copy with the updated timestamp (don't mutate shared module data)
        import dataclasses
        restamped = dataclasses.replace(post, timestamp=new_ts)
        result.append(restamped)

    return result


def _generate_generic_posts(ticker: str) -> List[SocialPost]:
    """Generate generic posts for tickers without specific data."""
    return [
        SocialPost(
            text=f"سهم {ticker} شكله كويس النهارده، حد متابع؟",
            timestamp=_make_timestamp(1, 10),
            platform="twitter",
            engagement={"likes": 5, "shares": 1, "comments": 3, "views": 120},
            ticker=ticker,
            language="ar",
            author="trader_eg",
        ),
        SocialPost(
            text=f"Anyone tracking {ticker} on EGX? Volume looks interesting today.",
            timestamp=_make_timestamp(1, 14),
            platform="twitter",
            engagement={"likes": 3, "shares": 0, "comments": 2, "views": 80},
            ticker=ticker,
            language="en",
            author="egx_watcher",
        ),
        SocialPost(
            text=f"{ticker} technical analysis shows support at current levels. RSI neutral.",
            timestamp=_make_timestamp(2, 11),
            platform="telegram",
            engagement={"likes": 8, "shares": 2, "comments": 4, "views": 200},
            ticker=ticker,
            language="en",
            author="tech_analysis_eg",
        ),
        SocialPost(
            text=f"محدش يبيع {ticker} دلوقتي، السهم لسه عنده مشوار",
            timestamp=_make_timestamp(3, 9),
            platform="telegram",
            engagement={"likes": 15, "shares": 5, "comments": 8, "views": 350},
            ticker=ticker,
            language="ar",
            author="borsa_masr",
        ),
        SocialPost(
            text=f"EGX {ticker} - watching for breakout above resistance",
            timestamp=_make_timestamp(4, 13),
            platform="reddit",
            engagement={"likes": 2, "shares": 0, "comments": 1, "views": 45},
            ticker=ticker,
            language="en",
            author="u/egypt_trader",
        ),
    ]


# =============================================================================
# Stock-Specific Sample Data
# =============================================================================
# These posts simulate realistic social media discussions for major EGX stocks

_STOCK_DATA: Dict[str, List[SocialPost]] = {
    
    # =========================================================================
    # COMI - Commercial International Bank (CIB)
    # =========================================================================
    "COMI": [
        SocialPost(
            text="سهم CIB صاروخ 🚀 بعد نتائج الأعمال، الأرباح زادت 35% والتوزيعات هتكون ممتازة إن شاء الله",
            timestamp=_make_timestamp(0, 11),
            platform="twitter",
            engagement={"likes": 45, "shares": 12, "comments": 18, "views": 1200},
            ticker="COMI",
            language="ar",
            author="borsa_analyst",
        ),
        SocialPost(
            text="CIB is the safest play on EGX right now. Strong fundamentals, good dividend yield, and the devaluation actually helps their USD positions.",
            timestamp=_make_timestamp(0, 14),
            platform="twitter",
            engagement={"likes": 22, "shares": 5, "comments": 8, "views": 650},
            ticker="COMI",
            language="en",
            author="mena_investor",
        ),
        SocialPost(
            text="التجاري الدولي عامل دعم قوي عند 95 جنيه، لو كسره يبقى فيه مشكلة. بس الشكل الفني حلو",
            timestamp=_make_timestamp(1, 10),
            platform="telegram",
            engagement={"likes": 30, "shares": 8, "comments": 15, "views": 800},
            ticker="COMI",
            language="ar",
            author="chartist_eg",
        ),
        SocialPost(
            text="Just bought more COMI.CA on the dip. P/E still reasonable at 8x vs regional peers at 12x.",
            timestamp=_make_timestamp(1, 15),
            platform="reddit",
            engagement={"likes": 8, "shares": 0, "comments": 4, "views": 200},
            ticker="COMI",
            language="en",
            author="u/cairo_investor",
        ),
        SocialPost(
            text="حد يقدر يفيدني عن سهم سي اي بي؟ أنا لسه مبتدئ وعايز أدخل بمبلغ صغير",
            timestamp=_make_timestamp(2, 9),
            platform="facebook",
            engagement={"likes": 12, "shares": 2, "comments": 25, "views": 400},
            ticker="COMI",
            language="ar",
            author="new_investor_2026",
        ),
        SocialPost(
            text="CIB Q4 results beat estimates. Net income up ~30% YoY. Management guidance positive on loan growth. Maintaining OW.",
            timestamp=_make_timestamp(2, 16),
            platform="twitter",
            engagement={"likes": 55, "shares": 20, "comments": 12, "views": 2000},
            ticker="COMI",
            language="en",
            author="equity_research_mena",
        ),
        SocialPost(
            text="انا شايف إن CIB هيوصل 120 جنيه قبل نهاية السنة. البنك ده أقوى بنك في مصر بلا منازع",
            timestamp=_make_timestamp(3, 11),
            platform="telegram",
            engagement={"likes": 40, "shares": 10, "comments": 20, "views": 900},
            ticker="COMI",
            language="ar",
            author="bulls_egypt",
        ),
        SocialPost(
            text="⚠️ Warning: COMI volume declining. When big caps lose volume on uptrend, usually means distribution. Be careful.",
            timestamp=_make_timestamp(3, 13),
            platform="twitter",
            engagement={"likes": 18, "shares": 7, "comments": 10, "views": 500},
            ticker="COMI",
            language="en",
            author="cautious_trader",
        ),
        SocialPost(
            text="البنك المركزي ممكن يرفع الفايدة تاني وده هيساعد البنوك بشكل كبير. CIB أكبر مستفيد",
            timestamp=_make_timestamp(4, 10),
            platform="telegram",
            engagement={"likes": 25, "shares": 6, "comments": 8, "views": 600},
            ticker="COMI",
            language="ar",
            author="macro_egypt",
        ),
        SocialPost(
            text="CIB forming cup and handle pattern on daily chart. Bullish if it breaks 100 EGP with volume 📈",
            timestamp=_make_timestamp(5, 14),
            platform="twitter",
            engagement={"likes": 15, "shares": 3, "comments": 5, "views": 350},
            ticker="COMI",
            language="en",
            author="ta_egypt",
        ),
    ],
    
    # =========================================================================
    # HRHO - EFG Hermes Holding
    # =========================================================================
    "HRHO": [
        SocialPost(
            text="هيرميس المفروض يستفيد من أي طروحات جديدة. الحكومة بتتكلم عن طرح شركات تانية في البورصة",
            timestamp=_make_timestamp(0, 10),
            platform="twitter",
            engagement={"likes": 20, "shares": 5, "comments": 8, "views": 500},
            ticker="HRHO",
            language="ar",
            author="ipo_tracker",
        ),
        SocialPost(
            text="EFG Hermes revenue diversification is underappreciated. BNPL, leasing, microfinance all growing 40%+",
            timestamp=_make_timestamp(1, 12),
            platform="twitter",
            engagement={"likes": 30, "shares": 8, "comments": 6, "views": 700},
            ticker="HRHO",
            language="en",
            author="fintech_mena",
        ),
        SocialPost(
            text="سهم هيرميس غالي شوية عند الأسعار دي. P/E فوق 15 مرة. أفضل أستنى نزلة",
            timestamp=_make_timestamp(2, 11),
            platform="telegram",
            engagement={"likes": 10, "shares": 2, "comments": 12, "views": 300},
            ticker="HRHO",
            language="ar",
            author="value_investor_eg",
        ),
        SocialPost(
            text="HRHO stock is a hold for me. Good company but priced for perfection. Any miss and it drops 10%.",
            timestamp=_make_timestamp(3, 15),
            platform="reddit",
            engagement={"likes": 5, "shares": 0, "comments": 3, "views": 120},
            ticker="HRHO",
            language="en",
            author="u/mena_stocks",
        ),
        SocialPost(
            text="EFG Hermes expanded into Kenya and Pakistan. Growth story intact but execution risk is high.",
            timestamp=_make_timestamp(4, 9),
            platform="twitter",
            engagement={"likes": 12, "shares": 3, "comments": 4, "views": 250},
            ticker="HRHO",
            language="en",
            author="frontier_markets",
        ),
        SocialPost(
            text="هيرميس هتفضل أحسن سهم مالي في البورصة المصرية. الإدارة ممتازة وبتوسع في كل حتة",
            timestamp=_make_timestamp(5, 13),
            platform="telegram",
            engagement={"likes": 35, "shares": 9, "comments": 15, "views": 800},
            ticker="HRHO",
            language="ar",
            author="finance_masr",
        ),
    ],
    
    # =========================================================================
    # EAST - Eastern Company (tobacco)
    # =========================================================================
    "EAST": [
        SocialPost(
            text="الشرقية للدخان سهم defensive ممتاز. توزيعات أرباح ثابتة وأرباح بتزيد كل سنة",
            timestamp=_make_timestamp(0, 10),
            platform="twitter",
            engagement={"likes": 15, "shares": 3, "comments": 6, "views": 350},
            ticker="EAST",
            language="ar",
            author="dividend_hunter",
        ),
        SocialPost(
            text="EAST is basically a cash cow. Monopoly on tobacco in Egypt. Price increases = instant margin expansion.",
            timestamp=_make_timestamp(1, 14),
            platform="twitter",
            engagement={"likes": 20, "shares": 5, "comments": 8, "views": 500},
            ticker="EAST",
            language="en",
            author="value_plays_eg",
        ),
        SocialPost(
            text="سعر السجاير هيزيد تاني. ده صاروخ لسهم الشرقية. اللي معاه يمسك كويس",
            timestamp=_make_timestamp(2, 11),
            platform="telegram",
            engagement={"likes": 28, "shares": 7, "comments": 10, "views": 600},
            ticker="EAST",
            language="ar",
            author="borsa_tips",
        ),
        SocialPost(
            text="Concerns about ESG investors avoiding tobacco stocks. EAST might face foreign selling pressure.",
            timestamp=_make_timestamp(3, 16),
            platform="reddit",
            engagement={"likes": 4, "shares": 0, "comments": 2, "views": 80},
            ticker="EAST",
            language="en",
            author="u/esg_aware",
        ),
        SocialPost(
            text="الشرقية عند أعلى سعر تاريخي. المقاومة عند 28 جنيه صعبة. ممكن يصحح شوية",
            timestamp=_make_timestamp(4, 9),
            platform="telegram",
            engagement={"likes": 12, "shares": 3, "comments": 8, "views": 300},
            ticker="EAST",
            language="ar",
            author="technical_eg",
        ),
    ],
    
    # =========================================================================
    # EFIH - EFinance (digital payments, Fawry-adjacent)
    # =========================================================================
    "EFIH": [
        SocialPost(
            text="إي فاينانس بتكسب من كل معاملة حكومية إلكترونية. Growth story قوية جدا",
            timestamp=_make_timestamp(0, 11),
            platform="twitter",
            engagement={"likes": 25, "shares": 6, "comments": 10, "views": 550},
            ticker="EFIH",
            language="mixed",
            author="fintech_eg",
        ),
        SocialPost(
            text="EFIH is the backbone of Egypt's digital transformation. Government mandating e-payments = guaranteed revenue growth.",
            timestamp=_make_timestamp(1, 13),
            platform="twitter",
            engagement={"likes": 18, "shares": 4, "comments": 5, "views": 400},
            ticker="EFIH",
            language="en",
            author="digital_egypt",
        ),
        SocialPost(
            text="اللي اشترى EFinance من سنة كده خسران 30%. السهم ده overvalued بشكل فظيع",
            timestamp=_make_timestamp(2, 10),
            platform="telegram",
            engagement={"likes": 20, "shares": 5, "comments": 18, "views": 500},
            ticker="EFIH",
            language="ar",
            author="bear_case_eg",
        ),
        SocialPost(
            text="EFIH valuation is stretched at 30x forward P/E. Need to see revenue acceleration to justify price.",
            timestamp=_make_timestamp(3, 15),
            platform="reddit",
            engagement={"likes": 6, "shares": 0, "comments": 3, "views": 100},
            ticker="EFIH",
            language="en",
            author="u/valuation_matters",
        ),
    ],
    
    # =========================================================================
    # SWDY - Elsewedy Electric
    # =========================================================================
    "SWDY": [
        SocialPost(
            text="السويدي هيستفيد من مشاريع الطاقة المتجددة في أفريقيا. الشركة بتتوسع بشكل كبير",
            timestamp=_make_timestamp(0, 10),
            platform="twitter",
            engagement={"likes": 22, "shares": 5, "comments": 8, "views": 450},
            ticker="SWDY",
            language="ar",
            author="energy_sector",
        ),
        SocialPost(
            text="Elsewedy won a massive contract in Tanzania. This is a game changer for FY2026 revenue.",
            timestamp=_make_timestamp(1, 14),
            platform="twitter",
            engagement={"likes": 30, "shares": 10, "comments": 7, "views": 700},
            ticker="SWDY",
            language="en",
            author="infra_plays",
        ),
        SocialPost(
            text="سويدي كهرب كل حاجة 😂 السهم طالع والشركة بتفوز بعقود جديدة كل أسبوع",
            timestamp=_make_timestamp(2, 11),
            platform="telegram",
            engagement={"likes": 35, "shares": 8, "comments": 12, "views": 800},
            ticker="SWDY",
            language="ar",
            author="bulls_telegram",
        ),
    ],
}


# =============================================================================
# Market-Wide / General EGX Sentiment Posts
# =============================================================================

MARKET_WIDE_POSTS: List[SocialPost] = [
    SocialPost(
        text="البورصة المصرية عندها فرص حلوة بعد التعويم. أسعار الأسهم بقت رخيصة بالدولار",
        timestamp=_make_timestamp(0, 9),
        platform="twitter",
        engagement={"likes": 50, "shares": 15, "comments": 20, "views": 1500},
        ticker=None,
        language="ar",
        author="macro_egypt",
    ),
    SocialPost(
        text="EGX30 looking strong. Foreign inflows picking up after devaluation. MENA allocators adding Egypt exposure.",
        timestamp=_make_timestamp(1, 10),
        platform="twitter",
        engagement={"likes": 35, "shares": 10, "comments": 8, "views": 900},
        ticker=None,
        language="en",
        author="mena_allocator",
    ),
    SocialPost(
        text="⚠️ الحذر واجب - البنك المركزي ممكن يرفع الفايدة وده هيأثر على البورصة بشكل سلبي",
        timestamp=_make_timestamp(2, 11),
        platform="telegram",
        engagement={"likes": 25, "shares": 8, "comments": 15, "views": 600},
        ticker=None,
        language="ar",
        author="cbg_watcher",
    ),
    SocialPost(
        text="Volume on EGX has been anemic. Low participation = low conviction. Wait for catalyst before going in heavy.",
        timestamp=_make_timestamp(3, 14),
        platform="twitter",
        engagement={"likes": 12, "shares": 3, "comments": 5, "views": 300},
        ticker=None,
        language="en",
        author="flow_trader",
    ),
]
