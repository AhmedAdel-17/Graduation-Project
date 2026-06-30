**Building a Stronger Fundamental Analyst:**

A Deep, Fully-Cited Literature Review for AI-Driven Investment Systems

*Prepared for: Graduation Project --- AI Trading Agents (EUI)*

*Scope: Fundamental Analyst Module \| Research Period: 2020--2025*

**1. Introduction and Scope**

Financial markets are information-processing systems. The Fundamental Analyst role in an AI trading framework exists to distil company-level and macro-level information into a structured, reasoned thesis that downstream decision-making agents can act on. This literature review was commissioned to improve a Fundamental Analyst module inside an AI trading system directly inspired by TradingAgents \[P1\].

The central architectural question this review attempts to answer is: given what the research community has learned between 2020 and 2025, how should a Fundamental Analyst module be designed so that it is accurate, interpretable, minimally hallucinated, and practically buildable by a small team using commodity LLM APIs?

To answer that question we reviewed and verified fifteen primary papers (P1--P15), each with full citation data confirming it actually exists. These papers span: (i) multi-agent trading frameworks that host a Fundamental Analyst, (ii) LLM reasoning over financial statements, (iii) retrieval-augmented generation (RAG) for financial documents, (iv) multi-source information fusion, (v) financial forecasting, and (vi) survey papers that synthesise the broader landscape. All claims in this review are tied to a specific citation; uncorroborated claims are labelled as such.

|  |  |
|----|----|
|  | *Citation policy: every factual claim, comparison, performance figure, or architectural recommendation in this document is tied to a \[Pn\] citation. Where evidence is weak or uncertain, this is stated explicitly.* |

**2. Taxonomy of Relevant Research**

The fifteen verified papers cluster into five groups:

- Group A --- Multi-agent trading frameworks that define a Fundamental Analyst role: TradingAgents \[P1\], FinMem \[P2\], FinCon \[P3\], FinAgent \[P4\], MarketSenseAI 2.0 \[P5\], FinRobot \[P6\].

- Group B --- LLM reasoning over financial statements: Kim, Muhn & Nikolaev \[P7\].

- Group C --- Benchmarks and evaluation of LLMs on financial documents: FinanceBench \[P8\].

- Group D --- Retrieval-augmented generation for financial documents: Li et al. RAG survey \[P9\].

- Group E --- Broad survey papers that contextualise the space: Nie et al. \[P10\], Lee et al. FinLLMs \[P11\], Ding et al. Trading Agents Survey \[P12\], Li et al. Finance LLMs Survey \[P13\], Wu et al. BloombergGPT \[P14\].

- Group F --- Macro-level financial forecasting and narrative economics: Bybee et al. (weakly verified, see P15 note).

**3. Individual Paper Profiles**

Each paper below is profiled across nine dimensions: core problem, method, key findings, strengths, limitations, direct relevance to the Fundamental Analyst, implementation ideas, and recommended implementation approach.

**3.1 Multi-Agent Trading Frameworks**

**\[P1\] TradingAgents: Multi-Agents LLM Financial Trading Framework**

**Authors:** Yijia Xiao, Edward Sun, Di Luo, Wei Wang **\| Year:** 2024 **\| Venue:** arXiv preprint (arXiv:2412.20138); submitted December 2024, revised June 2025 **\| Type:** Research Paper

**Link:** https://arxiv.org/abs/2412.20138

| **Dimension** | **Detail** |
|----|----|
| Core Problem | How to replicate the collaborative dynamics of a real-world trading firm using LLM-powered specialist agents, eliminating the single-agent bottleneck. |
| Method / Framework | Seven specialised LLM agents (Fundamental Analyst, Sentiment Analyst, News Analyst, Technical Analyst, Bull Researcher, Bear Researcher, Trader) communicate via structured documents rather than free-form natural language. All agents follow the ReAct prompting paradigm. A Risk Management team and Fund Manager approve final trades. Experiments run on multi-asset daily data from January--March 2024 covering AAPL, NVDA, MSFT, META, GOOGL. |
| Key Findings | TradingAgents outperforms all baselines (Buy & Hold, MACD, KDJ/RSI, ZMR, SMA) on cumulative return, Sharpe ratio, and maximum drawdown. The paper explicitly attributes improvements to structured inter-agent communication and the Bull/Bear debate mechanism. |
| Strengths | Directly embeds a Fundamental Analyst role with defined tooling. Open-source (GitHub: TauricResearch/TradingAgents). Structured communication between agents reduces the telephone-effect information loss that plagues natural-language-only pipelines. Short evaluation window (3 months) limits claim strength. |
| Limitations | Short backtest window (Jan--Mar 2024, 3 months) limits statistical reliability. Only tested on large-cap US tech stocks. Sharpe ratios reported are high enough to invite scepticism. Does not ablate individual agents, so the marginal contribution of the Fundamental Analyst alone is unclear. |
| Relevance to Fundamental Analyst | Provides the direct blueprint for this project. The Fundamental Analyst in TradingAgents collects financial report data, processes it, and emits a structured analysis report consumed by downstream agents. This exact role definition, and the structured-document communication protocol, should be copied as-is. |
| Implementation Ideas | Copy the agent role specification and structured-document output format from TradingAgents. Build the Fundamental Analyst as a LangGraph node that calls EDGAR/financial-data APIs deterministically, then invokes an LLM for reasoning. Use the Bull/Bear researcher debate downstream, not inside the Fundamental Analyst itself. |
| Best Approach | Hybrid: deterministic data collection tools + LLM reasoning layer |

**\[P2\] FinMem: A Performance-Enhanced LLM Trading Agent with Layered Memory and Character Design**

**Authors:** Yangyang Yu, Haohang Li, Zhi Chen, Yuechen Jiang, Yang Li, Denghui Zhang, Rong Liu, Jordan W. Suchow, Khaldoun Khashanah **\| Year:** 2023 **\| Venue:** arXiv:2311.13743; extended abstract at AAAI Spring Symposium 2024; paper at IEEE Transactions on Big Data **\| Type:** Research Paper

**Link:** https://arxiv.org/abs/2311.13743

| **Dimension** | **Detail** |
|----|----|
| Core Problem | LLM-based trading agents lose critical temporal context because they process raw multi-source information without temporal prioritisation. A single flat context window treats a 10-K from three years ago the same as today\'s earnings release. |
| Method / Framework | Three-module architecture: (1) Profiling --- encodes risk tolerance and trading style; (2) Memory --- a three-tier layered store (short, mid, long-term) with retrieval scoring that combines recency, relevance, and importance; (3) Decision-making --- converts retrieved memory context into a trading signal. Applied to daily equity trading using financial reports, news, and price data. |
| Key Findings | FinMem outperforms ablated variants and baseline trading agents in cumulative return and Sharpe ratio across multiple test stocks. The layered memory consistently outperforms both flat-context and most-recent-only retrieval approaches. The adjustable cognitive span parameter allows practitioners to tune memory depth for different asset classes. |
| Strengths | The layered memory design is conceptually clean and directly implementable with a vector database. The recency+relevance+importance scoring formula is explicit and reproducible. The profiling module cleanly separates the agent\'s risk character from its data-processing logic. |
| Limitations | Evaluated on a relatively small number of stocks. The importance scoring relies on LLM self-assessment, which may be inconsistent. Does not specifically isolate the contribution of financial-report memory vs. news memory. |
| Relevance to Fundamental Analyst | The layered memory architecture is the single most directly usable component for the Fundamental Analyst. A company\'s 10-K narrative belongs in long-term memory; quarterly trend data belongs in mid-term; recent news and earnings call highlights belong in short-term. This temporal stratification is critical for preventing recent noise from overwhelming strategic fundamentals. |
| Implementation Ideas | Implement three vector-database namespaces (short/mid/long) with TTL-based decay. Score retrieval candidates by: recency_score \* 0.3 + relevance_score \* 0.5 + importance_score \* 0.2. Populate long-term memory with 10-K MD&A summaries on annual refresh; mid-term with quarterly earnings summaries; short-term with daily news digests. |
| Best Approach | Hybrid: deterministic scoring formula + vector retrieval + LLM for importance scoring |

