"""Input sanitizer for external text entering LLM prompts.

Applies to news articles, social media posts, and any other user-generated
or web-scraped content.  Goal: reduce prompt-injection surface area without
corrupting legitimate financial text (Arabic or English).
"""

import re
import html as _html
from typing import Optional

# ── Injection patterns (case-insensitive) ────────────────────────────────────
# Phrases commonly used in prompt-injection attacks.  We replace them with a
# benign marker so the LLM sees that something was redacted rather than silently
# missing text.
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"ignore\s+(all\s+)?above\s+instructions",
        r"forget\s+(all\s+)?previous\s+instructions",
        r"forget\s+(your|all)\s+instructions",
        r"disregard\s+(all\s+)?previous",
        r"override\s+(all\s+)?instructions",
        r"you\s+are\s+now\s+a",
        r"new\s+instructions?\s*:",
        r"system\s*:\s*",
        r"<\s*/?\s*system\s*>",
        r"\[system\]",
        r"```\s*system",
    ]
]

_INJECTION_MARKER = "[redacted-injection-attempt]"

# ── HTML tag stripper ────────────────────────────────────────────────────────
_HTML_TAG_RE = re.compile(r"<[^>]{1,200}>")


def sanitize_external_text(
    text: str,
    *,
    max_length: int = 4000,
    strip_html: bool = True,
    neutralize_injections: bool = True,
) -> str:
    """Sanitize external text before it enters an LLM prompt.

    Operations (in order):
    1. Injection detection pass 1 (catches ``<system>`` tags before HTML strip)
    2. HTML entity decode  (``&amp;`` -> ``&``, ``&#111;`` -> ``o``)
    3. Injection detection pass 2 (catches phrases that were entity-encoded)
    4. Strip HTML tags     (``<script>…</script>`` -> ``…``)
    5. Truncate to *max_length* characters
    6. Collapse excessive whitespace

    Normal financial text in Arabic or English passes through unchanged
    (modulo HTML cleaning and length).
    """
    if not text:
        return ""

    result = text

    # 1. Neutralize injection patterns BEFORE stripping HTML, so patterns
    #    inside HTML tags (e.g. <system>…</system>) are caught.
    if neutralize_injections:
        for pattern in _INJECTION_PATTERNS:
            result = pattern.sub(_INJECTION_MARKER, result)

    # 2. Decode HTML entities so entity-encoded text is readable
    result = _html.unescape(result)

    # 3. Second injection pass — catches phrases that were entity-encoded
    #    (e.g. "ign&#111;re previous instructions" → "ignore previous instructions")
    if neutralize_injections:
        for pattern in _INJECTION_PATTERNS:
            result = pattern.sub(_INJECTION_MARKER, result)

    # 4. Strip HTML tags
    if strip_html:
        result = _HTML_TAG_RE.sub("", result)

    # 4. Truncate
    if len(result) > max_length:
        result = result[:max_length] + " [truncated]"

    # 5. Collapse runs of whitespace (but preserve single newlines)
    result = re.sub(r"[^\S\n]+", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result.strip()


def wrap_external_content(text: str, source_label: Optional[str] = None) -> str:
    """Wrap sanitized text in delimiters so the LLM can distinguish it from instructions.

    Parameters
    ----------
    text : str
        Already-sanitized text (call ``sanitize_external_text`` first).
    source_label : str, optional
        Human-readable source identifier (e.g. ``"Reuters"``, ``"Facebook post"``).
    """
    label = f" ({source_label})" if source_label else ""
    return (
        f"[EXTERNAL_CONTENT{label}]\n"
        f"{text}\n"
        f"[/EXTERNAL_CONTENT]"
    )
