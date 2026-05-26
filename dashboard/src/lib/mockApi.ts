/**
 * In-memory mock backend. Persists data to localStorage so reloads keep state.
 * Replace this file with a real fetch client when backend is ready —
 * the public API (api.*) is the contract.
 */
import type {
  User,
  Sector,
  Ticker,
  MacroSnapshot,
  Quote,
  Run,
  Backtest,
  Decision,
  AgentOutput,
  WSMessage,
} from "@/types/api";
import { EGX_TICKERS, AGENT_PIPELINE } from "./constants";
import { addDays, subDays, format } from "date-fns";

// ---------- persistence ----------
const isBrowser = typeof window !== "undefined";
const KEY = "ta_mock_db_v1";

interface DB {
  users: Record<string, User & { password: string }>;
  tokens: Record<string, string>; // token -> userId
  runs: Run[];
  backtests: Backtest[];
}

function loadDB(): DB {
  if (!isBrowser) return { users: {}, tokens: {}, runs: [], backtests: [] };
  try {
    const raw = window.localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw);
  } catch {}
  return { users: {}, tokens: {}, runs: [], backtests: [] };
}
function saveDB(db: DB) {
  if (!isBrowser) return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(db));
  } catch {}
}

let db: DB = loadDB();
const persist = () => saveDB(db);

// ---------- helpers ----------
const uid = () =>
  Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function currentMacro(): MacroSnapshot {
  return {
    cbe_rate: 27.25,
    real_rate: -8.4,
    usd_egp: 48.6,
    fx_trend: "stable",
    egx30_trend: "uptrend",
    egx30_return_1m: 4.2,
    brent_usd: 79.4,
    imf_program_active: true,
    as_of_date: format(new Date(), "yyyy-MM-dd"),
  };
}

function quoteFor(symbol: string): Quote {
  // deterministic-ish pseudo quote based on symbol hash
  let h = 0;
  for (let i = 0; i < symbol.length; i++) h = (h * 31 + symbol.charCodeAt(i)) | 0;
  const base = 20 + (Math.abs(h) % 180);
  const drift = ((Math.abs(h) % 1000) / 100) - 5;
  return {
    symbol,
    last: +(base + drift).toFixed(2),
    change_pct: +(((Math.sin(h + Date.now() / 1e7) * 3))).toFixed(2),
    volume: 100_000 + (Math.abs(h) % 5_000_000),
    as_of_date: format(new Date(), "yyyy-MM-dd"),
  };
}

function egx30History(days: number) {
  const out: { date: string; close: number }[] = [];
  let v = 32000;
  const start = subDays(new Date(), days);
  for (let i = 0; i <= days; i++) {
    v += (Math.sin(i / 3) + (Math.random() - 0.4)) * 80;
    out.push({ date: format(addDays(start, i), "yyyy-MM-dd"), close: +v.toFixed(2) });
  }
  return out;
}

// ---------- AUTH ----------
export const auth = {
  async signup(input: {
    name: string;
    email: string;
    password: string;
    sectors_of_interest: Sector[];
    alerts_enabled?: boolean;
  }) {
    await sleep(500);
    if (Object.values(db.users).find((u) => u.email === input.email)) {
      throw new Error("An account with that email already exists");
    }
    const user: User & { password: string } = {
      id: uid(),
      name: input.name,
      email: input.email,
      sectors_of_interest: input.sectors_of_interest,
      alerts_enabled: input.alerts_enabled,
      created_at: new Date().toISOString(),
      password: input.password,
    };
    db.users[user.id] = user;
    const token = uid();
    db.tokens[token] = user.id;
    persist();
    const { password, ...safe } = user;
    return { token, user: safe };
  },
  async login(input: { email: string; password: string }) {
    await sleep(450);
    const u = Object.values(db.users).find((x) => x.email === input.email);
    if (!u || u.password !== input.password) throw new Error("Invalid email or password");
    const token = uid();
    db.tokens[token] = u.id;
    persist();
    const { password, ...safe } = u;
    return { token, user: safe };
  },
  async me(token: string) {
    await sleep(120);
    const userId = db.tokens[token];
    if (!userId) throw new Error("Unauthorized");
    const u = db.users[userId];
    if (!u) throw new Error("Unauthorized");
    const { password, ...safe } = u;
    return { user: safe };
  },
  async logout(token: string) {
    delete db.tokens[token];
    persist();
  },
};