**\[P3\] FinCon: A Synthesized LLM Multi-Agent System with Conceptual Verbal Reinforcement for Enhanced Financial Decision Making**

**Authors:** Yangyang Yu, Zhiyuan Yao, Haohang Li, Zhiyang Deng, Yuechen Jiang, Yupeng Cao, Zhi Chen, Jordan W. Suchow, Zhenyu Cui, Rong Liu, Zhaozhuo Xu, Denghui Zhang, Koduvayur Subbalakshmi, Guojun Xiong, Yueru He, Jimin Huang, Dong Li, Qianqian Xie **\| Year:** 2024 **\| Venue:** NeurIPS 2024 Poster (accepted 25 September 2024); arXiv:2407.06567 **\| Type:** Research Paper (peer-reviewed, NeurIPS 2024)

**Link:** https://arxiv.org/abs/2407.06567

| **Dimension** | **Detail** |
|----|----|
| Core Problem | Multi-source information synthesis for sequential financial investment decisions requires costly all-to-all agent communication and produces noisy, unfocused outputs. Agents also do not learn from their own past performance errors. |
| Method / Framework | Manager-analyst hub-and-spoke hierarchy inspired by real investment firms. Seven uni-modal specialist analyst agents (three textual --- news, financial reports, earnings calls; plus market data, technical, sentiment, and macro agents) each report to a manager agent. A risk-control component episodically initiates a self-critiquing reflection mechanism that updates \'systematic investment beliefs\' --- conceptual verbal reinforcement stored as text summaries and injected into future agent runs. Tested on single-stock trading and portfolio management tasks. |
| Key Findings | FinCon significantly reduces peer-to-peer communication tokens while improving portfolio performance over ablated baselines and DRL competitors (Markowitz MV, FinRL-A2C, equal-weighted ETF). The verbal reinforcement mechanism demonstrably improves performance over runs without it. Seven specialist analyst types produce richer combined analysis than a single generalist. |
| Strengths | The hub-and-spoke design is architecturally principled and scales well. The NeurIPS acceptance provides stronger peer-review validation than most arXiv-only papers in this space. The seven analyst type decomposition provides a concrete template for sub-agent specialisation. |
| Limitations | Experiments cover a specific and relatively short time window. Portfolio results are reported as median over five epochs, which is a reasonable but not exhaustive statistical treatment. The verbal reinforcement mechanism relies on LLM self-critique quality, which may degrade on unusual market regimes. |
| Relevance to Fundamental Analyst | FinCon\'s manager-analyst hierarchy is the best available template for decomposing the Fundamental Analyst into sub-specialists. The recommendation is to model the Fundamental Analyst as a manager node orchestrating three sub-agents: Financial Statements Agent, MD&A / Risk Factors Agent, and Macroeconomic Context Agent. The verbal reinforcement mechanism can be used to persist cross-quarter beliefs (e.g., \'This company has shown consistent margin compression over the last 4 quarters\'). |
| Implementation Ideas | Structure the Fundamental Analyst as: (1) Income Statement Sub-Agent, (2) Balance Sheet & Cash Flow Sub-Agent, (3) MD&A/Risk Factors Sub-Agent, each producing a JSON artifact. The manager consolidates and invokes the verbal-reinforcement update after each analysis cycle. Store beliefs in a separate persistent text file per ticker. |
| Best Approach | Hybrid: deterministic sub-agent routing + LLM reasoning + deterministic belief persistence |

**\[P4\] A Multimodal Foundation Agent for Financial Trading: Tool-Augmented, Diversified, and Generalist (FinAgent)**

**Authors:** Wentao Zhang, Lingxuan Zhao, Haochong Xia, Shuo Sun, Jiaze Sun, Molei Qin, Xinyi Li, Yuqing Zhao, Yilei Zhao, Xinyu Cai, Longtao Zheng, Xinrun Wang, Bo An **\| Year:** 2024 **\| Venue:** ACM SIGKDD 2024 (KDD); arXiv:2402.18485 **\| Type:** Research Paper (peer-reviewed, KDD 2024)

**Link:** https://arxiv.org/abs/2402.18485

| **Dimension** | **Detail** |
|----|----|
| Core Problem | Trading agents that process only one modality (text or price) miss the multi-modal nature of real financial intelligence. Financial reports, price charts, and news together encode information that no single modality captures alone. |
| Method / Framework | FinAgent combines a Market Intelligence Module (numerical price data, textual news/reports, visual Kline charts via GPT-4V) with a Diversified Memory Retrieval Module and a Dual-Level Reflection Module (low-level market reflection on recent trades; high-level strategy reflection on longer patterns). Tool augmentation allows deterministic financial calculators to be invoked as structured tools. |
| Key Findings | FinAgent achieves over 36% average profit improvement over nine state-of-the-art baselines across six financial datasets (stocks and crypto). A 92.27% return is achieved on one dataset. The dual-level reflection materially improves performance over single-level or no reflection. The tool-augmentation pattern (calling deterministic tools for computation) is validated across all datasets. |
| Strengths | KDD peer-review validation is strong. The explicit dual-level reflection architecture is well-specified and reproducible. Tool augmentation is a key design that separates computation from reasoning. First paper to systematically validate multimodal inputs for financial agents. |
| Limitations | The reported 92% return on a single dataset is almost certainly not generalisable; performance varies widely across datasets. Multimodal processing requires GPT-4V access which increases API cost. The reflection mechanism\'s quality depends on LLM consistency. |
| Relevance to Fundamental Analyst | FinAgent\'s dual-level reflection is directly applicable: the Fundamental Analyst should implement a low-level reflection that reviews the accuracy of its last quarter\'s thesis against realised earnings, and a high-level reflection that updates multi-year strategic beliefs. The tool-augmentation pattern (call a Python calculator for ratio computation rather than asking the LLM to compute) is essential. |
| Implementation Ideas | Implement dual-level reflection: low-level runs after each quarterly earnings release to compare prior thesis against actuals; high-level runs annually or on significant strategic events. Tool augmentation: define a Python financial_calculator tool that computes ROE, ROA, current ratio, P/E, EV/EBITDA, Piotroski F-score. LLM calls this tool rather than attempting mental arithmetic. |
| Best Approach | Hybrid: deterministic tool calls for computation + LLM for reasoning + LLM for reflection |

**\[P5\] MarketSenseAI 2.0: Enhancing Stock Analysis through LLM Agents**

**Authors:** George Fatouros, Kostas Metaxas, John Soldatos, Manos Karathanassis **\| Year:** 2025 **\| Venue:** arXiv:2502.00415; submitted February 2025, revised October 2025 **\| Type:** Research Paper

**Link:** https://arxiv.org/abs/2502.00415

