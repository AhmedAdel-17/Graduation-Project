"""On-demand agent-output translation service (EN → Egyptian Arabic).

The dashboard's *static* UI is translated client-side via a dictionary. This
module handles the *dynamic* LLM-generated agent prose (bull/bear theses, the
research-manager verdict, reasoning summaries, news) which a static dictionary
cannot cover.

Design (see CLAUDE.md / MEMORY.md project_dashboard_arabic_i18n):
  - English stays the source of truth; we translate on demand.
  - Cache-first: results are keyed by ``sha256(source)`` + target locale in the
    Postgres ``translation_cache`` table, so repeat views and historical runs
    cost nothing and are deterministic. Missing/unreachable Postgres degrades to
    "translate every time" rather than failing.
  - Batched: all cache-miss blocks in one request go through a single LLM call.
  - Egyptian dialect: the prompt pins العامية المصرية, preserves markdown
    structure, and keeps numbers / tickers / finance acronyms untouched.
  - Graceful: any LLM failure returns the original English so the UI never blanks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger("tradingagents.server.translation")

# Only Arabic is offered in the UI today; guard so an unknown target is a no-op
# (returns source) rather than an accidental cross-language translation.
SUPPORTED_TARGETS = {"ar"}

# Cap batch + block sizes so a runaway request can't build a giant prompt.
_MAX_BLOCKS = 40
_MAX_BLOCK_CHARS = 16_000

# One LLM call must emit ALL its translations as JSON within the model's output
# budget. A long agent thesis translated to (longer) Arabic easily exceeds 4096
# tokens, which truncates the JSON → parse/count failure → English fallback. So
# we split large blocks into paragraph segments and translate them in batches
# capped to this many characters, keeping every response comfortably in-budget.
_BATCH_CHAR_BUDGET = 2200
# Segments larger than this are still sent (can't split further) but alone.
_SEGMENT_SPLIT_THRESHOLD = 1600
_MAX_TRANSLATE_WORKERS = 6

TRANSLATION_SYSTEM_PROMPT = """\
You are a professional financial translator for an EGX (Egyptian Exchange)
research tool. Translate each input text block from English into EGYPTIAN ARABIC
(العامية المصرية) — natural and fluent, the way an Egyptian financial analyst
actually writes — NOT Modern Standard Arabic.

