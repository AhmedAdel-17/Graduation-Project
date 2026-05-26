/**
 * Real backend client — talks to the FastAPI server at VITE_API_URL.
 * Drop-in replacement for mockApi.ts (same exported shape).
 *
 * Configure via .env / .env.local:
 *   VITE_API_URL=http://localhost:8000
 *   VITE_WS_URL=ws://localhost:8000   (optional — defaults to http→ws of VITE_API_URL)
 */
import type {
  User,
  Sector,
  Ticker,
  MacroSnapshot,
  Quote,
  Run,
  Backtest,
  WSMessage,
} from "@/types/api";

const API_BASE =
  (typeof import.meta !== "undefined" && (import.meta as any).env?.VITE_API_URL) ||
  "http://localhost:8000";

const WS_BASE =
  (typeof import.meta !== "undefined" && (import.meta as any).env?.VITE_WS_URL) ||
  API_BASE.replace(/^http/, "ws");

// ---------------------------------------------------------------------------
// fetch helper
// ---------------------------------------------------------------------------

interface FetchOpts extends RequestInit {
  token?: string | null;
  query?: Record<string, string | number | undefined>;
}

async function http<T = any>(path: string, opts: FetchOpts = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as any),
  };
  if (opts.token) headers["Authorization"] = `Bearer ${opts.token}`;

  let url = API_BASE + path;
  if (opts.query) {
    const params = new URLSearchParams();
    Object.entries(opts.query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
    });
    const qs = params.toString();
    if (qs) url += (path.includes("?") ? "&" : "?") + qs;
  }

  const res = await fetch(url, { ...opts, headers });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || body.message || JSON.stringify(body);
    } catch {
      try { detail = await res.text() || detail; } catch {}
    }
    throw new Error(detail);
  }

  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// auth
// ---------------------------------------------------------------------------

export const auth = {
  async signup(input: {
    name: string;
    email: string;
    password: string;
    sectors_of_interest: Sector[];
    alerts_enabled?: boolean;
  }) {
    return http<{ token: string; user: User }>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({
        name: input.name,
        email: input.email,
        password: input.password,
        sectors_of_interest: input.sectors_of_interest,
        alerts_enabled: input.alerts_enabled ?? false,
      }),
    });
  },

  async login(input: { email: string; password: string }) {
    return http<{ token: string; user: User }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  async me(token: string) {
    return http<{ user: User }>("/api/auth/me", { token });
  },

  async logout(token: string) {
    return http<{ status: string }>("/api/auth/logout", {
      method: "POST",
      token,
    });
  },
};

// ---------------------------------------------------------------------------
// market
// ---------------------------------------------------------------------------

export const market = {
  async tickers(): Promise<Ticker[]> {
    return http<Ticker[]>("/api/tickers");
  },

  async macroCurrent(): Promise<MacroSnapshot> {
    return http<MacroSnapshot>("/api/macro/current");
  },

  async egx30History(days = 30) {
    return http<{ date: string; close: number }[]>("/api/macro/egx30-history", {
      query: { days },
    });
  },

  async quote(symbol: string): Promise<Quote> {
    return http<Quote>(`/api/ticker/${encodeURIComponent(symbol)}/quote`);
  },
};

// ---------------------------------------------------------------------------
// runs (single predictions)
// ---------------------------------------------------------------------------

export const runs = {
  async predict(
    token: string | null,
    body: {
      ticker: string;
      trade_date: string;
      portfolio_value: number;
      risk_appetite?: "conservative" | "balanced" | "aggressive";
    },
  ) {
    return http<{ job_id: string; run_id?: string }>("/api/predict", {
      method: "POST",
      body: JSON.stringify(body),
      token,
    });
  },

  async getJob(jobId: string) {
    // No token here — match the mock's signature (some callers don't have it).
    // We'll just include the token from the auth store via a helper at call site.
    // For now, allow callers that DO have a token to pass it via the wrapper.
    return http<{ job_id: string; status: string; run_id?: string; error?: string }>(
      `/api/jobs/${encodeURIComponent(jobId)}`,
      { token: getStoredToken() },
    );
  },

  async cancel(jobId: string) {
    await http<{ status: string }>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
      token: getStoredToken(),
    });
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
    return http<{ items: Run[]; total: number; page: number; page_size: number }>(
      "/api/runs",
      { token, query: { ...params } as any },
    );
  },

  async get(token: string | null, id: string) {
    return http<Run>(`/api/runs/${encodeURIComponent(id)}`, { token });
  },

  async delete(token: string | null, id: string) {
    await http<{ status: string }>(`/api/runs/${encodeURIComponent(id)}`, {
      method: "DELETE",
      token,
    });
    return { ok: true };
  },
};

// ---------------------------------------------------------------------------
// backtests
// ---------------------------------------------------------------------------

export const backtests = {
  async start(
    token: string | null,
    body: {
      ticker: string;
      start_date: string;
      end_date: string;
      interval_days: number;
      capital: number;
      analysts: string[];
    },
  ) {
    return http<{ job_id: string; backtest_id?: string }>("/api/backtest", {
      method: "POST",
      body: JSON.stringify(body),
      token,
    });
  },

  async list(
    token: string | null,
    params: { page?: number; page_size?: number },
  ) {
    return http<{
      items: Backtest[];
      total: number;
      page: number;
      page_size: number;
    }>("/api/backtests", { token, query: params as any });
  },

  async get(token: string | null, id: string) {
    return http<Backtest>(`/api/backtests/${encodeURIComponent(id)}`, { token });
  },

  async delete(token: string | null, id: string) {
    await http<{ status: string }>(`/api/backtests/${encodeURIComponent(id)}`, {
      method: "DELETE",
      token,
    });
    return { ok: true };
  },
};

// ---------------------------------------------------------------------------
// Token lookup — pulled lazily from zustand store to avoid circular import
// ---------------------------------------------------------------------------

function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem("ta_auth");
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed?.state?.token ?? null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Real WebSocket — drop-in for MockJobSocket
// ---------------------------------------------------------------------------

export class MockJobSocket {
  private ws: WebSocket | null = null;
  private listeners = new Set<(m: WSMessage) => void>();
  public connected = false;
  private reconnectAttempts = 0;
  private maxReconnects = 5;
  private closed = false;

  constructor(public jobId: string) {
    this._connect();
  }

  private _connect() {
    const token = getStoredToken();
    const url = `${WS_BASE}/ws/jobs/${encodeURIComponent(this.jobId)}?token=${encodeURIComponent(token || "")}`;

    try {
      this.ws = new WebSocket(url);
    } catch (e) {
      console.error("WebSocket construction failed:", e);
      return;
    }

    this.ws.onopen = () => {
      this.connected = true;
      this.reconnectAttempts = 0;
    };

    this.ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data) as WSMessage;
        this.listeners.forEach((l) => l(msg));
      } catch (err) {
        console.error("Invalid WS message:", e.data, err);
      }
    };

    this.ws.onclose = () => {
      this.connected = false;
      if (this.closed) return;
      if (this.reconnectAttempts < this.maxReconnects) {
        const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 10000);
        this.reconnectAttempts++;
        setTimeout(() => this._connect(), delay);
      }
    };

    this.ws.onerror = (err) => {
      console.warn("WebSocket error", err);
    };
  }

  on(cb: (m: WSMessage) => void) {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  close() {
    this.closed = true;
    if (this.ws) {
      try { this.ws.close(); } catch {}
      this.ws = null;
    }
    this.listeners.clear();
    this.connected = false;
  }
}
