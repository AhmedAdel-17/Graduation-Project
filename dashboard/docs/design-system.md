# Design System

> Tokens, primitives, theming rules, and the RTL / a11y conventions every
> new screen must honor.

---

## 1. Theme

Dark-only for now (no light theme, per scope). Background is a near-black ink palette
with two faint radial gradients applied to `<body>` (brand-green top-right, accent-blue
top-left). All surfaces are translucent — they read off the gradient.

### 1.1 Color tokens (`tailwind.config.js`)

| Token | Hex | Meaning |
|---|---|---|
| `ink-950` | `#0a0d12` | Page background |
| `ink-900` | `#0e1218` | Sidebar / TopBar tint |
| `ink-800` | `#151a22` | Cards, table rows, chips |
| `ink-700` | `#1c2230` | Hover surfaces, secondary buttons |
| `ink-600` | `#262d3d` | Pressed / active |
| `line` | `#1f2633` | Default border |
| `line-strong` | `#2a3345` | Emphasis border (hover, active card) |
| `fg` | `#e6e9ef` | Primary text |
| `fg-muted` | `#8b94a7` | Secondary text |
| `fg-subtle` | `#5e6678` | Meta / timestamps / hints |
| `brand-500` | `#10b981` | Primary action, focus rings, active nav |
| `brand-400` | `#34d399` | Brand text accent |
| `up` | `#10b981` | Positive PnL, BUY signal |
| `down` | `#f43f5e` | Negative PnL, SELL signal, errors |
| `accent` | `#7c9cff` | Backtest engine markers, info pills |
| `nosignal` | `#d97706` | Amber — Layer-A0/A/B/C NO_SIGNAL gates |
| `parsefail` | `#c026d3` | Magenta — JSON parse fell back to keyword regex |
| `regime.calm` / `.elevated` / `.stressed` | `#10b981` / `#d97706` / `#f43f5e` | Sentiment regime tiles |

Use semantic tokens, not raw hexes. If a new state shows up that doesn't fit, add the
token; don't reach for `#cccccc` inline.

### 1.2 Typography

- Sans: `Inter` (LTR), `Cairo` (RTL via `html[dir="rtl"]` override)
- Mono: `JetBrains Mono` — applied via the `.num` utility for all monetary values,
  fingerprints, percentages, dates, session-ids
- Numerals stay LTR even on Arabic pages (`direction: ltr; unicode-bidi: isolate`)

### 1.3 Spacing / sizing rhythm

Default card padding is `px-5 py-3` (header) / `px-5 pb-5` (body). Inputs are `h-9`,
small buttons `h-7`, primary buttons `h-10`. Sticky-bar height is `h-16`.

### 1.4 Borders & shadows

- Borders are always `border-line` or `border-line-strong`. No solid `#fff` strokes.
- `surface` (in `index.css`) is the canonical translucent card class. Use it on cards;
  never hand-roll the gradient backdrop.
- `shadow-card` is the only card shadow. `shadow-glow` is reserved for primary buttons.

---

## 2. Primitives

All live under `src/components/ui/` and are re-exported from `ui/index.ts`. Prefer
extending an existing primitive over building a bespoke version inside a feature folder.

| Primitive | Source | When to use |
|---|---|---|
| `Button` | `ui/Button.tsx` | Anywhere an action is triggered. `variant=primary` (CTA) / `outline` (secondary) / `ghost` (icon-only) / `danger`. Has built-in `aria-busy` when `loading` |
| `Card` / `CardHeader` / `CardBody` / `CardTitle` / `CardDescription` / `CardFooter` | `ui/Card.tsx` | Every content surface |
| `Badge` | `ui/Badge.tsx` | Status, count, signal pill. Tones: `neutral`/`brand`/`up`/`down`/`accent`/`warning` |
| `StatusPill` | `ui/StatusPill.tsx` | Agent-node status (`idle` / `in_progress` / `completed` / `error`) |
| `Input` | `ui/Input.tsx` | Forms with `label` + `hint` + `rightAddon` |
| `Select` | `ui/Select.tsx` | Native `<select>`, styled |
| `StockSelector` | `ui/StockSelector.tsx` | Ticker chooser; reads `/api/test/egx-tickers` with fallback |
| `Tabs` / `TabsList` / `TabsTrigger` / `TabsContent` | `ui/Tabs.tsx` | Workspace tabs, Reasoning canvas |
| `Tooltip` | `ui/Tooltip.tsx` | Hover hints — keep to single-line content |
| `Dialog` | `ui/Dialog.tsx` | Disclaimer modal, confirmation prompts |
| `Drawer` | `ui/Drawer.tsx` | Optional side panels (not currently used in main flows) |
| `JSONViewer` | `ui/JSONViewer.tsx` | Audit payloads, fingerprints, raw health blobs |
| `Markdown` | `ui/Markdown.tsx` | Narrative reports from analysts (whitelisted renderer) |
| `Gauge` | `ui/Gauge.tsx` | Confidence bars, signal coherence |
| `Skeleton` | `ui/Skeleton.tsx` | Use during `isLoading` instead of spinners on a content card |
| `Spinner` | `ui/Spinner.tsx` | Small inline loader; the `Button` already has one |
| `EmptyState` | `ui/EmptyState.tsx` | "No data yet" surfaces — always include an `action` if recovery is possible |
| `LocaleToggle` | `ui/LocaleToggle.tsx` | EN / AR switch (TopBar + Settings) |

