# Financial Ratios — What They Mean and Where They Come From

This document explains every financial ratio used by the EGX Fundamental Analyst: what each one measures, how it is calculated, and exactly which source or paper justifies its inclusion.

---

## Paper Reference Key

The code uses short codes to tag where each design decision comes from. Here is what each code means:

| Code | Full name | What it is |
|---|---|---|
| **[P7]** | Kim et al. 2024 | An academic paper on LLM-based financial analysis using chain-of-thought prompting to mimic how a human analyst reads financial statements. The main design influence for the entire pipeline. |
| **[P4]** | FinAgent | An AI agent paper that builds an autonomous financial analysis system. Uses the same ratio set as its fundamental evidence pack. |
| **FinAR-Bench** | Financial Agent Reasoning Benchmark | A benchmark for evaluating LLM financial agents. Defines what data a competent financial agent must be able to reason over. |
| **[P2]** | FinMem | A paper on memory-augmented financial LLM agents. Referenced for data importance scoring principles. |
| **[P5]** | MarketSenseAI | A paper on market-aware AI agents. Referenced for data completeness checks. |
| **[AF]** | Academic Finance | Well-established in traditional finance literature (not AI-specific). |
| **[EI]** | Established in Investment Practice | Widely used in investment practice or economic theory, but not validated in any AI agent system. |
| **ALG** | Mathematical Identity / Algebra | Not from a paper — the formula is mathematically true by definition. |
| **EJ** | Engineering Judgment | Chosen by the project team based on observed EGX data behavior. No paper source. |

---

## Part 1 — Profitability Ratios

These five ratios answer the question: **Is this company making money, and how well?**

They appear together across all three source papers ([P7], [P4], FinAR-Bench) as the minimum required profitability evidence pack for any LLM financial analyst.

---

### Return on Equity (ROE)

**Formula:** Net Income ÷ Total Equity

**What it means:** For every pound of shareholder money invested in the company, how much profit did the company generate? A ROE of 20% means the company earned 20 piastres of profit for every pound shareholders own.

**Why it matters:** ROE is the single most-cited measure of management effectiveness. It answers "are the people running this company good at using the money entrusted to them?" High ROE sustained over multiple years is one of the strongest signals of competitive advantage.

**Important edge case:** If total equity is negative (the company owes more than it owns), ROE becomes mathematically misleading. The system detects this and generates a `NEGATIVE_EQUITY_ALERT` for the LLM to see.

**Source:** [P7] Kim et al. 2024, [P4] FinAgent, FinAR-Bench — all three use ROE as a required input. Also the backbone of the DuPont identity (see Part 3).

---

### Return on Assets (ROA)

**Formula:** Net Income ÷ Total Assets

**What it means:** For every pound of assets the company controls (factories, inventory, cash, etc.), how much profit did it generate? A ROA of 5% means the company earned 5 piastres of profit for every pound of assets it has.

**Why it matters:** ROA measures how efficiently the company uses everything it owns, not just shareholders' money. It is the key signal in the Piotroski scoring system (see Part 6) and helps distinguish between companies that earn well because they are genuinely productive versus companies that just borrowed a lot.

**Source:** [P7] Kim et al. 2024, [P4] FinAgent, FinAR-Bench. Also used as F1, F2 signals in the Piotroski score [AF].

---

### Gross Margin

**Formula:** Gross Profit ÷ Revenue

**What it means:** Out of every pound of sales, what fraction remains after paying only the direct cost of producing the product or service (raw materials, direct labor, etc.)? A gross margin of 40% means 40 piastres of every pound of sales is left to cover all other expenses.

**Why it matters:** Gross margin reveals **pricing power**. A company that can charge prices well above its production costs has some form of competitive advantage — a brand, a patent, or a captive market. Falling gross margin is often the first sign of deterioration, visible before it reaches the bottom line. It also allows the LLM to form a "margin stack": gross → operating → net. If gross margin is healthy but net margin is not, the problem is in operating costs, not the core product.

**Source:** [P7] Kim et al. 2024, [P4] FinAgent, FinAR-Bench. Also used as F8 in the original Piotroski score [AF].

---

### Operating Margin

**Formula:** Operating Income ÷ Revenue