function userIdOrThrow(token: string | null) {
  if (!token) throw new Error("Unauthorized");
  const id = db.tokens[token];
  if (!id) throw new Error("Unauthorized");
  return id;
}

// ---------- TICKERS / MACRO ----------
export const market = {
  async tickers(): Promise<Ticker[]> {
    await sleep(120);
    return EGX_TICKERS;
  },
  async macroCurrent(): Promise<MacroSnapshot> {
    await sleep(180);
    return currentMacro();
  },
  async egx30History(days = 30) {
    await sleep(200);
    return egx30History(days);
  },
  async quote(symbol: string): Promise<Quote> {
    await sleep(100);
    return quoteFor(symbol);
  },
};

// ---------- RUNS / JOBS ----------
interface Job {
  id: string;
  kind: "predict" | "backtest";
  status: "pending" | "running" | "done" | "failed";
  run_id?: string;
  error?: string;
  cancelled?: boolean;
}
const jobs: Record<string, Job> = {};
const jobBuses: Record<string, EventTarget> = {};
const jobBuffers: Record<string, WSMessage[]> = {};

function emit(jobId: string, msg: WSMessage) {
  jobBuffers[jobId] = jobBuffers[jobId] || [];
  jobBuffers[jobId].push(msg);
  const bus = jobBuses[jobId];
  if (bus) bus.dispatchEvent(new MessageEvent("msg", { data: msg }));
}

function pickDecision(): { decision: Decision; confidence: number; rationale: string } {
  const r = Math.random();
  if (r < 0.45)
    return {
      decision: "BUY",
      confidence: 0.65 + Math.random() * 0.3,
      rationale:
        "Macro tailwinds (CBE pivot expected, stable EGP) combine with bullish technicals and improving fundamentals; risk/reward favorable.",
    };
  if (r < 0.75)
    return {
      decision: "HOLD",
      confidence: 0.55 + Math.random() * 0.25,
      rationale:
        "Mixed signals: technicals constructive but news flow unclear and constitutional risk check flags concentration.",
    };
  return {
    decision: "SELL",
    confidence: 0.6 + Math.random() * 0.3,
    rationale:
      "Bearish news momentum and stretched valuation outweigh weak technical support; risk manager vetoes new long.",
  };
}

function summaryFor(agent: string, ticker: string): { summary: string; details: any } {
  switch (agent) {
    case "Data Prefetcher":
      return { summary: `Cached 252d OHLCV, news, fundamentals for ${ticker}`, details: { rows: 252, sources: 6 } };
    case "Market Analyst":
      return {
        summary: "RSI 58, MACD bullish cross, price above 50d/200d MAs",
        details: { rsi: 58, macd: "bullish_cross", trend: "up" },
      };
    case "Fundamentals Analyst":
      return {
        summary: "P/E 8.2, ROE 22%, debt/equity 0.4 — undervalued vs peers",
        details: { pe: 8.2, roe: 0.22, debt_to_equity: 0.4 },
      };
    case "News Analyst":
      return {
        summary: "Fetched 20 articles from 6 sources, sentiment: bullish (65/100)",
        details: { articles: 20, sources: ["Mubasher", "Reuters", "EnterprisePress", "AlBorsa", "Bloomberg", "Daily News Egypt"], sentiment: "bullish", score: 65 },
      };
    case "Social Media Analyst":
      return { summary: "Twitter buzz +18% w/w, Reddit sentiment neutral-positive", details: { mentions: 432, sentiment: 0.22 } };
    case "Bull Researcher":
      return {
        summary: "Strong long thesis: catalyst-driven, valuation support",
        details: {
          thesis:
            "We see asymmetric upside driven by (1) imminent rate-cut cycle from CBE supporting credit demand, (2) attractive entry given 35% YTD discount to historical multiples, and (3) positive earnings revisions trending. Downside is bounded by tangible book value.",
          targets: { base: 92, bull: 110 },
        },
      };
    case "Bear Researcher":
      return {
        summary: "Counter-thesis: FX volatility, regulatory overhang",
        details: {
          thesis:
            "Bear case rests on (1) sticky inflation forcing higher-for-longer rates, (2) recurring EGP devaluation pressure compressing margins, and (3) potential dilution from announced rights issue. Downside to 64.",
          targets: { base: 64, bear: 52 },
        },
      };
    case "Research Manager":
      return {
        summary: "Recommendation: BUY — bull case has stronger evidence",
        details: { recommendation: "BUY", reasoning: "Rate path and valuation gap dominate; FX risk acknowledged but hedgeable." },
      };
    case "Trader":
      return {
        summary: "Plan: enter 3% portfolio, stop at -8%, target +18% in 90d",
        details: { size_pct: 3, stop_pct: -8, target_pct: 18, horizon_days: 90 },
      };
    case "Risk Scorer":
      return { summary: "Composite risk: 0.42 (moderate)", details: { liquidity: 0.3, volatility: 0.5, concentration: 0.4 } };
    case "Risk Debators":
      return {
        summary: "Risky/Safe/Neutral debated — Neutral wins",
        details: {
          risky: "Double position size; rate cuts will repricе faster than consensus.",
          safe: "Wait for clearer FX signal; reduce to 1% probe.",
          neutral: "Stick to plan: 3% with disciplined stop.",
        },
      };
    case "Risk Manager":
      return { summary: "Constitutional check passed", details: { clauses: ["§2.1 sizing", "§3.4 stop loss", "§5.2 sector cap"], pass: true } };
    default:
      return { summary: "Done", details: {} };
  }
}