---

## 3. Page layout

Every page composes `<AppShell title subtitle>` around its content:

```tsx
<AppShell title={t("foo.title")} subtitle={t("foo.subtitle")}>
  <div className="flex flex-col gap-5">
    <Card>…</Card>
    <Card>…</Card>
  </div>
</AppShell>
```

`AppShell` renders Sidebar (desktop), MobileNav (under TopBar on mobile), TopBar, the
disclaimer ribbon, the main slot, and SiteFooter. Use `gap-5` between cards on the
same page; do not add custom padding to `<main>`.

---

## 4. Accessibility rules

- **Interactive elements are `<button>`, `<a>`, or `<label>` — never `<div onClick>`.**
  The global focus ring in `index.css:36` targets only those tags + `[role="button"]`
  and `[role="tab"]`. A `<div onClick>` will be unreachable by keyboard.
- **Focus-visible**: the global rule covers most cases. Custom buttons should add
  `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50`
  inside their class string if they override the default outline.
- **aria-live**: required on any region that updates without an explicit user action.
  Current `aria-live="polite"` regions are `AgentTimeline` and the TopBar health pill.
  Add it to new streaming surfaces.
- **aria-busy**: `Button` sets it automatically when `loading`. For non-Button surfaces
  that show a skeleton during a fetch, set `aria-busy={isLoading}` on the wrapping card.
- **Table `aria-label`**: every `<table>` has `aria-label={t("...table.aria")}`. Keep
  this convention so screen readers can navigate by landmark.
- **Color contrast**: `fg-muted` (`#8b94a7`) on `ink-800` (`#151a22`) is ~6.5:1 AA-pass.
  Don't drop below `fg-subtle` for any text that needs to be read; that token is for
  meta-data only.
- **Lang + direction**: `applyDocumentAttrs(locale)` in `lib/i18n.ts:915` flips
  `<html lang dir>`. All Tailwind layout uses logical properties where it matters
  (`me-*` / `ms-*` are not in Tailwind 3.4 by default — we use `text-end` and explicit
  flex directions instead).

---

## 5. RTL rules

1. **Charts stay LTR.** Wrap any chart container in `<div className="ltr-island">` or
   `dir="ltr"`. Lightweight-charts is not RTL-aware.
2. **Numbers stay LTR.** Apply the `.num` utility class to every span/cell that
   contains a number, fingerprint hex, session id, or currency value. The CSS in
   `index.css:29` forces `direction: ltr` on `.num` regardless of the page direction.
3. **Mirrored icons.** Arrow icons (e.g. `ArrowLeft` for back navigation) read as
   "forward" in RTL. Most navigation arrows in this codebase are kept as-is for
   consistency with international finance UIs, but flip them with `rtl:rotate-180`
   if user feedback demands it.
4. **Test in both directions.** Toggle locale in the TopBar after any non-trivial
   layout change.

---

## 6. Naming + structure conventions

- Component files: `PascalCase.tsx`, one default-or-named export per file matching
  the filename.
- Hook files: `useFoo.ts` — one hook (and its tightly-coupled helpers) per file.
- Feature folders: `features/<feature>/<Page>.tsx` for the route; supporting tabs /
  cards live in the same folder with the feature name as prefix
  (`OverviewTab.tsx`, `BacktestDetailPage.tsx`).
- Service files: shapes in `services/api/types.ts`, bindings in `endpoints.ts`,
  custom hooks in `hooks/use*.ts`. Don't import `endpoints` from a component — go
  via a hook so the cache key stays canonical.

---

## 7. Animation

Only two animations are blessed:

- `animate-fade-in` (200 ms ease-out) — applied to `<main>` once on mount
- `animate-shimmer` (1.6 s linear infinite) — `Skeleton` shimmer

Don't introduce new keyframes without a strong reason. Charts have their own internal
animations and shouldn't be wrapped in additional ones.