**What it means:** Out of every pound of sales, what fraction remains after paying both the direct cost of production AND the overhead costs of running the business (salaries, rent, administration)? Operating margin excludes interest payments and taxes — it measures only the core business.

**Why it matters:** Operating margin isolates whether the actual business operations are profitable, separate from how the company is financed. A company can have a healthy operating margin but poor net margin if it carries heavy debt. Comparing gross margin and operating margin together shows how much of the revenue is being absorbed by overhead costs.

**Source:** [P7] Kim et al. 2024, [P4] FinAgent, FinAR-Bench.

---

### Net Margin

**Formula:** Net Income ÷ Revenue

**What it means:** Out of every pound of sales, what fraction becomes actual profit after paying everything — production costs, overhead, interest, and taxes? A net margin of 10% means 10 piastres of every pound of sales becomes profit.

**Why it matters:** Net margin is the bottom line. It is the end result of all the margin layers above it. The three margins together (gross → operating → net) form a diagnostic trail. If the gap between operating margin and net margin is large, the company is paying a lot in interest — which points directly to high debt. This is why `NEGATIVE_MARGIN_ALERT` fires on all sectors when net margin goes negative: it means the company is destroying value, not creating it.

**Source:** [P7] Kim et al. 2024, [P4] FinAgent, FinAR-Bench.

---

## Part 2 — Leverage and Liquidity Ratios

These two ratios answer: **Can the company survive its debts?**

---

### Debt-to-Equity (D/E)

**Formula:** Total Liabilities ÷ Total Equity

**What it means:** For every pound of shareholders' equity, how many pounds of debt does the company carry? A D/E of 2.0 means the company owes twice as much as its shareholders own. A D/E of 0.5 means it owes half as much.

**Why it matters:** Leverage is a double-edged sword. Debt amplifies returns when things go well, but it also amplifies losses when things go badly. Very high leverage means interest payments can overwhelm earnings during a downturn. D/E is also directly wired into the calibration system: if a company's D/E exceeds 4.0, the system suppresses "down" earnings predictions, because EGX companies with extreme leverage historically tend to refinance their debt or sell assets rather than actually reporting lower earnings — so a down prediction for such a company is structurally unreliable.

**Important edge case:** If equity is negative, D/E becomes meaningless (or sign-reversed). The system returns `None` and fires `NEGATIVE_EQUITY_ALERT` instead.

**Bank note:** Banks are excluded from standard D/E interpretation. A D/E of 8× is normal for a bank (deposits are technically liabilities). Flagging COMI at 8× as "critically leveraged" would be incorrect. The sector configuration handles this.

**Source:** [P7] Kim et al. 2024, [P4] FinAgent, FinAR-Bench. The D/E > 4.0 calibration gate is [EJ].

---

### Current Ratio

**Formula:** Current Assets ÷ Current Liabilities

**What it means:** Can the company pay its bills in the next 12 months? Current assets are things the company can convert to cash within a year (cash, inventory, receivables). Current liabilities are obligations due within a year (payables, short-term loans). A current ratio of 1.5 means the company has 1.50 pounds of short-term assets for every pound of short-term debt.

**Why it matters:** A current ratio below 1.0 means the company technically cannot pay its short-term bills from short-term assets alone — it would need to borrow more or sell long-term assets. Below 0.5 triggers `LIQUIDITY_EMERGENCY` in the system. This is one of the signals the LLM receives to assess operational distress.

**Bank note:** Banks are excluded from current ratio alerts. Banks by nature hold more short-term liabilities (deposits) than short-term liquid assets — their liquidity is managed through CBE reserve ratios, not the current ratio.

**Source:** [P7] Kim et al. 2024, [P4] FinAgent, FinAR-Bench. Also used as F6 in the Piotroski score [AF]. The 0.5 emergency threshold is [EJ] based on observed EGX data.

---

## Part 3 — DuPont Decomposition

These three ratios together form the DuPont identity — a mathematical breakdown of ROE into its three root causes.

**Source for all three:** Mathematical identity (ALG). No paper needed — DuPont = Net Margin × Asset Turnover × Equity Multiplier is algebraically guaranteed to equal ROE. The purpose in this system is data validation, not signal generation.

---

