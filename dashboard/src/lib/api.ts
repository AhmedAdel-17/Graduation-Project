/**
 * Public API surface — talks to the FastAPI backend at VITE_API_URL.
 *
 * To revert to the mock backend during local dev, set VITE_USE_MOCK=1 in .env.local
 * (the import line below will switch to ./mockApi).
 */
import { useAuthStore } from "./store";

// Switch backends via env flag. Default = real.
const USE_MOCK =
  typeof import.meta !== "undefined" &&
  (import.meta as any).env?.VITE_USE_MOCK === "1";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let backend: any;
if (USE_MOCK) {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  backend = await import("./mockApi");
} else {
  backend = await import("./realApi");
}

const token = () => useAuthStore.getState().token;

export const api = {
  auth: {
    signup: backend.auth.signup,
    login: backend.auth.login,
    me: () => backend.auth.me(token()!),
    logout: () => backend.auth.logout(token()!),
  },
  market: {
    tickers: backend.market.tickers,
    macroCurrent: backend.market.macroCurrent,
    egx30History: backend.market.egx30History,
    quote: backend.market.quote,
  },
  runs: {
    predict: (b: Parameters<typeof backend.runs.predict>[1]) =>
      backend.runs.predict(token(), b),
    getJob: backend.runs.getJob,
    cancel: backend.runs.cancel,
    list: (p: Parameters<typeof backend.runs.list>[1]) =>
      backend.runs.list(token(), p),
    get: (id: string) => backend.runs.get(token(), id),
    delete: (id: string) => backend.runs.delete(token(), id),
  },
  backtests: {
    start: (b: Parameters<typeof backend.backtests.start>[1]) =>
      backend.backtests.start(token(), b),
    list: (p: Parameters<typeof backend.backtests.list>[1]) =>
      backend.backtests.list(token(), p),
    get: (id: string) => backend.backtests.get(token(), id),
    delete: (id: string) => backend.backtests.delete(token(), id),
  },
  jobSocket: (jobId: string) => new backend.MockJobSocket(jobId),
};