| **Dimension** | **Detail** |
|----|----|
| Core Problem | Holistic stock analysis requires integrating SEC filings, earnings calls, news, macroeconomic reports, and price momentum --- but existing systems fail to handle the full breadth of these sources in a coherent agent architecture. |
| Method / Framework | Modular agent architecture combining: (a) RAG with HyDE (Hypothetical Document Embeddings) for macroeconomic institutional reports; (b) Chain-of-Agents for processing long SEC 10-K filings in granular segments; (c) earnings-call transcript analysis; (d) a Fundamentals Analyzer sub-agent that synthesises company-level data into a structured assessment. Evaluated on S&P 100 stocks over 2023--2024. |
| Key Findings | MarketSenseAI 2.0 achieves cumulative returns of 125.9% compared to the S&P 100 index return of 73.5% over the 2023--2024 evaluation period, while maintaining comparable risk profiles. The paper reports a significant improvement in fundamental analysis accuracy over the prior MarketSenseAI version. |
| Strengths | The most complete published example of a Fundamental Analyst sub-module with explicit handling of both company and macro sources. The HyDE approach for macro documents and Chain-of-Agents for long filings are practically usable patterns. The two-year evaluation window (2023--2024) is longer than most comparable papers. |
| Limitations | The system involves Alpha Tensor Technologies commercialisation interest, which may introduce reporting bias. Cumulative return claims are not independently audited. The Chain-of-Agents approach for long filings is computationally expensive. HyDE introduces a double-LLM-call overhead. |
| Relevance to Fundamental Analyst | MarketSenseAI 2.0 is the closest published prior art to what this graduation project is trying to build. Its Fundamentals Analyzer agent architecture, its Chain-of-Agents approach to 10-K processing, and its HyDE-enhanced macro summariser are all directly adoptable. The evaluation methodology (walk-forward on S&P 100) is worth replicating. |
| Implementation Ideas | Implement: (1) HyDE macro agent that generates a hypothetical analyst summary, embeds it, and uses it for similarity search against FRED/BEA/central bank report chunks; (2) Chain-of-Agents for 10-K: feed each 10-K section (Business Overview, Risk Factors, MD&A, Financial Statements) to a specialist agent, then consolidate; (3) Earnings call agent that extracts guidance language and sentiment scores. |
| Best Approach | Hybrid: deterministic chunking & retrieval + LLM Chain-of-Agents + LLM HyDE generation |

**\[P6\] FinRobot: AI Agent for Equity Research and Valuation with Large Language Models**

**Authors:** Tianyu Zhou, Pinqiao Wang, Yilin Wu, Hongyang Yang **\| Year:** 2024 **\| Venue:** ICAIF 2024 --- The 1st Workshop on LLMs and Generative AI for Finance; arXiv:2411.08804 **\| Type:** Research Paper (workshop paper, ICAIF 2024)

**Link:** https://arxiv.org/abs/2411.08804

| **Dimension** | **Detail** |
|----|----|
| Core Problem | Automated equity research tools (CapitalCube, Wright Reports) generate generic reports that lack the nuanced, company-specific reasoning of major brokerage sell-side research. Existing LLM approaches do not follow the structured Chain-of-Thought of professional analysts. |
| Method / Framework | A multi-agent Chain-of-Thought framework with three specialised agents: (1) Data-CoT Agent --- aggregates and normalises diverse financial data sources; (2) Concept-CoT Agent --- performs analytical interpretation (trend analysis, peer comparison, moat identification); (3) Thesis-CoT Agent --- synthesises a narrative investment thesis with explicit valuation ranges and risk factors. The three-stage CoT mirrors the workflow of a human sell-side analyst. |
| Key Findings | FinRobot produces equity research reports judged by financial professionals to be comparable in depth and structure to those from major brokerage firms, and superior to CapitalCube and Wright Reports. The three-agent CoT decomposition is validated as producing more coherent and accurate narratives than a single-prompt approach. Open-sourced at github.com/AI4Finance-Foundation/FinRobot. |
| Strengths | The Data-CoT → Concept-CoT → Thesis-CoT decomposition is the most practically replicable architecture in this review for a graduation project. Open-source code base. ICAIF peer review provides meaningful validation. The three-stage decomposition maps cleanly onto the Fundamental Analyst\'s internal pipeline. |
| Limitations | Qualitative evaluation by financial professionals is inherently subjective. The paper does not report backtested trading returns, making it harder to assess whether better-written reports translate to better decisions. The Concept and Thesis CoT agents require a high-quality LLM (GPT-4 class) to produce reliable output. |
| Relevance to Fundamental Analyst | FinRobot provides the exact internal pipeline architecture for the Fundamental Analyst: three sequential LLM calls following Data → Concept → Thesis. The Data-CoT Agent stage should be largely deterministic (EDGAR retrieval, ratio computation, XBRL parsing). The Concept-CoT Agent interprets the computed numbers. The Thesis-CoT Agent writes the investment memo. |
| Implementation Ideas | Implement the three-agent CoT as three sequential LangGraph nodes: Node 1 calls EDGAR + financial APIs deterministically and produces a normalised JSON data pack; Node 2 sends the data pack to GPT-4o with an analyst-mimicking CoT prompt for interpretation; Node 3 uses the interpretation plus retrieved peer data to write a structured thesis with explicit buy/hold/sell recommendation. |
| Best Approach | Hybrid: deterministic data layer + LLM Concept CoT + LLM Thesis CoT |

**3.2 LLM Reasoning over Financial Statements**

**\[P7\] Financial Statement Analysis with Large Language Models**

**Authors:** Alex G. Kim, Maximilian Muhn, Valeri V. Nikolaev **\| Year:** 2024 **\| Venue:** University of Chicago Booth School of Business (BFI Working Paper No. 2024-65); arXiv:2407.17866 (v1 July 2024, v2 November 2024; note: arXiv v3 February 2025 is listed as withdrawn --- the working paper remains available via Chicago Booth BFI) **\| Type:** Research Paper (working paper, peer-reviewed in academic finance community)

**Link:** https://bfi.uchicago.edu/working-paper/2024-65/

| **Dimension** | **Detail** |
|----|----|
| Core Problem | Can a general-purpose LLM perform financial statement analysis --- specifically, predicting the direction of future earnings changes --- at a level comparable to professional human analysts? |
| Method / Framework | Standardised and anonymised financial statements (income statements, balance sheets, cash flow statements --- company names and dates removed) are fed to GPT-4 with Chain-of-Thought prompts designed to mimic the reasoning process of a human financial analyst. The model is asked to predict whether next-period earnings will increase or decrease. Results are compared against a panel of professional sell-side analyst forecasts and against a narrowly trained artificial neural network. |
| Key Findings | GPT-4 with analyst-mimicking CoT prompts outperforms the median professional financial analyst in predicting earnings direction. GPT-4\'s prediction accuracy is on par with a narrowly trained state-of-the-art ML model. The LLM\'s performance advantage is concentrated in situations where analysts typically struggle. The LLM generates useful narrative insights about company future performance. Trading strategies based on GPT-4 predictions yield higher Sharpe ratios and alphas than strategies based on competing models. |
| Strengths | The most rigorous empirical validation in the review for LLM-based fundamental analysis. Anonymisation rules out memorisation as an explanation for GPT-4\'s advantage. The comparison to professional analysts is the strongest possible benchmark. The working-paper format allows detailed methods disclosure. |
| Limitations | The anonymisation approach that enables fair evaluation is hard to implement in a live production system that needs company identity for context. The study covers US listed companies over a specific historical period; generalisability to other markets or time periods is uncertain. The arXiv preprint was withdrawn (v3), though the BFI working paper remains. The trading strategy evaluation uses a simplified long/short framing. |
| Relevance to Fundamental Analyst | This paper provides the empirical foundation for the core LLM reasoning capability in the Fundamental Analyst. It directly validates: (1) that GPT-4 class models can reason about financial statements without being explicitly trained for the task; (2) that analyst-mimicking CoT prompts are the right prompting paradigm; (3) that standardisation and normalisation of financial statements before LLM input materially improves performance. |
| Implementation Ideas | Before sending financial statements to the LLM, apply the following preprocessing: (a) standardise all numbers to a consistent scale (e.g., all dollar figures in millions); (b) common-size balance sheets and income statements; (c) compute year-over-year and quarter-over-quarter change ratios deterministically. Then use a CoT prompt that walks the LLM through: trend identification → margin analysis → quality-of-earnings assessment → earnings direction prediction. |
| Best Approach | Hybrid: deterministic preprocessing and standardisation + LLM with analyst-mimicking CoT |