### Asset Turnover

**Formula:** Revenue ÷ Total Assets

**What it means:** How much revenue does the company generate for every pound of assets it holds? An asset turnover of 0.8 means every pound of assets generates 80 piastres of revenue.

**Why it matters (in this system):** Asset turnover is one of the three DuPont components. It is also used in the Piotroski score (F9: is asset turnover improving year-over-year?). It separates capital-efficient companies (high turnover, low asset base) from asset-heavy companies (low turnover, large asset base). Real estate companies and banks naturally have low asset turnover — this is normal and expected.

---

### Equity Multiplier

**Formula:** Total Assets ÷ Total Equity

**What it means:** How many pounds of assets does the company control per pound of equity? An equity multiplier of 3 means the company controls 3 pounds of assets for every pound shareholders own — the other 2 pounds are funded by debt.

**Why it matters (in this system):** The equity multiplier is the leverage component of DuPont. Combined with net margin and asset turnover, it explains exactly where ROE comes from. A high ROE driven by high equity multiplier (leverage) is riskier than the same ROE driven by high net margin.

---

### DuPont 3-Factor

**Formula:** Net Margin × Asset Turnover × Equity Multiplier

**What it means:** This should equal ROE. If it does not (within a 5% rounding tolerance), the income statement, balance sheet, and ratios CSV files are inconsistent with each other.

**Why it matters (in this system):** Used purely as a **data integrity check**. If DuPont ≠ ROE, the system flags a data inconsistency rather than silently feeding bad numbers to the LLM. This protects against CSV data errors propagating into the analysis.

---

## Part 4 — Valuation Ratios

These ratios compare the company's market price to its financial fundamentals.

---

### Earnings Per Share (EPS)

**Formula:** Net Income ÷ Shares Outstanding

**What it means:** How much profit did the company earn per share of stock? If net income is 100 million EGP and there are 50 million shares, EPS = 2 EGP per share.

**Why it matters:** EPS is the bridge between a company's profits and its stock price. It is required to compute the P/E ratio. EPS growth year-over-year is also one of the most direct signals of improving profitability.

**Source:** [P7] Kim et al. 2024, [P4] FinAgent, FinAR-Bench.

---

### Price-to-Earnings Ratio (P/E)

**Formula:** Stock Price ÷ EPS

**What it means:** How many pounds are investors paying for every pound of annual earnings? A P/E of 12 means investors are paying 12 EGP for every 1 EGP of earnings per share. A lower P/E generally means a cheaper valuation; a higher P/E means investors expect strong future growth and are paying a premium for it.

**Why it matters:** P/E is the most widely used valuation multiple in investment analysis worldwide. It allows comparison of how cheaply or expensively a company is priced relative to its earnings power.

**Important limitations on EGX:**
- P/E is undefined when EPS ≤ 0 (you cannot price a company on negative earnings). The system returns `None` and generates `PE_UNDEFINED` in those cases.
- P/E benchmarks from the US (e.g., "P/E below 15 is cheap") **do not apply to EGX**. When Egypt's interest rates were 27% in 2024, rational investors demanded much higher earnings yields, pushing P/E ratios far lower than US equivalents. The old code had hardcoded `PE < 8 = undervalued, PE > 15 = overvalued` — these were removed because they had no sourced basis for the EGX context.

**How it is computed here:** For annual ratios, the P/E comes directly from the yfinance snapshot. For quarterly periods, it is computed from the historical closing price on the quarter-end date divided by annualized EPS (quarterly EPS × 4).

**Source:** [P7] Kim et al. 2024, [P4] FinAgent, FinAR-Bench.

---

### Price-to-Book Ratio (P/B)

**Formula:** Stock Price ÷ Book Value Per Share

Book Value Per Share = Total Equity ÷ Shares Outstanding

**What it means:** How many pounds are investors paying for every pound of net asset value the company actually owns? A P/B of 1.5 means investors pay 1.50 EGP for every 1 EGP of accounting book value. A P/B below 1.0 means the stock trades below what the company's assets are worth on paper.