function macroForRun(): Run["macro_context"] {
  const m = currentMacro();
  return {
    cbe_rate: m.cbe_rate,
    real_rate: m.real_rate,
    usd_egp: m.usd_egp,
    fx_trend: m.fx_trend,
    egx30_trend: m.egx30_trend,
    brent_usd: m.brent_usd,
    imf_program_active: m.imf_program_active,
  };
}

async function runPipeline(jobId: string, userId: string, ticker: string, tradeDate: string, portfolio: number) {
  const run: Run = {
    id: uid(),
    user_id: userId,
    type: "prediction",
    ticker,
    trade_date: tradeDate,
    portfolio_value: portfolio,
    status: "running",
    macro_context: macroForRun(),
    agents: AGENT_PIPELINE.map((a) => ({ agent: a, status: "pending" })),
    created_at: new Date().toISOString(),
  };
  jobs[jobId].run_id = run.id;
  jobs[jobId].status = "running";

  for (let i = 0; i < AGENT_PIPELINE.length; i++) {
    if (jobs[jobId].cancelled) {
      run.status = "failed";
      break;
    }
    const agent = AGENT_PIPELINE[i];
    const idx = run.agents.findIndex((a) => a.agent === agent);
    run.agents[idx] = { ...run.agents[idx], status: "running", started_at: new Date().toISOString() };
    emit(jobId, { type: "agent_start", agent });
    await sleep(700 + Math.random() * 700);
    const { summary, details } = summaryFor(agent, ticker);
    run.agents[idx] = {
      ...run.agents[idx],
      status: "done",
      finished_at: new Date().toISOString(),
      summary,
      details,
    };
    if (agent === "Bull Researcher") run.bull_thesis = details;
    if (agent === "Bear Researcher") run.bear_thesis = details;
    if (agent === "Trader") run.execution_plan = details;
    if (agent === "Risk Manager") run.risk_assessment = details;
    emit(jobId, { type: "agent_done", agent, summary, details });
  }

  if (!jobs[jobId].cancelled) {
    const d = pickDecision();
    run.decision = d.decision;
    run.confidence = d.confidence;
    run.rationale = d.rationale;
    run.status = "done";
    run.finished_at = new Date().toISOString();
    emit(jobId, { type: "decision", action: d.decision, confidence: d.confidence, rationale: d.rationale });
  }
  db.runs.unshift(run);
  persist();
  jobs[jobId].status = run.status === "done" ? "done" : "failed";
  emit(jobId, { type: "complete", run_id: run.id });
}

