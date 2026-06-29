# Prompt For Thesis Agent

Please start a read-only thesis drafting pass while benchmark/backtesting continues. Do not modify benchmark code or rerun experiments unless explicitly asked.

Project thesis materials are now organized under:

- `/Users/mennaazazy/stockHive/Graduation-Project/thesis/template/gp26_thesis_template_with_cover_page.md`
- `/Users/mennaazazy/stockHive/Graduation-Project/thesis/drafts/thesis_working_draft.md`
- `/Users/mennaazazy/stockHive/Graduation-Project/thesis/literature_reviews/fundamental_analyst_lit_review.md`
- `/Users/mennaazazy/stockHive/Graduation-Project/thesis/research_notes/egx30_fundamental_analyst_ai_research.md`

Task:

1. Read the official template and identify the required thesis sections.
2. Read the two literature/research files and map their content into Chapter 2: Literature Review.
3. Produce a thesis writing plan with:
   - chapter-by-chapter outline,
   - which existing source files support each section,
   - which sections still need teammate literature reviews,
   - which benchmark/backtest evidence is still pending.
4. Start drafting only safe, non-results-dependent sections:
   - Abstract placeholder,
   - Chapter 1 introduction draft,
   - Chapter 2 literature-review structure,
   - methodology skeleton.
5. Do not invent citations, numerical results, benchmark claims, or final conclusions.
6. Clearly mark TODOs for missing teammate literature reviews and pending benchmark results.

Important project facts to preserve:

- EGX30 benchmark now uses a real local CSV: `/Users/mennaazazy/stockHive/Graduation-Project/EGX 30 Historical Data.csv`.
- CSV coverage: 2020-01-02 to 2026-06-14, 1561 rows.
- Benchmark reports are valid only when `benchmark_enabled: true`, `source: local_csv`, and `benchmark_error: null`.
- Memory learning is not online during backtests. Reflection flushing remains disabled during backtests.
- Memory retrieval is temporally gated using `valid_after_date <= trade_date`.
- BM25-only benchmark mode is being used for controlled benchmark runs with `EMBEDDINGS_BACKEND_URL=disabled`.

Deliver:

- A concise thesis status map.
- A proposed table of contents aligned to the template.
- A list of missing inputs needed from teammates.
- A draft starter file or patch only if asked to write directly.
