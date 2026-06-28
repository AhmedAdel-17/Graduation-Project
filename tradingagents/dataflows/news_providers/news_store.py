"""Postgres archive for categorized news articles.

Mirrors the social_v2 post_store pattern but for structured news. Each article
is enriched via the social_v2 entity / sector / index / event extractors so the
agent can query by any axis:

    - Which sector does this article relate to?
    - Which index (EGX30/70/100)?
    - Which company/stock?
    - Is this whole-market news?

Schema auto-creates on first connect (idempotent DDL).
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Iterable, List, Optional

log = logging.getLogger("tradingagents.news.news_store")

_DDL = """
CREATE TABLE IF NOT EXISTS news_archive (
    id              BIGSERIAL PRIMARY KEY,
    article_hash    TEXT UNIQUE NOT NULL,
    source          TEXT NOT NULL,
    language        TEXT,
    title           TEXT NOT NULL,
    summary         TEXT,
    url             TEXT,
    published_at    TIMESTAMPTZ,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbols         TEXT[],
    sectors         TEXT[],
    indices         TEXT[],
    events          TEXT[],
    is_market_wide  BOOLEAN DEFAULT FALSE,
    sentiment_score REAL,
    sentiment_label TEXT,
    intents         TEXT[],
    content_label   TEXT
);
CREATE INDEX IF NOT EXISTS news_archive_pub_idx
    ON news_archive (published_at);
CREATE INDEX IF NOT EXISTS news_archive_symbols_idx
    ON news_archive USING GIN (symbols);
CREATE INDEX IF NOT EXISTS news_archive_sectors_idx
    ON news_archive USING GIN (sectors);
CREATE INDEX IF NOT EXISTS news_archive_indices_idx
    ON news_archive USING GIN (indices);
CREATE INDEX IF NOT EXISTS news_archive_events_idx
    ON news_archive USING GIN (events);
CREATE INDEX IF NOT EXISTS news_archive_market_wide_idx
    ON news_archive (is_market_wide) WHERE is_market_wide;
"""

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
        log.info("news_store: POSTGRES_URL unset, archive disabled")
        _DISABLED = True
        return None
    try:
        import psycopg2
        _CONN = psycopg2.connect(url)
        _CONN.autocommit = True
        with _CONN.cursor() as cur:
            cur.execute(_DDL)
        log.info("news_store: connected and schema ensured")
        return _CONN
    except Exception as exc:
        log.warning("news_store: disabling archive (%s)", exc)
        _DISABLED = True
        _CONN = None
        return None


def _article_hash(source: str, title: str, url: str) -> str:
    payload = f"{source}|{title[:200]}|{url}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _derive_tags(text: str) -> dict:
    """Run the social_v2 enrichment extractors on article text.

    Returns dict with keys: symbols, sectors, indices, events, is_market_wide.
    """
    symbols: List[str] = []
    sectors: set = set()
    indices: set = set()
    events: list = []
    is_market_wide = False

    try:
        from tradingagents.dataflows.social_v2.entities import (
            extract as extract_entities,
            has_market_term,
        )
        mentions = extract_entities(text)
        symbols = [m.symbol for m in mentions]
        is_market_wide = has_market_term(text)
    except Exception:
        pass

    try:
        from tradingagents.dataflows.social_v2.sectors import extract_sector_mentions
        sec_mentions = extract_sector_mentions(text)
        for sm in sec_mentions:
            name = getattr(sm, "sector", None) or (sm if isinstance(sm, str) else None)
            if name:
                sectors.add(str(name).lower())
    except Exception:
        pass

    try:
        from tradingagents.sentiment.taxonomy import (
            SectorEnum,
            ticker_to_indices,
            ticker_to_sector,
        )
        for sym in symbols:
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

    try:
        from tradingagents.dataflows.social_v2.events import extract_events
        ev_mentions = extract_events(text)
        events = [str(e.event if hasattr(e, "event") else e) for e in ev_mentions]
    except Exception:
        pass

    return {
        "symbols": symbols,
        "sectors": sorted(sectors),
        "indices": sorted(indices),
        "events": events,
        "is_market_wide": is_market_wide or bool(
            any(s.startswith("EGX_") for s in symbols)
        ),
    }


def archive(articles: Iterable[dict]) -> int:
    """Persist enriched news articles to news_archive.

    Each article dict should have at minimum:
        title, source, and optionally: summary, url, published_at, language,
        symbols, sectors, indices, events, is_market_wide,
        sentiment_score, sentiment_label, intents, content_label.

    If categorization fields are missing, they are derived from title+summary text.

    Returns number of rows inserted (ignoring conflicts).
    """
    conn = _connect()
    if conn is None:
        return 0

    rows = []
    for art in articles:
        title = art.get("title", "").strip()
        if not title:
            continue
        source = art.get("source", "unknown")
        url = art.get("url", "")
        summary = art.get("summary", "")

        full_text = f"{title} {summary}".strip()
        tags = art if "symbols" in art else _derive_tags(full_text)

        rows.append((
            _article_hash(source, title, url),
            source,
            art.get("language"),
            title[:1000],
            (summary or "")[:4000],
            url,
            _parse_ts(art.get("published_at")),
            tags.get("symbols") or None,
            tags.get("sectors") or None,
            tags.get("indices") or None,
            tags.get("events") or None,
            tags.get("is_market_wide", False),
            float(art["sentiment_score"]) if art.get("sentiment_score") is not None else None,
            art.get("sentiment_label"),
            art.get("intents") or None,
            art.get("content_label"),
        ))

    if not rows:
        return 0

    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO news_archive (
                    article_hash, source, language, title, summary, url,
                    published_at, symbols, sectors, indices, events,
                    is_market_wide, sentiment_score, sentiment_label,
                    intents, content_label
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (article_hash) DO NOTHING
                """,
                rows,
            )
        inserted = len(rows)
        log.info("news_store: %d rows offered (conflicts silently skipped)", inserted)
        return inserted
    except Exception as exc:
        log.warning("news_store: insert failed (%s)", exc)
        return 0


def stats() -> dict:
    """Report archive health — total rows, recent count, top symbols."""
    conn = _connect()
    if conn is None:
        return {"error": "not connected"}
    try:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=8)).date().isoformat()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM news_archive")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM news_archive WHERE published_at >= %s",
                (cutoff,),
            )
            recent = cur.fetchone()[0]
            cur.execute(
                """
                SELECT unnest(symbols) AS sym, count(*) c
                FROM news_archive
                WHERE published_at >= %s AND symbols IS NOT NULL
                GROUP BY sym ORDER BY c DESC LIMIT 10
                """,
                (cutoff,),
            )
            top_symbols = cur.fetchall()
            cur.execute(
                """
                SELECT unnest(sectors) AS sec, count(*) c
                FROM news_archive
                WHERE published_at >= %s AND sectors IS NOT NULL
                GROUP BY sec ORDER BY c DESC LIMIT 8
                """,
                (cutoff,),
            )
            top_sectors = cur.fetchall()
            cur.execute(
                "SELECT count(*) FROM news_archive WHERE is_market_wide AND published_at >= %s",
                (cutoff,),
            )
            market_wide = cur.fetchone()[0]
        return {
            "total": total,
            "recent_8d": recent,
            "market_wide_8d": market_wide,
            "top_symbols": {s: c for s, c in top_symbols},
            "top_sectors": {s: c for s, c in top_sectors},
        }
    except Exception as exc:
        return {"error": str(exc)}