async function runBacktestPipeline(
  jobId: string,
  userId: string,
  cfg: { ticker: string; start_date: string; end_date: string; interval_days: number; capital: number; analysts: string[] },
) {
  const start = new Date(cfg.start_date);
  const end = new Date(cfg.end_date);
  const dates: Date[] = [];
  for (let d = new Date(start); d <= end; d = addDays(d, cfg.interval_days)) dates.push(new Date(d));

  const trades: Run["agents"] = [];
  const equity: { date: string; value: number }[] = [];
  let portfolio = cfg.capital;
  let shares = 0;
  let lastPrice = 50 + Math.random() * 80;
  const tradeLog: any[] = [];

  jobs[jobId].status = "running";
  for (let i = 0; i < dates.length; i++) {
    if (jobs[jobId].cancelled) break;
    await sleep(450 + Math.random() * 250);
    lastPrice *= 1 + (Math.random() - 0.48) * 0.06;
    const decision = pickDecision().decision;
    const dateStr = format(dates[i], "yyyy-MM-dd");

    let realized = 0;
    let actionShares = 0;
    if (decision === "BUY" && portfolio > lastPrice * 100) {
      actionShares = Math.floor((portfolio * 0.3) / lastPrice);
      shares += actionShares;
      portfolio -= actionShares * lastPrice;
    } else if (decision === "SELL" && shares > 0) {
      actionShares = Math.floor(shares * 0.5);
      const pnlPerShare = lastPrice - 50;
      realized = actionShares * pnlPerShare;
      shares -= actionShares;
      portfolio += actionShares * lastPrice;
    }

    const equityVal = portfolio + shares * lastPrice;
    equity.push({ date: dateStr, value: +equityVal.toFixed(2) });

    const fwd = (Math.random() - 0.4) * 0.18;
    tradeLog.push({
      date: dateStr,
      action: decision,
      price: +lastPrice.toFixed(2),
      shares: actionShares,
      realized_pnl: +realized.toFixed(2),
      forward_return_20d: +fwd.toFixed(4),
      outcome: realized > 0 ? "WIN" : realized < 0 ? "LOSS" : fwd > 0 && decision === "BUY" ? "WIN" : decision === "SELL" && fwd < 0 ? "WIN" : "NEUTRAL",
      run_id: uid(),
    });

    emit(jobId, {
      type: "agent_progress",
      agent: "Backtest",
      message: `[${dateStr}] ${decision} at ${lastPrice.toFixed(2)} — equity ${equityVal.toFixed(0)}`,
    });
  }

  const finalEquity = portfolio + shares * lastPrice;
  const total_return_pct = ((finalEquity - cfg.capital) / cfg.capital) * 100;
  const wins = tradeLog.filter((t) => t.outcome === "WIN").length;
  const total = tradeLog.length;
  const bt: Backtest = {
    id: uid(),
    user_id: userId,
    type: "backtest",
    ticker: cfg.ticker,
    start_date: cfg.start_date,
    end_date: cfg.end_date,
    interval_days: cfg.interval_days,
    capital: cfg.capital,
    analysts: cfg.analysts,
    status: "done",
    trades: tradeLog as any,
    equity_curve: equity,
    metrics: {
      total_return_pct: +total_return_pct.toFixed(2),
      buy_hold_return_pct: +((Math.random() * 30 - 5)).toFixed(2),
      alpha_pct: +(total_return_pct - (Math.random() * 12)).toFixed(2),
      sharpe: +(0.6 + Math.random() * 1.6).toFixed(2),
      max_drawdown_pct: -+(5 + Math.random() * 18).toFixed(2),
      win_rate_pct: total ? +((wins / total) * 100).toFixed(1) : 0,
      hit_rate_pct: total ? +((tradeLog.filter((t) => (t.forward_return_20d || 0) > 0).length / total) * 100).toFixed(1) : 0,
      total_trades: total,
      final_portfolio: +finalEquity.toFixed(2),
    },
    created_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
  };
  db.backtests.unshift(bt);
  persist();
  jobs[jobId].run_id = bt.id;
  jobs[jobId].status = "done";
  emit(jobId, { type: "complete", run_id: bt.id });
}