**Why it matters:** P/B is especially important for two EGX sectors:
- **Banks:** Bank earnings can be distorted by loan provisions and interest rate effects. P/B is often a more stable signal for bank valuation than P/E.
- **Real estate:** Real estate book values may understate actual property values (properties are carried at historical cost, not current market value). The system fires `PB_UNDERSTATED_HISTORICAL_COST` for real estate companies as a reminder to the LLM.

The old code had a hardcoded healthy range of `P/B 0.8–2.0` — this was removed because it is wrong for banks (which commonly trade at 1–3× book) and wrong for real estate (where book understates true value).

**How it is computed here:** From historical closing price on the quarter-end date divided by book value per share derived from the quarterly balance sheet.

**Source:** [P7] Kim et al. 2024, [P4] FinAgent, FinAR-Bench.

---

### Earnings Yield

**Formula:** 1 ÷ P/E Ratio

**What it means:** What percentage of the stock price does the company earn in profits? If P/E = 12, earnings yield = 8.3%. It is the inverse of P/E — a high earnings yield means cheap; a low earnings yield means expensive.

**Why it matters:** Earnings yield is easier to compare across companies than P/E (lower P/E is better, but higher earnings yield is better — keeping track of which direction is "good" for each metric causes confusion). Expressing it as a percentage also makes it directly comparable to interest rates.

**Source:** Mathematical identity (ALG) — exactly 1/P/E. No additional paper needed beyond the P/E source.

---

## Part 5 — Experimental Ratios

These two ratios are included but are explicitly marked as not validated in any AI agent system. They are available for the LLM to reference but are not treated as primary signals.

---

### Earnings Yield Spread

**Formula:** Earnings Yield − Risk-Free Rate (CBE overnight deposit rate)

**What it means:** How much more does this stock earn relative to a risk-free investment (Egyptian government bonds)? If earnings yield is 9% and the CBE rate is 27%, the spread is −18% — meaning you earn far less from this stock than from a safe bond. A positive spread means stocks offer a premium over the risk-free rate.

**Why it matters:** This is the core idea of the Fed Model from investment economics — stocks should be priced to offer some return above the risk-free rate, adjusted for risk. On the EGX during 2023–2024 with the CBE rate at 27%, many stocks had negative earnings yield spreads, meaning bonds were simply more attractive.

**EGX note:** The risk-free rate is date-aware — a 2020 analysis uses the 9.25% COVID-era CBE rate, not the 2024 rate of 27%. Using the wrong rate would be temporal leakage.

**Source:** Concept from the Fed Model [EI] — established in investment economics but never validated in any LLM agent system. Included as context, not as a primary driver.

---

### Dividend Yield

**Formula:** Dividend Per Share ÷ Stock Price

**What it means:** What percentage of the stock price is paid back to investors as dividends each year? A dividend yield of 3% means you receive 3 EGP per year for every 100 EGP invested.

**Why it matters:** Dividend yield is an income signal. For income-seeking investors, a high and stable dividend yield is a positive sign. It also indicates that management believes future earnings are sufficient to sustain payouts.

**Limitation:** Coverage across EGX30 is sparse. Many Egyptian companies do not report dividend data through yfinance. Where available the system includes it; where not available it is left blank.

**Source:** [AF] — standard investment metric. [EI] in the context of AI agents (not validated in any agent paper).

---

## Part 6 — Quality Score: Piotroski F-Score (7-Signal Variant)

This is not a single ratio but a composite health score. It gives 1 point for each of 7 binary signals, producing a score from 0 to 7. A score of 0–2 is weak, 3–5 is neutral, 6–7 is strong.

**Source:** Piotroski, J.D. (2000). "Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers." *Journal of Accounting Research*, 38(S), 1–41. Tagged as [AF] — validated for US value stocks, **not validated for EGX or any LLM agent system**. Treated as a diagnostic indicator only.

**Why the original 9 signals become 7 here:** Piotroski's original F-score has 9 signals. Signals 5 (operating cash flow profitability) and 8 (accruals = cash flow vs net income comparison) both require cash flow statement data. The EGX CSV schema does not have cash flow fields, so these two signals cannot be computed. If fewer than 4 of the remaining 7 signals are computable, the score is returned as `None` rather than a misleading partial score.

