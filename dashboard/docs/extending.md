# Extending the Dashboard

Concrete recipes for adding a page, a chart, a tab, a hook, and an endpoint.
Lifts straight from how the PR3→PR9 increments were built — see the commit log
for full examples.

---

## 1. Adding a new page

1. **Pick the path and the nav slot.** Routes are flat in `src/App.tsx`. Sidebar
   groups are: Research / Strategy / System.
2. **Create the feature folder:** `src/features/<feature>/<Feature>Page.tsx`.
   Compose `<AppShell title={t("...title")} subtitle={t("...subtitle")}>`.
3. **Wire i18n keys.** Open `src/lib/i18n.ts`, add the namespace under both
   `en` and `ar` dicts. Don't ship EN-only — the AR dict's fallback path will
   render the raw key string (visible to RTL users).
4. **Wire the route** in `App.tsx`. If the page belongs in the Sidebar, add an
   entry to `src/components/layout/Sidebar.tsx`'s `nav` array (and
   `MobileNav.tsx`).
5. **Pick a Lucide icon.** Sidebar enforces icon consistency — pick one verb
   icon (`Compass` for browser, `FlaskConical` for lab, `Cpu` for diagnostics).
6. **Run the gauntlet:**
   - `npm run lint`
   - `npm run build`
   - Eyeball both LTR and RTL by clicking the locale toggle in the TopBar.

Minimal skeleton:

```tsx
// src/features/insights/InsightsPage.tsx
import { AppShell } from "../../components/layout/AppShell";
import { Card, CardBody, CardHeader, CardTitle } from "../../components/ui/Card";
import { useT } from "../../lib/i18n";

export function InsightsPage() {
  const t = useT();
  return (
    <AppShell title={t("insights.title")} subtitle={t("insights.subtitle")}>
      <div className="flex flex-col gap-5">
        <Card>
          <CardHeader>
            <CardTitle>{t("insights.section.title")}</CardTitle>
          </CardHeader>
          <CardBody>{/* … */}</CardBody>
        </Card>
      </div>
    </AppShell>
  );
}
```

---

## 2. Adding a tab to the Workspace

`features/workspace/WorkspacePage.tsx` owns the tab shell. Add a tab by:

1. Importing a new `*Tab.tsx` component.
2. Adding it to the `Tabs` configuration block at the top of the file.
3. Adding `workspace.tab.<key>` to both i18n dicts.
4. **Use shared hooks** — `useLatestSessionTrace(ticker)` already wraps the
   results-index + trace fetch chain. Don't refetch from inside the tab.

`ReasoningView.tsx` is the shared phase-grouped renderer used by both the
Workspace > Reasoning tab and the Sessions detail page. If you're extracting
new "view a trace" logic, put it there.

---

## 3. Adding a chart

All chart wrappers live in `src/components/charts/`. They follow this pattern:

```tsx
import { useEffect, useRef } from "react";
import { createChart, type IChartApi } from "lightweight-charts";

export function FooChart({ data }: { data: Bar[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, { /* options */ });
    chartRef.current = chart;
    // … add series, set data …
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [data]);

  return <div ref={containerRef} className="ltr-island h-80" />;
}
```

The `.ltr-island` class is non-negotiable — `lightweight-charts` can't
position itself under RTL.

---

## 4. Adding an endpoint

The PR9 diagnostics endpoints are the freshest example end-to-end.

### 4.1 Backend

1. Add the route to `server/api_server.py`. Pattern: read-only `@app.get`,
   `Query` parameters with bounds, graceful degradation on DB unavailability
   (return `{source: "none", reason: "..."}`, not `500`).
2. Use `_trace_row_to_dict` to convert `psycopg2` `DictRow` into a JSON-safe
   dict (`Decimal → float`, `date/datetime → ISO string`).
3. Don't break the existing endpoints — match their shapes when extending
   (`source` / `total` / `reason` fields).
4. Write a test in `tests/test_api_<endpoint>.py`. Cover at minimum: missing
   data path (DB unreachable), happy path with `psycopg2` mocked via a
   `MagicMock` cursor, and the error path.

### 4.2 Frontend

1. Add the request/response types to `src/services/api/types.ts`. Keep
   nullable fields explicitly `T | null | undefined` rather than just `T?` —
   the audit data is full of nulls.
2. Bind the endpoint in `src/services/api/endpoints.ts`. Use the existing
   query-string builder pattern (`URLSearchParams`, only set when defined).
3. Wrap it in a hook in `src/hooks/use<Feature>.ts`. Pick the query key
   tuple deliberately (see `api-integration.md` §3) and set a stale time.
4. Consume in a page. Render `Skeleton` while loading, `EmptyState` when the
   response is empty or `source === "none"`, the table/cards otherwise.

---

## 5. Adding a UI primitive

Don't, unless it'll be used by ≥2 features. The bar:

- It must be reusable across feature folders.
- It must accept className passthrough.
- It must respect the design tokens (no hardcoded hex).
- It must work in RTL.
- It must work for keyboard-only users.

If those check out, drop it in `src/components/ui/<Name>.tsx`, re-export
from `ui/index.ts`, and update `design-system.md` §2.

Otherwise, keep it in the feature folder as a local component.

---

## 6. Adding a locale

We support `en` and `ar`. To add another:

1. Extend the `Locale` type in `lib/i18n.ts`.
2. Copy `en` to a new dict, translate.
3. Decide on direction: `applyDocumentAttrs` only flips to `rtl` for `ar` —
   widen it if the new locale needs RTL.
4. Update `LocaleToggle` to show three buttons instead of two.
5. Audit Cairo fallback in `index.css:25` — if the new locale needs a
   different font stack, extend the `html[dir="rtl"] body` selector.

Numerals stay Latin in all cases (institutional convention).

---

## 7. Performance gotchas

- **Don't `useState({...new}) inside render`.** React 19's hooks plugin will
  warn. Use a lazy initializer: `useState(() => createInitial())`.
- **Don't read refs during render.** Convert refs to state if you need them
  during `useMemo`. The `expectingEngine` field in `NewBacktestPage.tsx` is
  the canonical example.
- **Don't `useMemo` a `??[]` default.** The fallback array is a new
  reference each render and busts the memo. Move it inside the `useMemo`:
  ```ts
  const daily = useMemo(() => response?.daily_counts ?? [], [response]);
  ```
- **Don't re-render the whole tree on locale change.** `useT()` is already
  memo-stable per locale. Keep it.

---

## 8. Testing

There's no Vitest + React Testing Library suite yet — the dashboard relies on
TypeScript type-checking, ESLint, and the backend pytest suite for safety.
Two paths forward:

1. **Manual gauntlet** (current default). See `onboarding.md` §5.
2. **Playwright smoke tests** (planned). The skeleton goes under
   `dashboard/tests/e2e/`. Recipes:
   - Hit `/workspace`, assert the disclaimer modal appears, dismiss it,
     verify the URL persists `?ticker=COMI.CA`.
   - Hit `/run`, fill in COMI.CA + today, click Start, assert the timeline
     mounts (mocking the WS).
   - Hit `/backtest/new`, fill the wizard, click Run Multi-Agent, assert the
     toast and the auto-redirect to `/backtest/:id`.

Until Playwright lands, every PR should run `npm run lint && npm run build`
locally before merge.
