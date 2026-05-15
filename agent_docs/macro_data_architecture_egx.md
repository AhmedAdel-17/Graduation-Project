# Macro-Data Architectures for Egyptian Capital Markets

## A Professional Framework for EGX Systematic Trading

The construction of a systematic trading infrastructure for the Egyptian Exchange (EGX) requires rigorous integration of macroeconomic variables that function as primary drivers of asset pricing in a frontier market. In Egypt, where the equity market frequently acts as a surrogate for currency hedging and a reflection of sovereign risk profiles, the ability to ingest, parse, and model macro-data is not merely auxiliary. It is central to alpha generation.

This report analyzes the macro data landscape, prioritizing official repositories from:

- Central Bank of Egypt (CBE)
- Central Agency for Public Mobilization and Statistics (CAPMAS)
- Ministry of Finance (MoF)

It focuses on four critical pillars of Egyptian macro analysis:

- Inflationary dynamics
- Sovereign yield curves
- Currency regime transitions
- Shadow valuation signals from the parallel foreign exchange market

## The Inflationary Nexus: CAPMAS and CBE Price Indices

Inflation is one of the most significant systemic risks and valuation anchors for EGX-listed entities. For a trading system, the Consumer Price Index (CPI) is an input for real interest rate calculations, cost-of-capital adjustments, and purchasing power parity models.

Egyptian inflation reporting is split between:

- Headline CPI, managed by CAPMAS
- Core inflation, refined and published by the CBE

### CAPMAS Headline CPI: Methodology and Ingestion

CAPMAS is the primary authority for the Consumer Price Index, which measures changes in the cost of a fixed basket of goods and services purchased by representative urban households. Coverage includes Cairo, Alexandria, urban Lower Egypt, urban Upper Egypt, Canal cities, and Frontier governorates.

| Data Source | URL / Endpoint | Format | Parsing Difficulty |
|---|---|---|---|
| CAPMAS CPI Catalog | https://censusinfo.capmas.gov.eg/Metadata-en-v4.2/index.php/catalog/CPI | HTML / CSV | Moderate |
| CAPMAS Price Indices (NSDP) | https://capmas.gov.eg/Pages/StaticPages.aspx?page_id=5089 | HTML | Moderate |
| MoF Financial Monthly, Table 5 | https://assets.mof.gov.eg/files/180386c0-12de-11f1-8b76-f9fedb9cb444.pdf | PDF | High |

The CAPMAS metadata portal acts as a central data catalog where monthly bulletins are archived as individual studies. While the portal offers CSV export, the structure is inconsistent across time because of periodic revisions in base year and basket weights.

The most recent significant revision occurred on October 10, 2019, when CAPMAS released the 10th CPI series using fiscal year 2018/2019 as the base year, with weights derived from the 2017/2018 Household Income, Expenditure, and Consumption Survey (HIECS).

For a trading system, this requires a normalization layer to link older CPI series, such as the previous January 2010 base-month series, to the current series so that backtests use a continuous time series.

### Point-In-Time Leakage Risk

Point-in-time (PIT) leakage is a major risk when using CPI data. Egyptian inflation data for a given month is usually released around the 10th day of the following month.

Example: March 2026 inflation of 15.2% was officially released on April 9, 2026. A strategy running between April 1 and April 8, 2026 must still use February CPI data of 13.4%, because the March figure was not yet known to the market.

### CBE Core Inflation: Filtering for Monetary Policy Signals

The Central Bank of Egypt publishes a Core Inflation Index derived from CAPMAS headline CPI. This metric is important for predicting Monetary Policy Committee (MPC) decisions because it excludes items characterized by inherent volatility or administered prices.

The CBE excludes:

- Fruits and vegetables, approximately 8.8% of the headline basket
- Regulated items such as electricity and fuel, approximately 19.4% of the headline basket

| Metric | CBE Access URL | Format | Implementation Ease |
|---|---|---|---|
| Core Inflation Series | https://www.cbe.org.eg/en/monetary-policy/inflation | XLSX / CSV | High |
| Inflation Notes | https://www.cbe.org.eg/en/economic-research/publications/inflation-notes | PDF | Low |

The CBE's digital infrastructure is more automation-friendly than CAPMAS for some workflows because it provides CSV and XLSX export options on the primary inflation page.

When core inflation trends significantly below headline inflation, as seen in late 2025, this may suggest that inflation pressure is driven more by supply-side or administrative adjustments than by broad-based monetary expansion. This distinction matters for interest-rate-sensitive sectors such as banks and real estate.

## Sovereign Yield Dynamics: The T-Bill and Bond Market

For EGX trading systems, Treasury bill yields are the practical proxy for the risk-free rate. They are used in Discounted Cash Flow (DCF) models and as a benchmark for the carry cost of equity positions.

The Egyptian government relies heavily on short-term T-bills with tenors of 91, 182, 273, and 364 days to finance the budget deficit.

### Primary Market Auctions: CBE as Fiscal Agent

The Central Bank of Egypt conducts weekly auctions for EGP-denominated T-bills. These results provide an immediate read on institutional demand and the government's willingness to accept higher borrowing costs.