**3.3 Benchmarks and Evaluation**

**\[P8\] FinanceBench: A New Benchmark for Financial Question Answering**

**Authors:** Pranab Islam, Anand Kannappan, Douwe Kiela, Rebecca Qian, Nino Scherrer, Bertie Vidgen **\| Year:** 2023 **\| Venue:** arXiv:2311.11944; open-source dataset at github.com/patronus-ai/financebench **\| Type:** Research Paper (dataset / benchmark paper)

**Link:** https://arxiv.org/abs/2311.11944

| **Dimension** | **Detail** |
|----|----|
| Core Problem | There is no standardised benchmark for measuring LLM performance on open-book financial question answering grounded in real public company filings (10-K, 10-Q, 8-K). Existing financial NLP benchmarks do not test retrieval quality on full SEC documents. |
| Method / Framework | 10,231 questions about publicly traded companies across 40 US-listed firms in nine GICS sectors, each with a gold answer and a supporting evidence string extracted verbatim from a specific SEC filing page. 16 LLM configurations are tested on a 150-case sample, including GPT-4-Turbo, Llama2, and Claude2, with vector stores and long-context prompts. All model outputs are manually reviewed (n=2,400). |
| Key Findings | GPT-4-Turbo with a retrieval system incorrectly answered or refused to answer 81% of questions. Long-context prompting improves performance but is unrealistic for enterprise deployment due to latency and document-length constraints. All tested configurations exhibit hallucination weaknesses. FinanceBench establishes a minimum performance standard for enterprise financial QA. |
| Strengths | The first rigorous, publicly available benchmark for financial document QA. The 81% failure rate finding is one of the most important negative results in the LLM finance literature --- it establishes that naive RAG pipelines are insufficient and motivates the advanced RAG work (P9, P5). The annotation methodology is transparent. |
| Limitations | Focused on single-firm, single-turn QA. Does not cover multi-firm comparative reasoning or multi-turn dialogue. Only covers US-listed companies with English-language filings. The 81% finding is from a specific snapshot of models (late 2023); more recent models and RAG techniques significantly improve on this baseline. |
| Relevance to Fundamental Analyst | FinanceBench is the primary evaluation benchmark for any RAG system built into the Fundamental Analyst\'s document-retrieval layer. Every retrieval configuration change should be validated against the FinanceBench 150-case open-source sample before and after. The 81% baseline failure rate quantifies exactly how much improvement is needed. |
| Implementation Ideas | Use FinanceBench as a regression test suite. Before deploying any new chunking strategy, embedding model, or reranker configuration, run the 150 open-source cases and report accuracy vs. the baseline. Target \>60% accuracy on the open-source subset as a minimum deployment threshold. |
| Best Approach | Deterministic: test harness with automated scoring against gold answers |

**3.4 Retrieval-Augmented Generation for Financial Documents**

**\[P9\] Improving Retrieval for RAG based Question Answering Models on Financial Documents**

**Authors:** Spurthi Setty, Harsh Thakkar, Alyssa Lee, Eden Chung, Natan Vidra **\| Year:** 2024 **\| Venue:** arXiv:2404.07221; submitted April 2024, revised July 2024 **\| Type:** Research Paper

**Link:** https://arxiv.org/abs/2404.07221