export const runs = {
  async predict(token: string | null, body: { ticker: string; trade_date: string; portfolio_value: number }) {
    const userId = userIdOrThrow(token);
    await sleep(200);
    const job: Job = { id: uid(), kind: "predict", status: "pending" };
    jobs[job.id] = job;
    // fire off pipeline async
    setTimeout(() => runPipeline(job.id, userId, body.ticker, body.trade_date, body.portfolio_value), 50);
    return { job_id: job.id };
  },
  async getJob(jobId: string) {
    const j = jobs[jobId];
    if (!j) throw new Error("Job not found");
    return { job_id: j.id, status: j.status, run_id: j.run_id, error: j.error };
  },
  async cancel(jobId: string) {
    const j = jobs[jobId];
    if (j) j.cancelled = true;
    return { ok: true };
  },
  async list(
    token: string | null,
    params: {
      page?: number;
      page_size?: number;
      type?: string;
      decision?: string;
      ticker?: string;
      from?: string;
      to?: string;
      sort?: "newest" | "oldest" | "confidence";
    },
  ) {
    const userId = userIdOrThrow(token);
    await sleep(150);
    let items = db.runs.filter((r) => r.user_id === userId);
    if (params.type && params.type !== "all") items = items.filter((r) => r.type === params.type);
    if (params.decision && params.decision !== "all") items = items.filter((r) => r.decision === params.decision);
    if (params.ticker) items = items.filter((r) => r.ticker.toLowerCase().includes(params.ticker!.toLowerCase()));
    if (params.from) items = items.filter((r) => r.created_at >= params.from!);
    if (params.to) items = items.filter((r) => r.created_at <= params.to!);
    if (params.sort === "oldest") items = [...items].sort((a, b) => a.created_at.localeCompare(b.created_at));
    else if (params.sort === "confidence") items = [...items].sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
    else items = [...items].sort((a, b) => b.created_at.localeCompare(a.created_at));
    const page = params.page || 1;
    const ps = params.page_size || 20;
    return {
      items: items.slice((page - 1) * ps, page * ps),
      total: items.length,
      page,
      page_size: ps,
    };
  },
  async get(token: string | null, id: string) {
    const userId = userIdOrThrow(token);
    await sleep(100);
    const r = db.runs.find((x) => x.id === id && x.user_id === userId);
    if (!r) throw new Error("Run not found");
    return r;
  },
  async delete(token: string | null, id: string) {
    const userId = userIdOrThrow(token);
    db.runs = db.runs.filter((r) => !(r.id === id && r.user_id === userId));
    persist();
    return { ok: true };
  },
};

export const backtests = {
  async start(
    token: string | null,
    body: { ticker: string; start_date: string; end_date: string; interval_days: number; capital: number; analysts: string[] },
  ) {
    const userId = userIdOrThrow(token);
    await sleep(200);
    const job: Job = { id: uid(), kind: "backtest", status: "pending" };
    jobs[job.id] = job;
    setTimeout(() => runBacktestPipeline(job.id, userId, body), 50);
    return { job_id: job.id };
  },
  async list(token: string | null, params: { page?: number }) {
    const userId = userIdOrThrow(token);
    await sleep(150);
    const items = db.backtests.filter((b) => b.user_id === userId);
    const page = params.page || 1;
    const ps = 20;
    return {
      items: items.slice((page - 1) * ps, page * ps),
      total: items.length,
      page,
      page_size: ps,
    };
  },
  async get(token: string | null, id: string) {
    const userId = userIdOrThrow(token);
    await sleep(100);
    const b = db.backtests.find((x) => x.id === id && x.user_id === userId);
    if (!b) throw new Error("Backtest not found");
    return b;
  },
  async delete(token: string | null, id: string) {
    const userId = userIdOrThrow(token);
    db.backtests = db.backtests.filter((b) => !(b.id === id && b.user_id === userId));
    persist();
    return { ok: true };
  },
};

// ---------- Mock WebSocket ----------
export class MockJobSocket {
  private bus: EventTarget;
  private listeners = new Set<(m: WSMessage) => void>();
  public connected = true;
  constructor(public jobId: string) {
    this.bus = jobBuses[jobId] = jobBuses[jobId] || new EventTarget();
    // Replay any buffered messages on next tick so subscribers attach first.
    queueMicrotask(() => {
      const buf = jobBuffers[jobId] || [];
      buf.forEach((m) => this.listeners.forEach((l) => l(m)));
    });
    this.bus.addEventListener("msg", this._onMsg);
  }
  private _onMsg = (e: Event) => {
    const m = (e as MessageEvent).data as WSMessage;
    this.listeners.forEach((l) => l(m));
  };
  on(cb: (m: WSMessage) => void) {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }
  close() {
    this.bus.removeEventListener("msg", this._onMsg);
    this.connected = false;
  }
}