| Data Component | URL | Format | Update Frequency |
|---|---|---|---|
| EGP T-Bills Results | https://www.cbe.org.eg/en/auctions/egp-t-bills | HTML / PDF | Weekly |
| Historical Auction Data | https://www.cbe.org.eg/en/auctions/egp-t-bills/historical-data | HTML / XLSX | Periodic |
| T-Bonds, Fixed Coupon | https://www.cbe.org.eg/en/auctions/egp-t-bonds-fixed-coupon/historical-data | HTML / PDF | Monthly |

The results page contains implementation-relevant data including:

- ISIN
- Total nominal submitted bids
- Total nominal accepted bids
- Minimum yield
- Maximum yield
- Weighted average yield

The acceptance ratio, calculated as accepted bids divided by submitted bids, can be a useful liquidity signal. A low ratio suggests the MoF is either well-funded or unwilling to validate market expectations of higher inflation.

Technical extraction from the CBE historical portal is moderate to high difficulty. The site uses an AJAX-driven calendar system that may require specific POST requests or headless browser automation.

### Secondary Market Executions: Mark-To-Market Data

Primary auctions are weekly, but the secondary market for T-bills provides more frequent execution data. This is useful for mark-to-market calculations and for identifying yield curve inversions.

| Tenor Category | CBE Secondary Market URL | Format |
|---|---|---|
| Short-term, less than 30 days | https://www.cbe.org.eg/en/economic-research/statistics/egp-t-bills-secondary-market | HTML |
| Mid-term, 92-182 days | https://www.cbe.org.eg/en/economic-research/statistics/egp-t-bills-secondary-market | HTML |
| Long-term, 274-365 days | https://www.cbe.org.eg/en/economic-research/statistics/egp-t-bills-secondary-market | HTML |

The secondary market executions table includes:

- Weighted average yield
- Count of trades
- Total traded value

### Ministry of Finance: Fiscal Perspective

The Ministry of Finance provides corroborating T-bill data through Treasury Securities Auction Results and the Financial Monthly Bulletin.

| Publication | URL | Format | Data Utility |
|---|---|---|---|
| MoF Auction Results | https://mof.gov.eg/en/posts/treasuryBills/ | HTML / PDF | High |
| Financial Monthly | https://assets.mof.gov.eg/files/21e1e4e0-50f4-11f0-9431-b95f6852c1e5.pdf | PDF | Historical |

MoF reporting is often more comprehensive for fiscal context and legislative changes. One important example is Law No. 3 of 2021, which removed tax exemptions on T-bill and bond interest. A model that ignores this tax treatment may overstate the attractiveness of debt and understate the relative appeal of equities.

## Currency Regime Analysis: Official Rates and Parallel Premium

The EGP exchange rate is one of the most influential macro variables for EGX because it affects foreign institutional investor flows and the valuation of companies with hard-currency revenues.

The transition toward a flexible exchange rate regime, especially on March 6, 2024, makes monitoring both official and unofficial FX rates essential.

### Official Exchange Rates: CBE Benchmark

The official exchange rate is published daily by the Central Bank of Egypt and is the benchmark used by banks and the government.

| Source | URL / Endpoint | Format | PIT Integrity |
|---|---|---|---|
| CBE Official Rates | https://www.cbe.org.eg/en/economic-research/statistics/exchange-rates/historical-data | HTML / Excel | High |
| Investing.com USD/EGP | https://za.investing.com/currencies/usd-egp-historical-data | HTML / CSV | Moderate |
| Wise USD/EGP History | https://wise.com/us/currency-converter/usd-to-egp-rate/history | HTML | Moderate |

The CBE historical exchange rate portal provides rates for USD, EUR, GBP, and regional currencies such as SAR and AED. The preferred integration path is Excel export, although the portal may require calendar-driven state handling similar to CBE T-bill pages.

### Parallel FX Premium: Real-Economy Clearing Price

During FX scarcity episodes, the official rate may decouple from the rate at which economic agents can access hard currency. The resulting parallel market premium can act as a leading indicator for devaluations and equity market rallies.

| Parallel Market Source | URL | Type | Credibility |
|---|---|---|---|
| Sarf-Today | https://sarf-today.com/en/currency/us_dollar/market | Cash market | High, IMF-cited |
| ParallelRate | https://www.parallelrate.org/ | Aggregator | High, IMF-cited |
| Gold-implied rate | https://egypt.gold-price-today.com/ | Commodity-based | Moderate |
| CIB GDR implied rate | Bloomberg: COMIA LI / COMI EY | Equity-based | High institutional relevance |

A more institutional version of the parallel market signal is the CIB GDR implied exchange rate, calculated by comparing Commercial International Bank shares on the EGX with its GDR on the London Stock Exchange.

The implied exchange rate can be approximated as:

```text
E_implied = (Price_EGX * Ratio) / Price_LSE
```

where `Ratio` is the number of local shares per GDR.

When the implied rate diverges from the official CBE rate by more than 5%, it can signal macro-volatility and capital control stress.

