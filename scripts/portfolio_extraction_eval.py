"""Portfolio extraction mini-eval (roadmap P9, optional thesis artifact).

Reports precision / recall / F1 on a labeled set of 30 bilingual (EN + Egyptian
Arabic) portfolio messages for three extracted fields: **tickers**, **prices**
(avg_cost) and **percentages** (weight_pct). Emits a Markdown table.

Two modes:

* ``--resolver`` (default, **runs offline, no LLM/key**): replays each case's
  *labeled LLM candidates* through ``PortfolioExtractionAgent`` so the score
  isolates the deterministic, registry-gated **resolution + arithmetic** layer —
  the safety-critical half of extraction. This is the number you can reproduce on
  any machine and cite as "the resolver never emits a non-registry ticker".
* ``--live`` (**requires the conversational key**, ``EGX_LIVE_LLM=1`` +
  ``NVIDIA_API_KEY``): runs the real agent on the *raw text*, scoring end-to-end
  LLM extraction accuracy. This is the model-quality number for the thesis.

Usage::

    python scripts/portfolio_extraction_eval.py                 # resolver mode
    EGX_LIVE_LLM=1 python scripts/portfolio_extraction_eval.py --live
    python scripts/portfolio_extraction_eval.py --out results/extraction_eval.md
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingagents.portfolio.extraction import PortfolioExtractionAgent

_TOL = 1e-6


@dataclass
class Case:
    lang: str
    text: str
    candidates: list[dict]   # the labeled LLM-candidate holdings
    expected: list[str]      # expected resolved tickers (aligned to candidates)


# 30 labeled cases (15 EN + 15 AR) — mirrors tests/test_portfolio_extraction.py.
EN = [
    ("I own 25% CIB bought at 72 and 10% Fawry at 8.5.", [{"name": "CIB", "weight_pct": 25, "avg_cost": 72}, {"name": "Fawry", "weight_pct": 10, "avg_cost": 8.5}], ["COMI.CA", "FWRY.CA"]),
    ("Telecom Egypt is 300 shares, avg 38.", [{"name": "Telecom Egypt", "shares": 300, "avg_cost": 38}], ["ETEL.CA"]),
    ("TMG is half my account.", [{"name": "TMG", "weight_pct": 50}], ["TMGH.CA"]),
    ("I have 1000 shares in Orascom Construction.", [{"name": "Orascom Construction", "shares": 1000}], ["ORAS.CA"]),
    ("EFG Hermes is 15 percent.", [{"name": "EFG Hermes", "weight_pct": 15}], ["HRHO.CA"]),
    ("Palm Hills 20%, Sodic 10%.", [{"name": "Palm Hills", "weight_pct": 20}, {"name": "Sodic", "weight_pct": 10}], ["PHDC.CA", "OCDI.CA"]),
    ("Edita and Juhayna, 5% each.", [{"name": "Edita", "weight_pct": 5}, {"name": "Juhayna", "weight_pct": 5}], ["EFID.CA", "JUFO.CA"]),
    ("Abu Qir fertilizers 200 shares.", [{"name": "Abu Qir", "shares": 200}], ["ABUK.CA"]),
    ("QNB AlAhli 7%.", [{"name": "QNB AlAhli", "weight_pct": 7}], ["QNBE.CA"]),
    ("Beltone 11% and CI Capital 4%.", [{"name": "Beltone", "weight_pct": 11}, {"name": "CI Capital", "weight_pct": 4}], ["BTFH.CA", "CICH.CA"]),
    ("E-finance 6%.", [{"name": "e-finance", "weight_pct": 6}], ["EFIH.CA"]),
    ("Madinet Masr 9%.", [{"name": "Madinet Masr", "weight_pct": 9}], ["MASR.CA"]),
    ("Rameda 3%.", [{"name": "Rameda", "weight_pct": 3}], ["RMDA.CA"]),
    ("Domty 250 shares.", [{"name": "Domty", "shares": 250}], ["DOMT.CA"]),
    ("I keep 50000 EGP cash and 12% COMI.", [{"ticker": "COMI", "weight_pct": 12}], ["COMI.CA"]),
]
AR = [
    ("عندي ٢٠٪ في فوري و ١٠٪ في CIB", [{"name": "فوري", "weight_pct": 20}, {"name": "CIB", "weight_pct": 10}], ["FWRY.CA", "COMI.CA"]),
    ("المصرية للاتصالات ٣٠٠ سهم", [{"name": "ETEL", "shares": 300}], ["ETEL.CA"]),
    ("طلعت مصطفى نص المحفظة", [{"name": "TMG", "weight_pct": 50}], ["TMGH.CA"]),
    ("اوراسكوم للانشاء عندي فيها ١٥٪", [{"name": "Orascom", "weight_pct": 15}], ["ORAS.CA"]),
    ("هيرميس ٨٪", [{"name": "EFG", "weight_pct": 8}], ["HRHO.CA"]),
    ("بالم هيلز ٧٪ وسوديك ٦٪", [{"name": "Palm Hills", "weight_pct": 7}, {"name": "Sodic", "weight_pct": 6}], ["PHDC.CA", "OCDI.CA"]),
    ("ايديتا وجهينة خمسة في المية لكل واحد", [{"name": "Edita", "weight_pct": 5}, {"name": "Juhayna", "weight_pct": 5}], ["EFID.CA", "JUFO.CA"]),
    ("ابو قير ٢٠٠ سهم", [{"name": "Abu Qir", "shares": 200}], ["ABUK.CA"]),
    ("كيو ان بي ٩٪", [{"name": "QNB", "weight_pct": 9}], ["QNBE.CA"]),
    ("بلتون ١١٪ وسي اي كابيتال ٤٪", [{"name": "Beltone", "weight_pct": 11}, {"name": "CI Capital", "weight_pct": 4}], ["BTFH.CA", "CICH.CA"]),
    ("اي فاينانس ٦٪", [{"name": "EFINANCE", "weight_pct": 6}], ["EFIH.CA"]),
    ("مدينة مصر ٩٪", [{"name": "MNHD", "weight_pct": 9}], ["MASR.CA"]),
    ("راميدا ٣٪", [{"name": "Rameda", "weight_pct": 3}], ["RMDA.CA"]),
    ("دومتي ٢٥٠ سهم", [{"name": "Domty", "shares": 250}], ["DOMT.CA"]),
    ("معايا ٥٠ الف كاش و ١٢٪ التجاري الدولي", [{"name": "CIB", "weight_pct": 12}], ["COMI.CA"]),
]

CASES = [Case("EN", t, c, e) for t, c, e in EN] + [Case("AR", t, c, e) for t, c, e in AR]


class _ReplayLLM:
    """Returns the case's labeled candidates verbatim (resolver-mode harness)."""

    def __init__(self, candidates: list[dict]):
        self._payload = {"cash_egp": 50_000, "holdings": candidates, "unresolved_names": [], "warnings": []}

    def invoke(self, _messages):
        return SimpleNamespace(content=json.dumps(self._payload, ensure_ascii=False))