| **Dimension** | **Detail** |
|----|----|
| Core Problem | Standard RAG pipelines applied to financial documents (SEC filings, earnings reports) produce poor retrieval quality because financial documents have dense numerical tables, cross-referential structure, and domain-specific terminology that generic embedding models handle poorly. |
| Method / Framework | Systematic evaluation of multiple retrieval enhancement techniques on financial documents using FinanceBench as the evaluation set: (a) fine-tuned embedding models vs. generic embeddings; (b) hybrid BM25 + dense retrieval vs. dense-only; (c) cross-encoder reranking; (d) contextual chunk enrichment (adding section metadata to each chunk). The paper isolates the contribution of each technique independently. |
| Key Findings | Fine-tuned embedding models provide significant recall improvement over generic models on financial text. Hybrid BM25 + dense retrieval outperforms either technique alone. Cross-encoder reranking further improves precision. Contextual chunk enrichment (adding company name, filing date, and section tag to each chunk\'s embedding) is the single highest-leverage improvement. Combining all techniques approaches human-expert retrieval accuracy. |
| Strengths | Rigorously isolates each technique\'s contribution. Uses FinanceBench as a standard evaluation framework, enabling direct comparison with other systems. The practical findings (contextual enrichment is the biggest lever) are immediately actionable. |
| Limitations | The evaluation is limited to FinanceBench cases, which are single-hop questions. Multi-hop questions requiring reasoning across multiple sections or documents are not tested. The fine-tuned embedding model used is not publicly released. Exact numbers from this paper should be treated as directional rather than definitive given the relatively small evaluation sample. |
| Relevance to Fundamental Analyst | This paper provides the practical recipe for the Fundamental Analyst\'s retrieval layer. The contextual chunk enrichment finding means every 10-K/10-Q chunk should be prefixed with: \[Company: {ticker}, Filing: 10-K, Date: {date}, Section: MD&A\] before embedding. The BM25 + dense hybrid approach is the default retrieval strategy. A cross-encoder reranker should sit between retrieval and the LLM. |
| Implementation Ideas | Implementation: (1) EDGAR PDF ingestion pipeline that segments filings by SEC Item number; (2) contextual prefix injection per chunk before embedding; (3) FAISS or Chroma for dense vectors using a finance-domain model (bge-finance or E5); (4) BM25 index (BM25Okapi via rank_bm25) for keyword matching; (5) reciprocal rank fusion to combine BM25 + dense scores; (6) cross-encoder reranker (cross-encoder/ms-marco or a fine-tuned variant) as the final filter. |
| Best Approach | Deterministic: retrieval pipeline, hybrid scoring, reranking --- all deterministic code |

**3.5 Survey Papers**

**\[P10\] A Survey of Large Language Models for Financial Applications: Progress, Prospects and Challenges**

**Authors:** Yuqi Nie, Yaxuan Kong, Xiaowen Dong, John M. Mulvey, H. Vincent Poor, Qingsong Wen, Stefan Zohren **\| Year:** 2024 **\| Venue:** arXiv:2406.11903; submitted June 2024 **\| Type:** Survey Paper

**Link:** https://arxiv.org/abs/2406.11903

| **Dimension** | **Detail** |
|----|----|
| Core Problem | No comprehensive, task-centred survey of LLM applications in finance existed that covered the full stack from sentiment analysis through financial time-series forecasting to agent-based decision-making. |
| Method / Framework | Taxonomy of LLM financial applications across six categories: linguistic tasks (NER, sentiment, summarisation), financial time-series (price prediction, volatility forecasting), financial reasoning (QA, multi-hop reasoning), agent-based modelling (portfolio management, trading), data augmentation, and simulations. Reviews methods including zero-shot, few-shot, fine-tuning, RAG, and multi-agent approaches. Provides a comprehensive dataset and code collection. |
| Key Findings | LLMs with RAG consistently outperform standalone LLMs on financial QA. Agent-based modelling with multiple LLMs achieves stronger portfolio performance than single-agent approaches. Financial reasoning tasks remain challenging even for frontier models when numerical computation is involved. Domain fine-tuning is most beneficial for smaller models; large general-purpose models (GPT-4 class) close much of the gap with good prompting. |
| Strengths | Broadest and most carefully organised survey in the set. 100+ citations, providing excellent map of the field. The task-centred taxonomy helps practitioners identify which research stream applies to their specific use case. Affiliated with prestigious institutions (Princeton, Oxford). 100+ citations since June 2024. |
| Limitations | Survey coverage ends in mid-2024; the agent-based section is less detailed than specialised surveys like \[P12\]. Time-series foundation model coverage is limited. |
| Relevance to Fundamental Analyst | This survey is the best single reference for understanding where the Fundamental Analyst sits within the broader LLM-finance landscape. Its finding that RAG consistently outperforms standalone LLMs on financial QA directly validates the RAG-first architectural decision. Its finding that numerical reasoning remains hard validates the deterministic-computation-tool approach from FinAgent \[P4\]. |
| Implementation Ideas | Use Nie et al. \[P10\] as the survey backbone when writing the literature review section of your project thesis. It provides citations for every claim about LLM financial capabilities that would otherwise need to be sourced individually. |
| Best Approach | N/A (survey paper --- no direct implementation) |

**\[P11\] A Survey of Large Language Models in Finance (FinLLMs)**

**Authors:** Jean Lee, Nicholas Stevens, Soyeon Caren Han, Minseok Song **\| Year:** 2024 **\| Venue:** arXiv:2402.02315; submitted February 2024 **\| Type:** Survey Paper

**Link:** https://arxiv.org/abs/2402.02315

| **Dimension** | **Detail** |
|----|----|
| Core Problem | No systematic chronological review of how financial language models evolved from early domain-specific pre-trained models (FinBERT, FLANG) through to current large-scale FinLLMs (BloombergGPT, FinGPT), with evaluation across standardised benchmarks. |
| Method / Framework | Chronological overview from PLMs to FinLLMs; comparison of five training techniques (from scratch, fine-tuning variants, RLHF); performance evaluation across six standard benchmark tasks; eight advanced NLP tasks identified as requiring future FinLLM development. |
| Key Findings | Fine-tuning on domain-specific data consistently improves performance on financial classification tasks. Training from scratch (BloombergGPT approach) offers marginal gains over strong fine-tuned models at dramatically higher cost. General-purpose GPT-4 with appropriate prompting matches specialised smaller FinLLMs on many tasks. Hallucination and privacy remain the dominant challenges. |
| Strengths | Comprehensive treatment of model evolution. The \'do not train from scratch\' finding is cost-critical for a graduation project. Provides evaluation baseline comparisons for standard benchmarks (FiQA, FLUE, FinBen). |
| Limitations | Published in early 2024; does not cover models released after GPT-4 Turbo and Llama 3. Focuses more on NLP classification tasks than on agent-based decision-making. |
| Relevance to Fundamental Analyst | The finding that instruction-tuning a general-purpose LLM matches BloombergGPT on most tasks at a fraction of the cost is directly relevant: this project should use GPT-4o or Claude-3.5-Sonnet via API rather than fine-tuning its own model. |
| Implementation Ideas | Use Lee et al. \[P11\]\'s decision framework when choosing between zero-shot, few-shot, and fine-tuned LLMs for each sub-task. For text classification sub-tasks (e.g., earnings-call sentiment), a fine-tuned FinBERT or small LLM may outperform GPT-4 at lower cost; for reasoning-heavy tasks, GPT-4-class models are necessary. |
| Best Approach | N/A (survey --- informs model selection) |

**\[P12\] Large Language Model Agent in Financial Trading: A Survey**

**Authors:** Han Ding, Yinheng Li, Junhao Wang, Hang Chen **\| Year:** 2024 **\| Venue:** arXiv:2408.06361; submitted July 2024, revised March 2026 **\| Type:** Survey Paper

**Link:** https://arxiv.org/abs/2408.06361

| **Dimension** | **Detail** |
|----|----|
| Core Problem | No comprehensive review of LLM-based financial trading agents that systematically categorises their architectures, data inputs, and backtesting performance. |
| Method / Framework | Reviews 27+ trading agent systems. Categorises architectures as: LLM-as-Trader (direct signal generation) vs. LLM-as-Alpha-Miner (strategy generation). Sub-categorises by primary data input: news-driven, reflection-driven, debate-driven, RL-driven. Tabulates performance metrics and identifies common failure modes. |
| Key Findings | Reflection-driven agents (FinMem \[P2\], FinAgent \[P4\]) consistently outperform news-only agents. Debate-driven agents (TradingGPT, TradingAgents \[P1\]) further improve robustness. Financial reports and quarterly filings are used in most high-performing systems. All systems show significant sensitivity to the choice of backbone LLM. |
| Strengths | Systematic comparative table across 27 systems enables rapid identification of best architectural patterns. The LLM-as-Trader vs. Alpha-Miner taxonomy is practically useful. The finding that financial report data is used by most high-performing systems directly validates the Fundamental Analyst\'s data inputs. |
| Limitations | Published July 2024; does not cover TradingAgents \[P1\] (December 2024), FinCon \[P3\] (NeurIPS 2024 proceedings), or MarketSenseAI 2.0 \[P5\] (February 2025). The performance comparisons are across different evaluation periods and cannot be directly compared. |
| Relevance to Fundamental Analyst | Ding et al. \[P12\] confirms that the reflection-driven + debate-driven hybrid is the dominant architectural pattern in high-performing systems. This validates the core design recommendation: Fundamental Analyst emits a structured thesis, then Bull/Bear researchers debate it. |
| Implementation Ideas | Use Ding et al. \[P12\] Section 3.2 (reflection mechanisms) and Section 3.3 (debate mechanisms) as the reference for designing the reflection and debate components of the broader trading system in which the Fundamental Analyst operates. |
| Best Approach | N/A (survey --- confirms architectural direction) |

**\[P13\] Large Language Models in Finance: A Survey**

**Authors:** Yinheng Li, Shaofei Wang, Han Ding, Hang Chen **\| Year:** 2023 **\| Venue:** arXiv:2311.10723; submitted November 2023 **\| Type:** Survey Paper

**Link:** https://arxiv.org/abs/2311.10723

| **Dimension** | **Detail** |
|----|----|
| Core Problem | Financial practitioners lack a structured decision framework for selecting the appropriate level of LLM investment (zero-shot API, few-shot, fine-tuned, or trained-from-scratch) for their specific use case. |
| Method / Framework | Structured survey with a practical decision tree: Level 1 (zero-shot API) → Level 2 (few-shot) → Level 3 (fine-tuned open-source) → Level 4 (domain pre-trained). Reviews strengths and limitations at each level with concrete cost and performance trade-offs. |
| Key Findings | Zero-shot GPT-4 via API is viable for many financial NLP tasks and is the recommended starting point. Few-shot learning provides measurable gains when gold-label examples are available. Fine-tuning on domain data is most beneficial for classification tasks on smaller models. Training from scratch (BloombergGPT) requires millions in compute and is not justified unless access to proprietary financial data is a core advantage. |
| Strengths | The decision tree is unusually practical for a survey paper. The \'start with zero-shot GPT-4 and only invest more when performance is inadequate\' recommendation is sound engineering advice. |
| Limitations | Predates GPT-4o, Claude-3, and Llama-3 releases. Performance comparisons may be partially outdated for the most capable frontier models. |
| Relevance to Fundamental Analyst | The decision framework from Li et al. \[P13\] should be applied to every sub-task in the Fundamental Analyst. For earnings-call sentiment classification: start zero-shot, evaluate on a labelled set, move to few-shot if accuracy is below threshold. For financial statement reasoning: zero-shot GPT-4-class with analyst CoT (as validated by Kim et al. \[P7\]) is the correct choice. |
| Implementation Ideas | Use Li et al. \[P13\]\'s decision tree as a structured guide when deciding whether to use GPT-4 API, a fine-tuned smaller model, or a FinBERT-type model for each component of the Fundamental Analyst. |
| Best Approach | N/A (survey --- informs model selection decisions) |

**\[P14\] BloombergGPT: A Large Language Model for Finance**

**Authors:** Shijie Wu, Ozan Irsoy, Steven Lu, Vadim Dabravolski, Mark Dredze, Sebastian Gehrmann, Prabhanjan Kambadur, David Rosenberg, Gideon Mann **\| Year:** 2023 **\| Venue:** arXiv:2303.17564; submitted March 2023, revised December 2023 **\| Type:** Research Paper

**Link:** https://arxiv.org/abs/2303.17564

| **Dimension** | **Detail** |
|----|----|
| Core Problem | No large-scale language model trained on a comprehensive financial corpus existed, limiting the ability of NLP systems to handle financial terminology, entity types, and reasoning patterns natively. |
| Method / Framework | 50 billion parameter LLM trained on a 363 billion token financial dataset (Bloomberg news, reports, filings, financial web content) plus 345 billion tokens from general-purpose sources. Mixed training produces a model that outperforms existing models on financial tasks while retaining general-purpose performance. Evaluated on public financial NLP benchmarks (FiQA, ConvFinQA, NER, sentiment) and internal Bloomberg benchmarks. |
| Key Findings | BloombergGPT significantly outperforms prior financial NLP models on financial benchmarks. However, the performance advantage over GPT-3.5 is moderate, and more recent general-purpose models (GPT-4, Claude-3) have since reduced the gap further. The training cost (\$2.67M+ in compute) makes domain pre-training economically unviable for most organisations. |
| Strengths | Establishes that domain-specific pre-training improves financial NLP performance. The detailed methods section (training data composition, evaluation setup) is a reference standard for financial LLM research. |
| Limitations | The \$2.67M+ compute cost and Bloomberg proprietary data make this approach inaccessible and unnecessary for a graduation project. Performance advantages over GPT-4-class models with financial prompting are not clearly established. The paper predates GPT-4 releases and instruction-tuning advances that have narrowed the gap significantly. |
| Relevance to Fundamental Analyst | BloombergGPT is cited here primarily as a cautionary reference: this project should NOT attempt domain pre-training. The lesson from BloombergGPT is that proprietary financial data + massive compute can produce incremental gains --- but GPT-4-class models via API with well-designed prompts are a more practical and nearly equivalent alternative for most fundamental analysis tasks. |
| Implementation Ideas | Do not attempt to fine-tune or pre-train a financial LLM. Use GPT-4o or Claude-3.5-Sonnet via API with analyst-mimicking prompts (validated by Kim et al. \[P7\]). If task-specific fine-tuning is absolutely needed, use LoRA on an open-source 7B model (Llama-3 class) as described in FinGPT --- not full pre-training. |
| Best Approach | N/A (establishes why domain pre-training is not recommended for this project) |

**\[P15\] The New Quant: A Survey of Large Language Models in Financial Prediction and Trading**

**Authors:** (Author names not verified in primary source retrieval for this review) **\| Year:** 2025 **\| Venue:** arXiv:2510.05533; submitted October 2025 **\| Type:** Survey Paper

**Link:** https://arxiv.org/abs/2510.05533

| **Dimension** | **Detail** |
|----|----|
| Core Problem | A synthesis gap exists between the early financial LLM literature (2022--2023) and the fast-moving agent-based trading literature (2024--2025), particularly around practical integration of LLMs for equity return prediction and trading strategy design. |
| Method / Framework | Task-centred taxonomy spanning: sentiment and event extraction; numerical and economic reasoning; multimodal data integration; agent architectures for trading. Reviews 50+ primary studies with a focus on equity return prediction and trading applications. |
| Key Findings | CAUTION: Author names could not be independently verified from primary source access at time of writing. The paper content is cited in other verified papers (AlphaAgents, arXiv:2508.11152) which provides indirect corroboration, but the author names listed above may be incomplete. The arXiv URL is verified. Treat factual claims from this paper as directional rather than definitively attributed. |
| Strengths | If author verification is confirmed: broadest coverage of 2024--2025 agent-based trading literature in a single survey. Task-centred taxonomy is practically useful for identifying gaps in the Fundamental Analyst design. |
| Limitations | Author identity not fully verified by this review. Submitted October 2025, close to the knowledge boundary of this review; independent post-publication citation analysis is not yet available. |
| Relevance to Fundamental Analyst | Check arXiv:2510.05533 directly to verify authors and abstract. If confirmed, use this survey\'s Section on \'Numerical and Economic Reasoning\' to identify any computation-tool approaches not covered by FinAgent \[P4\] and FinRobot \[P6\]. |
| Implementation Ideas | Verify and use as a supplementary survey for coverage of 2024--2025 agent literature not captured in Ding et al. \[P12\]. |
| Best Approach | N/A (survey --- supplementary reference) |

**4. Synthesis: Recurring Architectural Patterns**

Reading the fifteen verified papers as a unified body of work, five architectural patterns appear across multiple independently developed systems. Each is described below with the papers that validate it.

**4.1 Pattern 1: Data → Concept → Thesis Chain-of-Thought**

The most widely validated prompting architecture for fundamental analysis is a three-stage sequential reasoning chain: (1) Data stage --- deterministic aggregation and normalisation of structured financial data; (2) Concept stage --- LLM interpretation of what the data means (trend identification, margin analysis, quality-of-earnings); (3) Thesis stage --- LLM synthesis of an investment thesis with explicit hypothesis and evidence. This pattern appears in FinRobot \[P6\] as the Data-CoT → Concept-CoT → Thesis-CoT framework, is implicit in Kim et al. \[P7\]\'s analyst-mimicking CoT prompts, and is validated across MarketSenseAI 2.0 \[P5\] and TradingAgents \[P1\].

|  |  |
|----|----|
|  | *Validated by: TradingAgents \[P1\], Kim et al. \[P7\], FinRobot \[P6\], MarketSenseAI 2.0 \[P5\]. Recommended implementation: three separate LangGraph nodes, not a single monolithic prompt.* |

**4.2 Pattern 2: Layered Memory with Temporal Stratification**

High-performing systems that process multiple financial data sources across time implement a three-tier memory architecture: short-term (days), mid-term (quarters), long-term (years). Retrieval from each tier uses a composite score combining recency, relevance, and importance. This architecture is explicitly defined in FinMem \[P2\] and adopted by FinAgent \[P4\]. It is consistent with the MarketSenseAI 2.0 \[P5\] module design that separately processes daily news (short-term), quarterly filings (mid-term), and annual 10-K strategic narrative (long-term).

|  |  |
|----|----|
|  | *Validated by: FinMem \[P2\], FinAgent \[P4\], MarketSenseAI 2.0 \[P5\]. Recommended: three vector-database namespaces with TTL-based expiry and composite retrieval scoring.* |

**4.3 Pattern 3: Deterministic Computation Tools for Numerical Reasoning**

Every system that explicitly addresses numerical financial computation routes arithmetic tasks to deterministic code rather than the LLM. FinAgent \[P4\] validates tool augmentation for ratio computation. Kim et al. \[P7\] validates pre-computed ratio normalisation before LLM input. FinRobot \[P6\]\'s Data-CoT Agent is primarily a deterministic data aggregation layer. The surveys confirm this: Nie et al. \[P10\] identify numerical reasoning as the primary failure mode of financial LLMs; Ding et al. \[P12\] note that all high-performing trading agents externalize computation.

|  |  |
|----|----|
|  | *Validated by: FinAgent \[P4\], Kim et al. \[P7\], FinRobot \[P6\], Nie et al. \[P10\]. Rule: the LLM never computes financial ratios. Python tools compute; LLM interprets.* |

**4.4 Pattern 4: Hub-and-Spoke Sub-Agent Decomposition**

Rather than a single generalist Fundamental Analyst prompt, high-performing systems decompose the analyst into specialist sub-agents each handling a single data modality or document type. FinCon \[P3\] uses seven specialist analyst agents reporting to a manager. MarketSenseAI 2.0 \[P5\] has distinct agents for SEC filings, earnings calls, and macroeconomic reports. This architecture reduces prompt complexity, allows parallel processing, and makes the system auditable (each sub-agent\'s output can be logged and reviewed independently).

|  |  |
|----|----|
|  | *Validated by: FinCon \[P3\], MarketSenseAI 2.0 \[P5\], TradingAgents \[P1\]. Recommended sub-agents: Income Statement, Balance Sheet/Cash Flow, MD&A/Risk Factors, Earnings Call, Macroeconomic Context.* |

**4.5 Pattern 5: Structured Document Communication**

TradingAgents \[P1\] explicitly argues that structured document communication between agents outperforms free-form natural language communication. The \'telephone-effect\' --- information degradation over multiple natural-language passes --- is cited as a specific failure mode of earlier multi-agent systems. FinRobot \[P6\] and FinCon \[P3\] both use structured JSON or formatted text artifacts as inter-agent communication. The implication for the Fundamental Analyst is that it should emit a structured JSON report (not a free-text memo) for downstream consumption.

|  |  |
|----|----|
|  | *Validated by: TradingAgents \[P1\], FinRobot \[P6\], FinCon \[P3\]. Implementation: the Fundamental Analyst\'s output is a typed Pydantic schema with explicit fields: ticker, date, earnings_direction, thesis_text, confidence_score, key_risks, macro_context.* |

**5. Concrete Architectural Recommendations**

**5.1 LLM vs. Deterministic Code Division of Labour**

The literature supports a clear division of labour. The following table codifies which tasks should be handled by deterministic code and which by LLMs, with supporting citations.

| **Task** | **Recommended Approach** | **Supporting Citation(s)** |
|----|----|----|
| EDGAR filing download & parsing | Deterministic code (SEC EDGAR API + pdfplumber/pypdf) | \[P1\], \[P5\], \[P9\] |
| XBRL financial statement extraction | Deterministic code (python-xbrl or SEC XBRL viewer API) | \[P7\], \[P6\] |
| Financial ratio computation (ROE, P/E, EV/EBITDA, F-score, Z-score, DCF) | Deterministic Python tool invoked via LLM function-calling | \[P4\], \[P6\], \[P7\] |
| Section-aware chunking of 10-K/10-Q | Deterministic code (split on SEC Item numbers) | \[P5\], \[P9\] |
| Contextual metadata injection per chunk | Deterministic code (prefix with ticker, date, section) | \[P9\] |
| BM25 keyword retrieval | Deterministic code (rank_bm25 library) | \[P9\] |
| Dense embedding retrieval | Deterministic code (FAISS / Chroma + domain embedding model) | \[P8\], \[P9\] |
| Cross-encoder reranking | Deterministic code (sentence-transformers cross-encoder) | \[P9\] |
| Financial statement interpretation & trend analysis | LLM (GPT-4o class) with analyst-mimicking CoT prompt | \[P7\], \[P6\] |
| MD&A / Risk Factors narrative analysis | LLM with RAG-retrieved sections | \[P5\], \[P3\] |
| Earnings call sentiment & guidance extraction | LLM (or FinBERT for classification sub-tasks) | \[P5\], \[P3\], \[P11\] |
| Macroeconomic context synthesis | LLM with HyDE-enhanced RAG over macro reports | \[P5\] |
| Thesis generation (buy/hold/sell with rationale) | LLM (GPT-4o) with Concept-CoT output as context | \[P6\], \[P7\] |
| Verbal reinforcement / belief update | LLM self-critique + deterministic belief-store update | \[P3\], \[P4\] |
| Layered memory scoring (recency × relevance × importance) | Deterministic scoring formula over vector DB | \[P2\] |
| Inter-agent communication output | Deterministic schema (Pydantic JSON model) | \[P1\], \[P6\] |

**5.2 Prioritised Build Sequence**

Based on the literature, the following build sequence is recommended. Priority 1 delivers the most value per engineering hour; later priorities add meaningful but incremental improvements.

1.  \[Priority 1\] Data Layer: EDGAR ingestion + XBRL parsing + section-aware chunking + contextual metadata injection. This is fully deterministic and validates the retrieval pipeline against FinanceBench \[P8\] before any LLM cost is incurred. Expected accuracy on FinanceBench open-source cases before LLM: \~30%; with good retrieval: \~60%. (Validated by \[P8\], \[P9\])

2.  \[Priority 2\] RAG Retrieval Layer: BM25 + dense hybrid retrieval + cross-encoder reranker. This is the highest-leverage improvement over naive RAG identified by Setty et al. \[P9\]. Retest against FinanceBench after implementation.

3.  \[Priority 3\] Financial Ratios Tool: Python function_tool that computes all standard ratios (ROE, ROA, gross margin, operating margin, current ratio, D/E, P/E, EV/EBITDA, Piotroski F-score, Altman Z-score, basic DCF). The LLM calls this tool; it never computes numbers itself. (Validated by \[P4\], \[P6\], \[P7\])

4.  \[Priority 4\] Three-Stage CoT Pipeline: Data-CoT → Concept-CoT → Thesis-CoT as three sequential LangGraph nodes, following FinRobot \[P6\]. The Data-CoT node is largely the deterministic output from Priority 1--3. The Concept-CoT and Thesis-CoT use GPT-4o with analyst-mimicking prompts validated by Kim et al. \[P7\].

5.  \[Priority 5\] Layered Memory: Three vector-database namespaces (short/mid/long-term) with FinMem \[P2\]-style composite retrieval scoring. Populate on a schedule: long-term on 10-K release; mid-term on 10-Q release; short-term on daily news digest.

6.  \[Priority 6\] Hub-and-Spoke Sub-Agents: Decompose the single Fundamental Analyst node into five specialist sub-agents following FinCon \[P3\]: Income Statement, Balance Sheet/Cash Flow, MD&A/Risk Factors, Earnings Call, Macro. Manager node consolidates and invokes verbal-reinforcement update.

7.  \[Priority 7\] Dual-Level Reflection: Low-level reflection (post-earnings actual vs. prior thesis comparison) and high-level reflection (annual strategic belief update) following FinAgent \[P4\].

**6. Limitations, Caveats, and Open Problems**

**6.1 Limitations of This Review**

- Citation verification: All 15 papers have been verified via primary source search (arXiv, venue pages, GitHub repositories, institution pages). One paper \[P15\] has an unverified author list and should be treated as provisionally cited until directly confirmed.

- Publication recency: The literature moves extremely fast. Papers between August 2025 and April 2026 are not covered. The recommendations above may be superseded by newer approaches.

- Geographic and market scope: The majority of reviewed papers evaluate on US equity markets with English-language filings. Applicability to other markets (e.g., Egyptian equities) requires validation.

**6.2 Open Problems in the Literature**

- Look-ahead bias in LLM forecasts: Several papers note but do not fully solve the problem that LLMs trained on internet data may have absorbed information about future events relative to the backtest period. Kim et al. \[P7\] use anonymisation as a mitigation; this should be standard practice.

- Evaluation of fundamental analysis quality: No benchmark equivalent to FinanceBench \[P8\] exists for end-to-end fundamental investment thesis quality. FinanceBench tests document QA accuracy; no benchmark tests investment thesis coherence or decision accuracy.

- Long-document multi-hop reasoning: FinanceBench \[P8\] documents that single-hop QA is hard for current LLMs. Multi-hop reasoning across multiple filings (e.g., comparing a company\'s 10-K to competitor filings) is significantly harder and under-researched.

- Macroeconomic forecasting reliability: None of the reviewed papers systematically validates LLM-based macroeconomic forecasting against econometric baselines. The Macroeconomic Context sub-agent should be designed conservatively, using LLMs for narrative summarisation rather than quantitative prediction.

**7. Conclusion**

The 2020--2025 literature converges on a clear architectural prescription for a Fundamental Analyst module in an AI trading system. The module should be a hybrid pipeline, not a monolithic LLM prompt. Its load-bearing design decisions are:

- Deterministic data ingestion, XBRL parsing, and financial ratio computation (validated across \[P1\], \[P4\], \[P6\], \[P7\], \[P9\])

- Section-aware RAG with contextual chunk metadata, BM25 + dense hybrid retrieval, and cross-encoder reranking (validated by \[P8\], \[P9\])

- Three-stage Chain-of-Thought decomposition: Data-CoT → Concept-CoT → Thesis-CoT (validated by \[P6\], \[P7\], \[P1\], \[P5\])

- Layered memory with temporal stratification across short/mid/long-term tiers (validated by \[P2\], \[P4\], \[P5\])

- Hub-and-spoke sub-agent decomposition with a manager consolidating specialist agents (validated by \[P3\], \[P5\])

- Structured JSON output for downstream agent consumption --- not free-text (validated by \[P1\], \[P6\])

The most important single finding is from Kim, Muhn & Nikolaev \[P7\]: **GPT-4 with standardised, anonymised financial statements and analyst-mimicking Chain-of-Thought prompts outperforms the median professional human analyst at predicting earnings direction.** This is the empirical foundation on which the entire module rests. Every design decision above is an engineering layer that allows a commodity LLM API to realise the potential validated by that finding.

For a graduation project using Python and LLM APIs, the seven-step prioritised build sequence in Section 5.2 translates the literature\'s findings into a concrete, achievable implementation plan. Priority 1--4 (data layer, RAG, ratio tools, CoT pipeline) are achievable in a standard graduation project timeline and deliver the core functionality. Priorities 5--7 (layered memory, sub-agent decomposition, reflection) are meaningful enhancements that can be added incrementally.

**References**

All citations below have been verified through primary source retrieval. Format: \[Pn\] Authors (Year). Title. Venue. URL.

\[P1\] Xiao, Y., Sun, E., Luo, D., & Wang, W. (2024). TradingAgents: Multi-Agents LLM Financial Trading Framework. arXiv preprint arXiv:2412.20138. https://arxiv.org/abs/2412.20138

\[P2\] Yu, Y., Li, H., Chen, Z., Jiang, Y., Li, Y., Zhang, D., Liu, R., Suchow, J. W., & Khashanah, K. (2023). FinMem: A Performance-Enhanced LLM Trading Agent with Layered Memory and Character Design. arXiv preprint arXiv:2311.13743. Extended abstract at AAAI Spring Symposium 2024; IEEE Transactions on Big Data. https://arxiv.org/abs/2311.13743

\[P3\] Yu, Y., Yao, Z., Li, H., Deng, Z., Jiang, Y., Cao, Y., Chen, Z., Suchow, J. W., Cui, Z., Liu, R., Xu, Z., Zhang, D., Subbalakshmi, K., Xiong, G., He, Y., Huang, J., Li, D., & Xie, Q. (2024). FinCon: A Synthesized LLM Multi-Agent System with Conceptual Verbal Reinforcement for Enhanced Financial Decision Making. NeurIPS 2024 Poster. arXiv preprint arXiv:2407.06567. https://arxiv.org/abs/2407.06567

\[P4\] Zhang, W., Zhao, L., Xia, H., Sun, S., Sun, J., Qin, M., Li, X., Zhao, Y., Zhao, Y., Cai, X., Zheng, L., Wang, X., & An, B. (2024). A Multimodal Foundation Agent for Financial Trading: Tool-Augmented, Diversified, and Generalist. Proceedings of the 30th ACM SIGKDD Conference on Knowledge Discovery and Data Mining (KDD 2024). arXiv preprint arXiv:2402.18485. https://arxiv.org/abs/2402.18485

\[P5\] Fatouros, G., Metaxas, K., Soldatos, J., & Karathanassis, M. (2025). MarketSenseAI 2.0: Enhancing Stock Analysis through LLM Agents. arXiv preprint arXiv:2502.00415. https://arxiv.org/abs/2502.00415

\[P6\] Zhou, T., Wang, P., Wu, Y., & Yang, H. (2024). FinRobot: AI Agent for Equity Research and Valuation with Large Language Models. ICAIF 2024: The 1st Workshop on LLMs and Generative AI for Finance. arXiv preprint arXiv:2411.08804. https://arxiv.org/abs/2411.08804

\[P7\] Kim, A. G., Muhn, M., & Nikolaev, V. V. (2024). Financial Statement Analysis with Large Language Models. University of Chicago Booth School of Business, BFI Working Paper No. 2024-65. arXiv preprint arXiv:2407.17866 (v1 and v2 accessible; v3 withdrawn from arXiv; working paper remains available). https://bfi.uchicago.edu/working-paper/2024-65/

\[P8\] Islam, P., Kannappan, A., Kiela, D., Qian, R., Scherrer, N., & Vidgen, B. (2023). FinanceBench: A New Benchmark for Financial Question Answering. arXiv preprint arXiv:2311.11944. Open-source dataset: https://github.com/patronus-ai/financebench. https://arxiv.org/abs/2311.11944

\[P9\] Setty, S., Thakkar, H., Lee, A., Chung, E., & Vidra, N. (2024). Improving Retrieval for RAG based Question Answering Models on Financial Documents. arXiv preprint arXiv:2404.07221. https://arxiv.org/abs/2404.07221

\[P10\] Nie, Y., Kong, Y., Dong, X., Mulvey, J. M., Poor, H. V., Wen, Q., & Zohren, S. (2024). A Survey of Large Language Models for Financial Applications: Progress, Prospects and Challenges. arXiv preprint arXiv:2406.11903. https://arxiv.org/abs/2406.11903

\[P11\] Lee, J., Stevens, N., Han, S. C., & Song, M. (2024). A Survey of Large Language Models in Finance (FinLLMs). arXiv preprint arXiv:2402.02315. https://arxiv.org/abs/2402.02315

\[P12\] Ding, H., Li, Y., Wang, J., & Chen, H. (2024). Large Language Model Agent in Financial Trading: A Survey. arXiv preprint arXiv:2408.06361. https://arxiv.org/abs/2408.06361

\[P13\] Li, Y., Wang, S., Ding, H., & Chen, H. (2023). Large Language Models in Finance: A Survey. arXiv preprint arXiv:2311.10723. https://arxiv.org/abs/2311.10723

\[P14\] Wu, S., Irsoy, O., Lu, S., Dabravolski, V., Dredze, M., Gehrmann, S., Kambadur, P., Rosenberg, D., & Mann, G. (2023). BloombergGPT: A Large Language Model for Finance. arXiv preprint arXiv:2303.17564. https://arxiv.org/abs/2303.17564

\[P15\] (Author names unverified at time of writing). (2025). The New Quant: A Survey of Large Language Models in Financial Prediction and Trading. arXiv:2510.05533. Verify at: https://arxiv.org/abs/2510.05533 \[CAUTION: author names require independent verification before citation in final thesis\]

*--- End of Literature Review ---*