## Strategic Implementation: Data Engineering and System Architecture

To operationalize these sources, the trading system needs a unified ingestion pipeline designed around fragmented formats and point-in-time discipline.

### SDDS / NSDP Framework

Egypt maintains a National Summary Data Page (NSDP) as part of the IMF Special Data Dissemination Standard (SDDS). This can serve as an advance-release-calendar and source discovery layer.

| SDDS Category | Responsible Agency | Implementation-Ready URL | Format |
|---|---|---|---|
| GDP, constant prices | Ministry of Planning | https://mped.gov.eg/assets/uploads/excel/GDP-Q-Constant.xls | Excel |
| GDP, current prices | Ministry of Planning | https://mped.gov.eg/assets/uploads/excel/GDP-Q-Current.xls | Excel |
| Production Index | Ministry of Planning | https://mped.gov.eg/assets/uploads/PI_bulletin-Jun2025.pdf | PDF |
| Consumer Prices | CAPMAS | https://capmas.gov.eg/Pages/StaticPages.aspx?page_id=5089 | HTML |
| Balance of Payments | CBE | https://www.cbe.org.eg/en/EconomicResearch/Publications/Pages/SDDS.aspx | HTML |

### Bi-Temporal Data Model

A robust EGX macro data store should be bi-temporal, storing data along two axes:

- Valid time: when the economic event occurred
- Transaction time: when the data became available to the system

Examples:

- Inflation PIT: A strategy running on April 5, 2026 must use February CPI, not March CPI, because March CPI was released on April 9.
- T-Bill PIT: Auction date is the signal date for liquidity modeling, while issue date is the anchor for portfolio mark-to-market.
- FX PIT: Official CBE rates are daily, while parallel rates and GDR-implied rates may be intraday and should be timestamped accordingly.

### Technical Recommendations

#### CBE Portal

Use Python `requests` to emulate POST parameters used by the historical data search where possible. If the portal relies on ASP.NET-style state, capture parameters such as:

- `__VIEWSTATE`
- `__EVENTVALIDATION`
- Calendar or duration selection fields

If static requests fail, use headless browser automation.

#### MoF Bulletins

The MoF Financial Monthly Bulletin is useful for historical fiscal and macro tables, but it is PDF-based. Use `pdfplumber` or similar tooling to target recurring tables.

Potential scraping risk: MoF pages may reject some regional IPs. If scraping proves unstable, use a local Egyptian cloud environment or manual archival workflow.

#### CAPMAS Metadata

The CAPMAS census metadata portal is more machine-readable than some primary agency pages. Focus on CPI study endpoints with structured print or CSV export options.

## Macro-Driven Alpha for EGX Trading

The goal of this data architecture is to generate actionable signals from the interaction of macro variables.

### Real Yield Gap

A primary EGX macro signal is the real T-bill yield:

```text
r_real = Y_91d - pi_headline
```

When inflation spikes but T-bill yields are capped, real yields turn deeply negative. This can push local liquidity into equities as a hedge.

Example framework:

- Deeply negative real yield: supportive for equities and hard-asset proxies
- Positive real yield: fixed income becomes a stronger competitor to equities
- Narrowing real yield gap: possible rotation signal from T-bills into equities

### FX Unification Signal

The convergence or divergence between official and parallel FX rates defines the currency regime.

| Regime | Condition | Strategy Implication |
|---|---|---|
| Divergent | Parallel premium greater than 20% | Long exporters, banks, hard-currency earners, and store-of-value equities |
| Convergent / Unification | Premium less than 2% | Long banks and consumer stocks benefiting from FX liquidity and lower input-cost pressure |

Net Foreign Assets (NFA) can act as an early warning indicator. A sharp deterioration in banking-sector NFA may precede tighter FX liquidity or devaluation risk.

## Implementation Priorities

1. Prioritize direct ingestion of CBE and CAPMAS data.
2. Store all macro data in a point-in-time schema with `observation_date`, `publication_date`, `available_at`, `source_url`, and `data_quality`.
3. Keep unsupported values blank or unavailable rather than using static fallback values.
4. Use CBE/CAPMAS CPI for monthly inflation rather than annual FRED CPI.
5. Use CBE/MoF auction results for T-bill yields rather than nonexistent or incompatible FRED series.
6. Treat FX premium data as non-official unless provenance and collection method are explicitly documented.
7. Supplement official data with CIB GDR implied FX and IMF-cited parallel-rate trackers only with strict quality flags.

## Final Conclusion

To maintain a competitive edge in EGX systematic trading, macro data should be treated as a primary signal source rather than a background factor. The highest-priority path is direct ingestion of CBE, CAPMAS, and MoF data into a point-in-time database.

Official sources should anchor the model, while parallel FX trackers and CIB GDR implied rates can provide high-frequency shadow-market context when clearly labeled and quality controlled.

This multi-layered approach, combining fiscal data, monetary aggregates, official inflation, exchange rates, and shadow-market stress indicators, gives the system the depth needed to navigate Egyptian capital market regimes while reducing leakage and false precision.
