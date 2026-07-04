"""Postgres archive for v2 scraped posts.

Purpose: collect a historical post corpus so future backtests can replay
real social data on past dates instead of using news-derived proxies.

Behaviour: best-effort. If POSTGRES_URL is unset, or psycopg2 is missing,
or the table doesn't exist, the writer logs a single warning and turns
itself off for the process lifetime. The pipeline keeps running.

Schema (created by db_schema.sql, but auto-created here if missing for
convenience):

    CREATE TABLE IF NOT EXISTS social_v2_posts (
        id              BIGSERIAL PRIMARY KEY,
        post_hash       TEXT UNIQUE NOT NULL,
        platform        TEXT NOT NULL,
        source          TEXT NOT NULL,
        url             TEXT,
        username        TEXT,
        post_timestamp  TIMESTAMPTZ,
        scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        text            TEXT NOT NULL,
        engagement      INTEGER DEFAULT 0,
        symbols         TEXT[],
        intents         TEXT[],
        content_label   TEXT,
        sentiment_score REAL,
        sentiment_label TEXT
    );
    CREATE INDEX IF NOT EXISTS social_v2_posts_ts_idx
        ON social_v2_posts (post_timestamp);
    CREATE INDEX IF NOT EXISTS social_v2_posts_symbols_idx
        ON social_v2_posts USING GIN (symbols);
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Iterable

log = logging.getLogger("tradingagents.social_v2.post_store")

_DDL = """
CREATE TABLE IF NOT EXISTS social_v2_posts (
    id              BIGSERIAL PRIMARY KEY,
    post_hash       TEXT UNIQUE NOT NULL,
    platform        TEXT NOT NULL,
    source          TEXT NOT NULL,
    url             TEXT,
    username        TEXT,
    post_timestamp  TIMESTAMPTZ,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    text            TEXT NOT NULL,
    engagement      INTEGER DEFAULT 0,
    symbols         TEXT[],
    intents         TEXT[],
    content_label   TEXT,
    sentiment_score REAL,
    sentiment_label TEXT,
    sectors         TEXT[],
    indices         TEXT[]
);
-- Additive, idempotent: bring older deployments up to the tagged schema.
ALTER TABLE social_v2_posts ADD COLUMN IF NOT EXISTS sectors TEXT[];
ALTER TABLE social_v2_posts ADD COLUMN IF NOT EXISTS indices TEXT[];
CREATE INDEX IF NOT EXISTS social_v2_posts_ts_idx
    ON social_v2_posts (post_timestamp);
CREATE INDEX IF NOT EXISTS social_v2_posts_symbols_idx
    ON social_v2_posts USING GIN (symbols);
CREATE INDEX IF NOT EXISTS social_v2_posts_sectors_idx
    ON social_v2_posts USING GIN (sectors);
CREATE INDEX IF NOT EXISTS social_v2_posts_indices_idx
    ON social_v2_posts USING GIN (indices);