RULES:
1. Preserve Markdown structure EXACTLY (headings #, list markers - / *, tables |,
   bold **, blockquotes >, code fences ```). Translate only the human-readable text.
2. Do NOT translate: numbers, percentages, dates, currency amounts, ticker symbols
   (e.g. COMI.CA), and standard finance acronyms (EGX, EGP, RSI, MACD, ATR, SMA,
   EMA, P/E, P/B, ROE, ROA, ADV, T+2, BUY, SELL, HOLD).
3. Keep EXACTLY the same number of blocks, in the same order. Never merge or split.
4. Translate faithfully — do not add, remove, summarize, or comment.
5. Output ONLY JSON: {"translations": ["<block 1>", "<block 2>", ...]} with exactly
   as many items as the input array, in order.
"""

# Lazily-built LLM. Uses the same resilient multi-provider failover the agent
# graph relies on (NVIDIA → DeepSeek → Google via safe_invoke), NOT the
# NVIDIA-only conversational boundary — so a slow/unreachable provider fails over
# instead of hanging. Built once per process.
_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        from tradingagents.agents.utils.llm_failover import build_resilient_llm
        from tradingagents.default_config import DEFAULT_CONFIG

        # Generous output budget; batches are additionally char-capped so a
        # single JSON response never truncates mid-token.
        _llm = build_resilient_llm(
            DEFAULT_CONFIG, role="quick", seed=int(DEFAULT_CONFIG.get("llm_seed", 42)),
            max_tokens=8000,
        )
    return _llm


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_get(hashes: list[str], target: str) -> dict[str, str]:
    """Return {content_hash: translated_text} for whatever is already cached.

    Silent no-op ({}) when Postgres is unavailable.
    """
    if not hashes:
        return {}
    try:
        from tradingagents.db import connection as db

        if not db.is_postgres_available():
            return {}
        with db.cursor() as cur:
            cur.execute(
                "SELECT content_hash, translated_text FROM translation_cache "
                "WHERE target_locale = %s AND content_hash = ANY(%s)",
                (target, hashes),
            )
            return {row[0]: row[1] for row in cur.fetchall()}
    except Exception as exc:  # pragma: no cover — cache is best-effort
        logger.warning("translation cache read failed: %s", exc)
        return {}


def _cache_put(rows: list[tuple[str, str, str]]) -> None:
    """Persist [(content_hash, target_locale, translated_text), ...]. Best-effort."""
    if not rows:
        return
    try:
        from tradingagents.db import connection as db

        if not db.is_postgres_available():
            return
        with db.cursor() as cur:
            cur.executemany(
                "INSERT INTO translation_cache (content_hash, target_locale, translated_text) "
                "VALUES (%s, %s, %s) ON CONFLICT (content_hash, target_locale) DO NOTHING",
                rows,
            )
    except Exception as exc:  # pragma: no cover — cache is best-effort
        logger.warning("translation cache write failed: %s", exc)


def _llm_translate(blocks: list[str]) -> Optional[list[str]]:
    """Translate a batch via one LLM call. Returns None on any failure/mismatch."""
    from tradingagents.portfolio.llm_boundary import invoke_json

    payload = json.dumps({"blocks": blocks}, ensure_ascii=False)
    user_prompt = (
        "Translate every string in blocks[] to Egyptian Arabic following the rules. "
        "Return the same number of items, in order.\n\n" + payload
    )
    try:
        raw = invoke_json(
            _get_llm(),
            TRANSLATION_SYSTEM_PROMPT,
            user_prompt,
            agent_name="server.translation",
        )
    except Exception as exc:
        logger.warning("translation LLM failed: %s", exc)
        return None

    out = raw.get("translations")
    if not isinstance(out, list) or len(out) != len(blocks):
        logger.warning(
            "translation LLM returned %s items, expected %d",
            len(out) if isinstance(out, list) else type(out).__name__,
            len(blocks),
        )
        return None
    return [str(x) for x in out]


def _split_block(text: str) -> list[str]:
    """Split a block into paragraph segments on blank lines, KEEPING the
    separators (odd indices) so it rejoins losslessly. Short blocks return
    ``[text]``. Preserves markdown: tables/lists (no blank line inside) stay
    intact within one segment."""
    if len(text) <= _SEGMENT_SPLIT_THRESHOLD:
        return [text]
    return re.split(r"(\n[ \t]*\n)", text)


def _translate_missing(texts: list[str]) -> list[str]:
    """Translate each block — splitting large blocks into paragraph segments and
    translating segments in char-capped batches CONCURRENTLY, then reassembling.

    A batch whose LLM call fails leaves its segments as the source English
    (per-block graceful degradation) — never blank, never a wrong count. This is
    what keeps a long thesis from silently falling back to English wholesale when
    a single big JSON response would have blown the output-token budget.
    """
    # 1) Explode blocks into segments; record a rebuild plan per block.
    seg_texts: list[str] = []
    plan: list[list[tuple[str, object]]] = []
    for text in texts:
        parts = _split_block(text)
        block_plan: list[tuple[str, object]] = []
        for i, part in enumerate(parts):
            is_separator = len(parts) > 1 and i % 2 == 1
            if is_separator or not part.strip():
                block_plan.append(("lit", part))
            else:
                block_plan.append(("seg", len(seg_texts)))
                seg_texts.append(part)
        plan.append(block_plan)

    # 2) Group segments into char-capped batches so each JSON response fits.
    batches: list[tuple[int, list[str]]] = []  # (start index in seg_texts, texts)
    cur: list[str] = []
    cur_start = 0
    cur_chars = 0
    for i, s in enumerate(seg_texts):
        if cur and cur_chars + len(s) > _BATCH_CHAR_BUDGET:
            batches.append((cur_start, cur))
            cur, cur_start, cur_chars = [], i, 0
        if not cur:
            cur_start = i
        cur.append(s)
        cur_chars += len(s)
    if cur:
        batches.append((cur_start, cur))

    # 3) Translate batches concurrently; default to source on failure.
    translated: list[str] = list(seg_texts)
    if batches:
        def _run(batch: tuple[int, list[str]]):
            start, texts_b = batch
            return start, _llm_translate(texts_b)

        with ThreadPoolExecutor(max_workers=min(_MAX_TRANSLATE_WORKERS, len(batches))) as ex:
            for start, out in ex.map(_run, batches):
                if out is not None:
                    for j, tr in enumerate(out):
                        translated[start + j] = tr

    # 4) Reassemble each block from its plan.
    results: list[str] = []
    for block_plan in plan:
        buf: list[str] = []
        for kind, val in block_plan:
            buf.append(val if kind == "lit" else translated[val])  # type: ignore[index]
        results.append("".join(buf))
    return results


def translate_blocks(blocks: list[str], target: str = "ar") -> list[str]:
    """Translate ``blocks`` to ``target`` (Egyptian Arabic), cache-first.

    Always returns a list of the same length/order as the input. Blank blocks and
    unsupported targets pass through unchanged; on any LLM failure the original
    English is returned for the affected blocks so the caller never gets a blank.
    """
    if not isinstance(blocks, list):
        raise ValueError("blocks must be a list of strings")
    if target not in SUPPORTED_TARGETS:
        return list(blocks)
    if len(blocks) > _MAX_BLOCKS:
        raise ValueError(f"too many blocks (max {_MAX_BLOCKS})")

    # Normalize + decide which blocks are actually translatable.
    result: list[Optional[str]] = [None] * len(blocks)
    translatable_idx: list[int] = []
    hashes: list[str] = []
    for i, b in enumerate(blocks):
        text = b if isinstance(b, str) else str(b)
        if not text.strip():
            result[i] = text  # nothing to translate
            continue
        if len(text) > _MAX_BLOCK_CHARS:
            text = text[:_MAX_BLOCK_CHARS]
        translatable_idx.append(i)
        hashes.append(_hash(text))
        result[i] = text  # provisional (English) — overwritten if translated

    # 1) Serve from cache.
    cached = _cache_get(hashes, target)
    misses: list[int] = []          # indices (into blocks) still needing translation
    miss_hashes: list[str] = []
    miss_texts: list[str] = []
    for pos, i in enumerate(translatable_idx):
        h = hashes[pos]
        if h in cached:
            result[i] = cached[h]
        else:
            misses.append(i)
            miss_hashes.append(h)
            miss_texts.append(result[i] or "")

    # 2) Translate cache misses — large blocks are split + batched internally,
    #    so a long thesis never truncates the JSON and falls back wholesale.
    if miss_texts:
        translated = _translate_missing(miss_texts)
        new_rows: list[tuple[str, str, str]] = []
        for j, i in enumerate(misses):
            result[i] = translated[j]
            # Only cache blocks that actually changed; a block that came back
            # identical was a failed attempt and should be retried on next view.
            if translated[j] != miss_texts[j]:
                new_rows.append((miss_hashes[j], target, translated[j]))
        _cache_put(new_rows)

    return [r if r is not None else "" for r in result]


__all__ = ["translate_blocks", "SUPPORTED_TARGETS"]
