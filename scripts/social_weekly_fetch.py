"""Weekly social-media producer for the EGX sentiment archive.

Run this ONCE A WEEK (cron / Task Scheduler). It performs the single heavy
Apify-backed scrape, enriches + tags every post (ticker / sector / index / event),
runs sentiment once, and archives everything into the Postgres `social_v2_posts`
table.

Every subsequent agent run then reads that fresh, pre-tagged corpus via
`social_v2.signal_adapter.fetch_v2_signal` (which re-aggregates cheaply — no Apify
call, no LLM, sentiment already stored). This is what saves the Apify quota, the
tokens and the per-run latency, and removes the run-time failure surface of live
scraping. See CLAUDE.md §8 and MEMORY.md §MM.

Prerequisites:
    * POSTGRES_URL set (else the archive is disabled and this script is a no-op
      beyond logging — the live path will keep falling back to on-demand scraping).
    * APIFY_API_TOKEN set for the Facebook primary source (free news sources still
      contribute without it).

Usage
-----
    python scripts/social_weekly_fetch.py
    python scripts/social_weekly_fetch.py --fb-per-group 200 --reddit-max 160
    python scripts/social_weekly_fetch.py --stats-only   # just report corpus health

Schedule (Task Scheduler / cron), e.g. every Monday 06:00:
    cron:  0 6 * * 1  cd /path/to/repo && python scripts/social_weekly_fetch.py
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("tradingagents.social_weekly_fetch")


def _corpus_stats() -> None:
    """Report archive size + recency so the operator can confirm the run landed."""
    url = os.getenv("POSTGRES_URL")
    if not url:
        log.warning("POSTGRES_URL unset — cannot read corpus stats (archive disabled).")
        return
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(url)
        conn.autocommit = True
    except Exception as exc:
        log.warning("corpus stats: connect failed: %s", exc)
        return
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=8)).date().isoformat()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM social_v2_posts")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM social_v2_posts WHERE post_timestamp >= %s",
                (cutoff,),
            )
            recent = cur.fetchone()[0]
            cur.execute(
                """
                SELECT unnest(symbols) AS sym, count(*) c
                FROM social_v2_posts
                WHERE post_timestamp >= %s AND symbols IS NOT NULL
                GROUP BY sym ORDER BY c DESC LIMIT 8
                """,
                (cutoff,),
            )
            top = cur.fetchall()
        log.info("Corpus: %d total posts, %d in the last 8 days.", total, recent)
        if top:
            log.info("Top tickers (last 8d): %s", ", ".join(f"{s}:{c}" for s, c in top))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly EGX social producer → social_v2_posts.")
    ap.add_argument("--fb-per-group", type=int, default=200,
                    help="Facebook results per Apify group (default 200; ~1000 across 5 groups).")
    ap.add_argument("--reddit-max", type=int, default=160,
                    help="Max Reddit posts across all queries (default 160).")
    ap.add_argument("--stats-only", action="store_true",
                    help="Skip scraping; just print corpus health.")
    args = ap.parse_args()

    if args.stats_only:
        _corpus_stats()
        return

    if not os.getenv("POSTGRES_URL"):
        log.warning(
            "POSTGRES_URL is not set — the scrape will run but NOTHING will be archived. "
            "Set POSTGRES_URL so the weekly corpus persists."
        )

    from tradingagents.dataflows.social_v2.pipeline import run_pipeline

    log.info("Weekly social fetch starting (fb_per_group=%d, reddit_max=%d)",
             args.fb_per_group, args.reddit_max)
    output = run_pipeline(
        fb_per_group=args.fb_per_group,
        reddit_max=args.reddit_max,
        archive_posts=True,
    )

    counts = output.get("source_counts", {})
    market = output.get("market_sentiment", {})
    log.info(
        "Scrape complete: market=%s n=%d conf=%.2f | sources=%s",
        str(market.get("label", "n/a")).upper(),
        market.get("n", 0),
        market.get("confidence", 0.0),
        counts,
    )
    _corpus_stats()
    log.info("Weekly social fetch done.")


if __name__ == "__main__":
    main()