class _Counts:
    __slots__ = ("tp", "fp", "fn")

    def __init__(self):
        self.tp = self.fp = self.fn = 0

    def add(self, predicted: set, expected: set) -> None:
        self.tp += len(predicted & expected)
        self.fp += len(predicted - expected)
        self.fn += len(expected - predicted)

    def prf(self) -> tuple[float, float, float]:
        p = self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0
        r = self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0
        f = 2 * p * r / (p + r) if (p + r) else 0.0
        return p, r, f

    @property
    def support(self) -> int:
        """Number of labeled (expected) items for this field/lang slice."""
        return self.tp + self.fn


def _expected_fields(case: Case) -> dict[str, dict]:
    return {tk: case.candidates[i] for i, tk in enumerate(case.expected)}


def _score(cases: list[Case], *, live: bool) -> dict[str, dict[str, _Counts]]:
    """Returns {lang|ALL: {tickers|prices|percentages: _Counts}}."""
    groups = ("EN", "AR", "ALL")
    fields = ("tickers", "prices", "percentages")
    counts = {g: {f: _Counts() for f in fields} for g in groups}

    agent_live = PortfolioExtractionAgent() if live else None
    for case in cases:
        agent = agent_live if live else PortfolioExtractionAgent(llm=_ReplayLLM(case.candidates))
        result = agent.extract(case.text)
        pred = {h.ticker: h for h in result.snapshot.holdings}
        exp = _expected_fields(case)

        pred_tk, exp_tk = set(pred), set(exp)
        # prices / percentages scored only on correctly-resolved tickers
        pred_price = {tk for tk in pred_tk if pred[tk].avg_cost is not None}
        exp_price = {tk for tk in exp_tk if exp[tk].get("avg_cost") is not None}
        pred_pct = {tk for tk in pred_tk if pred[tk].weight_pct is not None}
        exp_pct = {tk for tk in exp_tk if exp[tk].get("weight_pct") is not None}
        # a price/pct counts as predicted-correct only when ticker AND value match
        good_price = {tk for tk in (pred_price & exp_price) if abs((pred[tk].avg_cost or 0) - exp[tk]["avg_cost"]) < _TOL}
        good_pct = {tk for tk in (pred_pct & exp_pct) if abs((pred[tk].weight_pct or 0) - exp[tk]["weight_pct"]) < _TOL}

        for g in (case.lang, "ALL"):
            counts[g]["tickers"].add(pred_tk, exp_tk)
            counts[g]["prices"].add(good_price, exp_price)
            counts[g]["percentages"].add(good_pct, exp_pct)
    return counts


def _markdown(counts: dict[str, dict[str, _Counts]], *, live: bool) -> str:
    mode = "live (LLM end-to-end)" if live else "resolver (registry + arithmetic, offline)"
    lines = [
        f"# Portfolio extraction mini-eval - {mode}",
        "",
        f"Labeled set: {len(CASES)} messages ({sum(c.lang=='EN' for c in CASES)} EN + "
        f"{sum(c.lang=='AR' for c in CASES)} AR).",
        "",
        "| Field | Lang | Precision | Recall | F1 | n |",
        "|---|---|--:|--:|--:|--:|",
    ]
    for field in ("tickers", "prices", "percentages"):
        for lang in ("EN", "AR", "ALL"):
            c = counts[lang][field]
            if c.support == 0:
                lines.append(f"| {field} | {lang} | n/a | n/a | n/a | 0 |")
                continue
            p, r, f = c.prf()
            lines.append(f"| {field} | {lang} | {p:.2f} | {r:.2f} | {f:.2f} | {c.support} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Portfolio extraction mini-eval (P9).")
    ap.add_argument("--live", action="store_true", help="Run the real LLM agent end-to-end (needs the conversational key).")
    ap.add_argument("--out", help="Write the Markdown table to this file as well as stdout.")
    args = ap.parse_args(argv)

    counts = _score(CASES, live=args.live)
    md = _markdown(counts, live=args.live)
    print(md)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"[written] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
