# Thesis Workspace

This folder contains thesis-writing materials copied from `/Users/mennaazazy/Desktop/thesis`.

## Structure

- `template/`
  - `gp26_thesis_template_with_cover_page.md` — official GP26 thesis template with cover page.
- `literature_reviews/`
  - `fundamental_analyst_lit_review.md` — focused literature review for the Fundamental Analyst module.
- `research_notes/`
  - `egx30_fundamental_analyst_ai_research.md` — broader EGX30 agentic finance and information-fusion research note.
- `drafts/`
  - Working thesis drafts should go here.

## Current Writing Priority

Start by converting the template into a project-specific draft while keeping the original template unchanged. Use the literature-review files as source material for Chapter 2, and use benchmark/backtest outputs only after the controlled runs are accepted.

## Safety Notes

- Do not invent citations. Preserve citation labels from the source reviews until the bibliography is verified.
- Do not claim EGX30 outperformance unless benchmark reports show `benchmark_enabled: true`, `source: local_csv`, and valid alpha metrics.
- Do not describe learning/memory as online training during backtests. Reflection flushing remains disabled during backtests; memory retrieval is temporally gated with `valid_after_date <= trade_date`.
