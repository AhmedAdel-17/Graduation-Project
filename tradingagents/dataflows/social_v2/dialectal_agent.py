"""Dialectal Egyptian-Arabic (Ammiya) sentiment agent for EGX retail chatter.

This is the one salvageable idea from an external "5-module pipeline" proposal:
a chat-LLM agent that understands Egyptian trading-floor slang that the
model-based sentiment engine (FinBERT / CAMeLBERT / VADER in
``tradingagents.utils.sentiment_engine``) systematically mis-reads — e.g.
``تجميع`` (accumulation, bullish), ``تصريف`` / ``لبسنا`` (distribution / "we
got dumped on", bearish), ``السهم طاير`` (strong momentum).

It is an OPTIONAL enrichment layer, NOT a replacement for the deterministic
sentiment engine and NOT on the trading-graph decision path. It exists so the
social_v2 pipeline can attach a dialect-aware reading to high-signal posts.

Design notes (project conventions):
  * Ticker detection reuses :func:`social_v2.entities.extract` /
    ``primary_symbol`` — we do NOT reimplement a ticker dictionary.
  * Arabic normalization reuses ``text_preprocessor.normalize_text``.
  * LLM determinism (CLAUDE.md rule #8): every call pins ``temperature=0`` and
    a fixed ``seed`` (``llm_seed`` config / ``LLM_SEED`` env, default 42).
    Groq's OpenAI-compatible endpoint honours both.
  * Reuses the repo's Groq settings (base_url + ``groq_model`` default
    ``llama-3.3-70b-versatile``). The key is read from ``GROQ_API_KEY``, then
    ``OPENAI_API_KEY`` (which maps to Groq in this repo's ``.env``).
  * Fail-closed: any network / quota / parse error returns a neutral, zero-
    confidence verdict rather than raising into the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Iterable, List, Optional

import httpx

try:  # .env is loaded centrally elsewhere, but be safe when run standalone.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass

from tradingagents.dataflows.social_v2 import entities
from tradingagents.utils.text_preprocessor import normalize_text

log = logging.getLogger("tradingagents.social_v2.dialectal_agent")

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
DEFAULT_TIMEOUT = float(os.getenv("DIALECTAL_AGENT_TIMEOUT", "20"))
_DEFAULT_SEED = int(os.getenv("LLM_SEED", "42"))

# Compact, auditable glossary the model is told to apply verbatim. Kept here
# (not buried in the prompt string) so it can be reviewed/extended on its own.
_AMMIYA_GLOSSARY = [
    ("تجميع", "accumulation by big players", "bullish"),
    ("السهم طاير / خانق", "strong upward momentum", "bullish"),
    ("السيولة داخلة", "liquidity flowing in", "bullish"),
    ("تصريف / تنفيذ في المليان", "distribution / dumping", "bearish"),
    ("لبسنا / لبسنا في الحيط", "we got trapped / dumped on", "bearish"),
    ("الحق اجري", "panic sell / rush to exit", "bearish"),
    ("مخروب / بيصيحوا", "capitulation / heavy fear", "bearish"),
    ("استني تصحيح / السهم غالي", "wait for a pullback / overpriced", "bearish-lean"),
]

_GLOSSARY_BLOCK = "\n".join(
    f"  - '{term}' -> {meaning} ({lean})" for term, meaning, lean in _AMMIYA_GLOSSARY
)

SYSTEM_PROMPT = (
    "You are an expert quantitative analyst for the Egyptian Exchange (EGX), "
    "fluent in Egyptian Arabic (Ammiya) and retail trading-floor slang. "
    "Read the post about the given ticker and judge the author's market "
    "sentiment toward that ticker.\n\n"
    "Apply this slang glossary literally:\n"
    f"{_GLOSSARY_BLOCK}\n\n"
    "Rules:\n"
    "  - Judge sentiment ONLY toward the target ticker, not the whole market.\n"
    "  - If the post is a question, news headline, or has no clear stance, "
    "return sentiment_score 0.0 and low confidence.\n"
    "  - sentiment_score: float in [-1.0, 1.0] (negative = bearish, positive = bullish).\n"
    "  - confidence: float in [0.0, 1.0].\n"
    "  - context_reason: one short English sentence citing the slang/phrase you keyed on.\n\n"
    "Respond with ONE JSON object and nothing else, matching exactly:\n"
    '{"ticker": "string", "sentiment_score": 0.0, "confidence": 0.0, "context_reason": "string"}'
)


@dataclass
class DialectalVerdict:
    """Structured output of the dialectal agent."""

    ticker: str
    sentiment_score: float
    confidence: float
    context_reason: str
    model_used: str = DEFAULT_MODEL

    def to_dict(self) -> dict:
        return asdict(self)


def _resolve_api_key(explicit: Optional[str] = None) -> Optional[str]:
    """GROQ_API_KEY first, then OPENAI_API_KEY (maps to Groq in this repo)."""
    return explicit or os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")


def detect_ticker(text: str) -> Optional[str]:
    """Reuse the project entity extractor; return the primary stock symbol."""
    mentions = entities.extract(text or "")
    return entities.primary_symbol(mentions)


def _neutral(ticker: str, reason: str) -> DialectalVerdict:
    return DialectalVerdict(
        ticker=ticker or "UNKNOWN",
        sentiment_score=0.0,
        confidence=0.0,
        context_reason=reason,
    )


def _clamp(value: object, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _parse_response(payload: dict, ticker: str, model: str) -> DialectalVerdict:
    content = payload["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return DialectalVerdict(
        ticker=str(parsed.get("ticker") or ticker or "UNKNOWN"),
        sentiment_score=_clamp(parsed.get("sentiment_score"), -1.0, 1.0),
        confidence=_clamp(parsed.get("confidence"), 0.0, 1.0),
        context_reason=str(parsed.get("context_reason") or "").strip()[:400],
        model_used=model,
    )


async def analyze_dialectal_sentiment(
    text: str,
    ticker: Optional[str] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    seed: int = _DEFAULT_SEED,
    timeout: float = DEFAULT_TIMEOUT,
) -> DialectalVerdict:
    """Analyze one post's dialectal sentiment toward ``ticker``.

    ``ticker`` is auto-detected from the text when omitted. Fail-closed: any
    error returns a neutral, zero-confidence verdict.
    """
    ticker = ticker or detect_ticker(text) or "UNKNOWN"

    key = _resolve_api_key(api_key)
    if not key:
        log.warning("dialectal_agent: no GROQ_API_KEY/OPENAI_API_KEY set; skipping LLM call")
        return _neutral(ticker, "No Groq API key configured.")

    normalized = normalize_text(text or "")
    if not normalized.strip():
        return _neutral(ticker, "Empty post text.")

    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Target ticker: {ticker}\nPost: {normalized}"},
        ],
        "temperature": 0,
        "seed": seed,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        response = await http.post(GROQ_ENDPOINT, headers=headers, json=request_body)
        response.raise_for_status()
        return _parse_response(response.json(), ticker, model)
    except httpx.HTTPStatusError as exc:
        log.error("dialectal_agent: Groq HTTP %s", exc.response.status_code)
        return _neutral(ticker, f"Groq HTTP error {exc.response.status_code}.")
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        log.error("dialectal_agent: network error: %s", exc)
        return _neutral(ticker, "Network error contacting Groq.")
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        log.error("dialectal_agent: malformed model output: %s", exc)
        return _neutral(ticker, "Malformed model output.")
    finally:
        if owns_client:
            await http.aclose()


async def analyze_batch(
    texts: Iterable[str],
    *,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    seed: int = _DEFAULT_SEED,
    timeout: float = DEFAULT_TIMEOUT,
    concurrency: int = 4,
) -> List[DialectalVerdict]:
    """Analyze many posts concurrently over a shared client (bounded)."""
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return []

    key = _resolve_api_key(api_key)
    if not key:
        log.warning("dialectal_agent: no API key; returning neutral verdicts for batch")
        return [_neutral(detect_ticker(t) or "UNKNOWN", "No Groq API key configured.") for t in texts]

    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with httpx.AsyncClient(timeout=timeout) as http:

        async def _one(t: str) -> DialectalVerdict:
            async with semaphore:
                return await analyze_dialectal_sentiment(
                    t, client=http, api_key=key, model=model, seed=seed, timeout=timeout
                )

        return await asyncio.gather(*(_one(t) for t in texts))


def analyze_sync(text: str, ticker: Optional[str] = None, **kwargs) -> DialectalVerdict:
    """Blocking convenience wrapper for non-async callers."""
    return asyncio.run(analyze_dialectal_sentiment(text, ticker, **kwargs))


if __name__ == "__main__":  # Manual smoke test against real Ammiya samples.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    samples = [
        "يا جماعة سهم فوري مسك دعم قوي وفيه تجميع واضح من الكبار، شكله طاير",
        "مجموعة طلعت مصطفى شكلها بتصرّف علينا، لبسنا في الحيط خلاص",
        "البنك التجاري الدولي CIB مستقر فوق الدعم، السوق لسه فيه خير",
        "حد يعرف ميعاد الجمعية العمومية لسهم سويدي؟",  # question -> should be ~neutral
    ]

    async def _demo() -> None:
        verdicts = await analyze_batch(samples)
        print("\n" + "=" * 72)
        print("        DIALECTAL EGYPTIAN-ARABIC EGX SENTIMENT — SMOKE TEST")
        print("=" * 72)
        for src, v in zip(samples, verdicts):
            print(f"\nPOST     : {src}")
            print(f"TICKER   : {v.ticker}")
            print(f"SCORE    : {v.sentiment_score:+.2f}  [-1 bearish .. +1 bullish]")
            print(f"CONF     : {v.confidence:.2f}")
            print(f"REASON   : {v.context_reason}")
        print("=" * 72)

    asyncio.run(_demo())