| Signal | What it checks | Why |
|---|---|---|
| F1 | ROA > 0 | Is the company profitable at all? |
| F2 | ROA improving vs prior year | Is profitability getting better? |
| F3 | Net income positive | Bottom-line sanity check |
| F4 | D/E decreasing vs prior year | Is the company paying down debt? |
| F6 | Current ratio improving vs prior year | Is short-term liquidity getting better? |
| F7 | No new shares issued | Is management diluting shareholders? |
| F9 | Asset turnover improving vs prior year | Is the company using assets more efficiently? |

---

## Part 7 — What Was Explicitly Removed

The original `fundamentals_analyst.py` contained several metrics and thresholds that were removed because they had no sourced justification:

| Removed item | Why removed |
|---|---|
| `PE < 8 = undervalued`, `PE > 15 = overvalued` | Absolute P/E thresholds from US market norms. Invalid on EGX where interest rates are 27% — rational P/E is much lower in a high-rate environment. |
| `P/B healthy range: 0.8–2.0` | Wrong for banks (normally 1–3× book) and wrong for real estate (book understates real asset value). No source cited. |
| `TARGET_PE_LOW = 6.0`, `TARGET_PE_HIGH = 12.0` | Hardcoded numbers with no paper or data source. |
| `DATA_COMPLETENESS_WEIGHT`, `RECENCY_WEIGHT`, `DISCLOSURE_QUALITY_WEIGHT` | Constants defined but never used anywhere in the code. |
| `calculate_confidence_score()` | Function with a missing return statement (broken). Replaced with three clean, separately validated scores: `data_confidence`, `model_confidence`, `signal_quality`. |

---

## Summary Table

| Ratio | Formula | Source |
|---|---|---|
| ROE | Net Income / Total Equity | [P7], [P4], FinAR-Bench |
| ROA | Net Income / Total Assets | [P7], [P4], FinAR-Bench |
| Gross Margin | Gross Profit / Revenue | [P7], [P4], FinAR-Bench |
| Operating Margin | Operating Income / Revenue | [P7], [P4], FinAR-Bench |
| Net Margin | Net Income / Revenue | [P7], [P4], FinAR-Bench |
| Debt-to-Equity | Total Liabilities / Total Equity | [P7], [P4], FinAR-Bench |
| Current Ratio | Current Assets / Current Liabilities | [P7], [P4], FinAR-Bench, [AF] |
| Asset Turnover | Revenue / Total Assets | ALG (DuPont component), [AF] |
| Equity Multiplier | Total Assets / Total Equity | ALG (DuPont component) |
| DuPont 3-Factor | Net Margin × Asset Turnover × Equity Multiplier | ALG (data validation only) |
| EPS | Net Income / Shares Outstanding | [P7], [P4], FinAR-Bench |
| P/E Ratio | Price / EPS | [P7], [P4], FinAR-Bench |
| P/B Ratio | Price / Book Value Per Share | [P7], [P4], FinAR-Bench |
| Earnings Yield | 1 / P/E | ALG (inverse of P/E) |
| Earnings Yield Spread | Earnings Yield − CBE Rate | [EI] — experimental |
| Dividend Yield | DPS / Price | [AF], [EI] — sparse EGX coverage |
| Piotroski F-Score (7-signal) | Sum of 7 binary health signals | [AF] Piotroski 2000 — not EGX-validated |

---

## Papers in Full

| Code | Citation |
|---|---|
| [P7] | Kim, S. et al. (2024). *Can Large Language Models be Financial Analysts? An Evaluation on Financial Analysis Using Chain-of-Thought Prompting.* |
| [P4] | Tang, B. et al. *FinAgent: A Multimodal Foundation Agent for Financial Trading.* |
| FinAR-Bench | *FinAR-Bench: A Benchmark for Financial Agent Reasoning.* |
| [P2] | *FinMem: A Performance-Enhanced LLM Trading Agent with Layered Memory and Character Design.* |
| [P5] | *MarketSenseAI: Real-World Market Intelligence for LLM Agents for Automated Stock Analysis.* |
| [AF] | Piotroski, J.D. (2000). *Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers.* Journal of Accounting Research, 38(S), 1–41. |
| [EI] | Fed Model: Estrada, J. (2006). *The Fed Model: A Note.* Finance Research Letters, 3(1), 14–22. |