"""


def _derive_tags(symbols, sector_mentions):
    """Roll ticker mentions up to EGX sector + index tags via the taxonomy.

    Returns (sectors, indices) as deduped string lists. Best-effort: any import
    or lookup failure yields whatever was resolved so far. This is what lets the
    weekly archive be filtered directly by sector/index without re-deriving at
    read time.
    """
    sectors: set = set()
    indices: set = set()
    # sector_mentions may already carry explicit sector tags (strings or objects).
    # Normalise to lower-case so keyword-detected tags (BANKS) and ticker-rollup
    # tags (banks) collapse to one canonical value for consistent filtering.
    for sm in sector_mentions or []:
        name = getattr(sm, "sector", None) or getattr(sm, "value", None) or (
            sm if isinstance(sm, str) else None
        )
        if name:
            sectors.add(str(name).lower())
    try:
        from tradingagents.sentiment.taxonomy import (
            SectorEnum,
            ticker_to_indices,
            ticker_to_sector,
        )
        for sym in symbols or []:
            try:
                sec = ticker_to_sector(sym)
                if sec is not None and sec != SectorEnum.UNKNOWN:
                    sectors.add(str(sec.value).lower())
            except Exception:
                pass
            try:
                for idx in ticker_to_indices(sym):
                    indices.add(idx.value)
            except Exception:
                pass
    except Exception:
        pass
    return sorted(sectors), sorted(indices)

_DISABLED = False
_CONN = None


def _connect():
    global _CONN, _DISABLED
    if _DISABLED:
        return None
    if _CONN is not None:
        return _CONN
    url = os.getenv("POSTGRES_URL")
    if not url:
        log.info("post_store: POSTGRES_URL unset, archive disabled")
        _DISABLED = True
        return None
    try:
        import psycopg2  # type: ignore
        _CONN = psycopg2.connect(url)
        _CONN.autocommit = True
        with _CONN.cursor() as cur:
            cur.execute(_DDL)
        log.info("post_store: connected and schema ensured")
        return _CONN
    except Exception as exc:
        log.warning("post_store: disabling archive (%s)", exc)
        _DISABLED = True
        _CONN = None
        return None


def _post_hash(platform: str, url: str, text: str, ts: str) -> str:
    payload = f"{platform}|{url}|{ts}|{text[:500]}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _parse_ts(value):
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None


def archive(enriched_records: Iterable[dict]) -> int:
    """Persist enriched pipeline records to social_v2_posts.

    Returns number of rows inserted (ignoring conflicts).
    """
    conn = _connect()
    if conn is None:
        return 0

    rows = []
    for record in enriched_records:
        post = record.get("post")
        if post is None:
            continue
        text = getattr(post, "text", "") or ""
        if not text.strip():
            continue
        url = getattr(post, "url", "") or ""
        platform = getattr(post, "platform", "") or ""
        ts_raw = getattr(post, "timestamp", "") or ""
        symbols = [m.symbol for m in record.get("mentions", []) or []]
        intents = list(record.get("intent", {}).get("intents") or [])
        sentiment = record.get("sentiment") or {}
        sectors, indices = _derive_tags(symbols, record.get("sector_mentions"))
        rows.append((
            _post_hash(platform, url, text, ts_raw),
            platform,
            getattr(post, "source", "") or "",
            url,
            getattr(post, "username", "") or "",
            _parse_ts(ts_raw),
            text[:4000],
            int(getattr(post, "engagement", 0) or 0),
            symbols or None,
            intents or None,
            record.get("content", {}).get("label"),
            float(sentiment.get("score", 0.0)) if sentiment else None,
            sentiment.get("label"),
            sectors or None,
            indices or None,
        ))

    if not rows:
        return 0

    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO social_v2_posts (
                    post_hash, platform, source, url, username,
                    post_timestamp, text, engagement, symbols,
                    intents, content_label, sentiment_score, sentiment_label,
                    sectors, indices
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (post_hash) DO NOTHING
                """,
                rows,
            )
        return len(rows)
    except Exception as exc:
        log.warning("post_store: insert failed (%s)", exc)
        return 0


def archive_news_articles(articles: Iterable[dict], ticker: str = "") -> int:
    """Persist News-Analyst articles into the shared social_v2_posts archive.

    These come from the News Analyst's live aggregator flow
    (``news_data_tools._fetch_live_news``), NOT the social_v2 sentiment
    pipeline. They are stored for archival / backtest reproducibility —
    symmetric to the social posts — and tagged ``platform='news'`` /
    ``content_label='NEWS'`` so they stay distinguishable.

    IMPORTANT — sentiment is stored as NULL on purpose. The News Analyst
    computes transformer sentiment POST-LLM on headlines extracted from its own
    report, not per source-article at fetch time, so there is no honest
    per-article score here. NULL also keeps these rows OUT of the
    social-sentiment backtest replay, whose reader filters
    ``WHERE sentiment_score IS NOT NULL`` (``signal_adapter._try_archive_replay``)
    — so archiving news does NOT alter the social signal. Opt-in scoring would
    require a dedicated sentiment pass + a backtest before it can feed the blend.

    Best-effort: no-op when Postgres is unavailable. Idempotent via post_hash.
    Returns the number of rows offered for insertion (ON CONFLICT DO NOTHING).
    """
    conn = _connect()
    if conn is None:
        return 0

    bare = (ticker or "").upper().replace(".CA", "").strip()
    # Market-wide news (ticker == "EGX") carries no per-stock symbol tag.
    symbols = [bare] if bare and bare != "EGX" else None
    sectors, indices = _derive_tags(symbols, None)

    rows = []
    for art in articles:
        if not isinstance(art, dict):
            continue
        title = (art.get("title") or "").strip()
        summary = (art.get("summary") or "").strip()
        text = (f"{title} — {summary}" if summary else title).strip()
        if not text:
            continue
        url = art.get("url") or ""
        ts_raw = art.get("published_at") or ""
        rows.append((
            _post_hash("news", url, text, str(ts_raw)),
            "news",                       # platform
            art.get("source") or "news",  # source (outlet/provider)
            url,
            None,                         # username — news has no author handle
            _parse_ts(ts_raw),
            text[:4000],
            0,                            # engagement — N/A for news
            symbols,
            None,                         # intents — not classified in this flow
            "NEWS",                       # content_label
            None,                         # sentiment_score — NULL on purpose
            None,                         # sentiment_label — NULL on purpose
            sectors or None,
            indices or None,
        ))

    if not rows:
        return 0

    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO social_v2_posts (
                    post_hash, platform, source, url, username,
                    post_timestamp, text, engagement, symbols,
                    intents, content_label, sentiment_score, sentiment_label,
                    sectors, indices
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (post_hash) DO NOTHING
                """,
                rows,
            )
        return len(rows)
    except Exception as exc:
        log.warning("post_store: news insert failed (%s)", exc)
        return 0
