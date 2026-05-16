import { useEffect, useMemo, useRef, useState } from "react";
import { Calendar, ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "../../lib/utils";

interface Props {
  value: string;            // ISO yyyy-mm-dd
  onChange: (v: string) => void;
  className?: string;
  min?: string;
  max?: string;
  placeholder?: string;
}

const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function isoToParts(iso: string): { y: number; m: number; d: number } | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return null;
  const [y, m, d] = iso.split("-").map(Number);
  return { y, m: m - 1, d };
}
function partsToIso(y: number, m: number, d: number) {
  return `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}
function displayFormat(iso: string) {
  const p = isoToParts(iso);
  if (!p) return iso || "";
  return `${MONTHS[p.m].slice(0, 3)} ${p.d}, ${p.y}`;
}
function daysInMonth(y: number, m: number) {
  return new Date(y, m + 1, 0).getDate();
}

type Mode = "days" | "months" | "years";
const YEARS_PER_PAGE = 12;

export function DatePicker({ value, onChange, className, min, max, placeholder = "Pick a date" }: Props) {
  const today = useMemo(() => {
    const t = new Date();
    return { y: t.getFullYear(), m: t.getMonth(), d: t.getDate() };
  }, []);
  const initial = isoToParts(value) || today;
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>("days");
  const [viewY, setViewY] = useState(initial.y);
  const [viewM, setViewM] = useState(initial.m);
  // Top-left year of the years-grid page (so the page is stable while paging)
  const [yearPageStart, setYearPageStart] = useState(
    initial.y - (initial.y % YEARS_PER_PAGE)
  );
  const rootRef = useRef<HTMLDivElement>(null);

  // Resync view when value prop changes externally
  useEffect(() => {
    const p = isoToParts(value);
    if (p) {
      setViewY(p.y);
      setViewM(p.m);
      setYearPageStart(p.y - (p.y % YEARS_PER_PAGE));
    }
  }, [value]);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        // Reset to day grid for next opening so the user always lands on days first
        setTimeout(() => setMode("days"), 150);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const minParts = min ? isoToParts(min) : null;
  const maxParts = max ? isoToParts(max) : null;
  function disabled(y: number, m: number, d: number) {
    const t = new Date(y, m, d).getTime();
    if (minParts) {
      const tmin = new Date(minParts.y, minParts.m, minParts.d).getTime();
      if (t < tmin) return true;
    }
    if (maxParts) {
      const tmax = new Date(maxParts.y, maxParts.m, maxParts.d).getTime();
      if (t > tmax) return true;
    }
    return false;
  }
  // A whole month is disabled only if every day in it is out of bounds.
  function monthDisabled(y: number, m: number) {
    const lastDayOfMonth = daysInMonth(y, m);
    if (minParts) {
      const tmin = new Date(minParts.y, minParts.m, minParts.d).getTime();
      const tMonthEnd = new Date(y, m, lastDayOfMonth).getTime();
      if (tMonthEnd < tmin) return true;
    }
    if (maxParts) {
      const tmax = new Date(maxParts.y, maxParts.m, maxParts.d).getTime();
      const tMonthStart = new Date(y, m, 1).getTime();
      if (tMonthStart > tmax) return true;
    }
    return false;
  }
  function yearDisabled(y: number) {
    if (minParts && y < minParts.y) return true;
    if (maxParts && y > maxParts.y) return true;
    return false;
  }

  const selected = isoToParts(value);
  const firstDow = new Date(viewY, viewM, 1).getDay();
  const dim = daysInMonth(viewY, viewM);
  const prevDim = daysInMonth(viewY, viewM - 1);

  // Build a 6×7 grid of cells (leading prev-month, current, trailing next-month)
  const cells: { y: number; m: number; d: number; inMonth: boolean }[] = [];
  for (let i = 0; i < firstDow; i++) {
    const d = prevDim - firstDow + 1 + i;
    const m = viewM === 0 ? 11 : viewM - 1;
    const y = viewM === 0 ? viewY - 1 : viewY;
    cells.push({ y, m, d, inMonth: false });
  }
  for (let d = 1; d <= dim; d++) {
    cells.push({ y: viewY, m: viewM, d, inMonth: true });
  }
  while (cells.length < 42) {
    const offset = cells.length - (firstDow + dim) + 1;
    const m = viewM === 11 ? 0 : viewM + 1;
    const y = viewM === 11 ? viewY + 1 : viewY;
    cells.push({ y, m, d: offset, inMonth: false });
  }

  function shiftMonth(delta: number) {
    const total = viewY * 12 + viewM + delta;
    setViewY(Math.floor(total / 12));
    setViewM(total % 12);
  }
  function pick(c: { y: number; m: number; d: number }) {
    onChange(partsToIso(c.y, c.m, c.d));
    setOpen(false);
  }
  function pickToday() {
    onChange(partsToIso(today.y, today.m, today.d));
    setViewY(today.y);
    setViewM(today.m);
    setOpen(false);
  }
  function clear() {
    onChange("");
    setOpen(false);
  }

  return (
    <div className={cn("relative", className)} ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "w-full h-12 px-4 rounded-xl bg-white border border-stone-200 hover:border-stone-300 transition-colors",
          "flex items-center justify-between gap-2 text-left",
          open && "border-stone-900 ring-4 ring-stone-900/5"
        )}
      >
        <span className="flex items-center gap-2 min-w-0">
          <Calendar className="h-4 w-4 text-stone-500 shrink-0" />
          <span
            className={cn(
              "mono text-[14px]",
              value ? "text-ink" : "text-stone-400"
            )}
          >
            {value ? displayFormat(value) : placeholder}
          </span>
        </span>
      </button>

      {open && (
        <div className="absolute z-50 mt-2 w-[300px] rounded-2xl border border-stone-200 bg-white shadow-[0_16px_40px_-12px_rgba(15,15,15,0.18)] overflow-hidden anim-fade-up">
          {/* Header — adapts per mode */}
          <div className="flex items-center justify-between px-3 pt-3">
            <button
              type="button"
              onClick={() => {
                if (mode === "days") shiftMonth(-1);
                else if (mode === "months") setViewY((y) => y - 1);
                else setYearPageStart((s) => s - YEARS_PER_PAGE);
              }}
              className="h-8 w-8 rounded-lg hover:bg-stone-100 text-stone-600 flex items-center justify-center"
              aria-label="Previous"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>

            {mode === "days" && (
              <button
                type="button"
                onClick={() => setMode("months")}
                className="display text-[14px] font-semibold text-ink hover:bg-stone-100 rounded-md px-2.5 py-1 transition-colors"
              >
                {MONTHS[viewM]} {viewY}
              </button>
            )}
            {mode === "months" && (
              <button
                type="button"
                onClick={() => {
                  setYearPageStart(viewY - (viewY % YEARS_PER_PAGE));
                  setMode("years");
                }}
                className="display text-[14px] font-semibold text-ink hover:bg-stone-100 rounded-md px-2.5 py-1 transition-colors"
              >
                {viewY}
              </button>
            )}
            {mode === "years" && (
              <div className="display text-[14px] font-semibold text-ink px-2.5 py-1">
                {yearPageStart} – {yearPageStart + YEARS_PER_PAGE - 1}
              </div>
            )}

            <button
              type="button"
              onClick={() => {
                if (mode === "days") shiftMonth(1);
                else if (mode === "months") setViewY((y) => y + 1);
                else setYearPageStart((s) => s + YEARS_PER_PAGE);
              }}
              className="h-8 w-8 rounded-lg hover:bg-stone-100 text-stone-600 flex items-center justify-center"
              aria-label="Next"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>

          {/* ─── Days mode ─────────────────────────────────────────── */}
          {mode === "days" && (
            <>
              <div className="grid grid-cols-7 px-3 pt-3 pb-1">
                {WEEKDAYS.map((w) => (
                  <div
                    key={w}
                    className="text-center text-[10px] tracking-[0.15em] uppercase text-stone-400 font-medium"
                  >
                    {w}
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-7 px-2 pb-2 gap-0.5">
                {cells.map((c, i) => {
                  const isToday =
                    c.y === today.y && c.m === today.m && c.d === today.d;
                  const isSelected =
                    selected &&
                    c.y === selected.y &&
                    c.m === selected.m &&
                    c.d === selected.d;
                  const isDisabled = disabled(c.y, c.m, c.d);
                  return (
                    <button
                      key={i}
                      type="button"
                      disabled={isDisabled}
                      onClick={() => !isDisabled && pick(c)}
                      className={cn(
                        "h-9 rounded-lg mono text-[12.5px] flex items-center justify-center transition-colors",
                        isSelected
                          ? "bg-stone-900 text-white font-semibold"
                          : c.inMonth
                          ? "text-ink hover:bg-stone-100"
                          : "text-stone-300 hover:bg-stone-50",
                        isToday && !isSelected && "ring-1 ring-stone-300",
                        isDisabled && "opacity-30 cursor-not-allowed hover:bg-transparent"
                      )}
                    >
                      {c.d}
                    </button>
                  );
                })}
              </div>
            </>
          )}

          {/* ─── Months mode ───────────────────────────────────────── */}
          {mode === "months" && (
            <div className="grid grid-cols-3 gap-1.5 px-3 pt-4 pb-3">
              {MONTHS.map((label, m) => {
                const isSelected =
                  selected && selected.y === viewY && selected.m === m;
                const isCurrent =
                  today.y === viewY && today.m === m && !isSelected;
                const isDisabled = monthDisabled(viewY, m);
                return (
                  <button
                    key={label}
                    type="button"
                    disabled={isDisabled}
                    onClick={() => {
                      if (isDisabled) return;
                      setViewM(m);
                      setMode("days");
                    }}
                    className={cn(
                      "h-11 rounded-lg text-[12.5px] font-medium transition-colors",
                      isSelected
                        ? "bg-stone-900 text-white"
                        : "text-ink hover:bg-stone-100",
                      isCurrent && "ring-1 ring-stone-300",
                      isDisabled && "opacity-30 cursor-not-allowed hover:bg-transparent"
                    )}
                  >
                    {label.slice(0, 3)}
                  </button>
                );
              })}
            </div>
          )}

          {/* ─── Years mode ────────────────────────────────────────── */}
          {mode === "years" && (
            <div className="grid grid-cols-3 gap-1.5 px-3 pt-4 pb-3">
              {Array.from({ length: YEARS_PER_PAGE }, (_, i) => yearPageStart + i).map(
                (y) => {
                  const isSelected = selected && selected.y === y;
                  const isCurrent = today.y === y && !isSelected;
                  const isDisabled = yearDisabled(y);
                  return (
                    <button
                      key={y}
                      type="button"
                      disabled={isDisabled}
                      onClick={() => {
                        if (isDisabled) return;
                        setViewY(y);
                        setMode("months");
                      }}
                      className={cn(
                        "h-11 rounded-lg mono text-[13px] font-medium transition-colors",
                        isSelected
                          ? "bg-stone-900 text-white"
                          : "text-ink hover:bg-stone-100",
                        isCurrent && "ring-1 ring-stone-300",
                        isDisabled && "opacity-30 cursor-not-allowed hover:bg-transparent"
                      )}
                    >
                      {y}
                    </button>
                  );
                }
              )}
            </div>
          )}

          {/* Footer actions */}
          <div className="flex items-center justify-between border-t border-stone-100 px-3 py-2">
            <button
              type="button"
              onClick={clear}
              className="text-[12px] text-stone-500 hover:text-ink font-medium px-2 py-1 rounded-md hover:bg-stone-50"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={pickToday}
              className="text-[12px] text-ink font-medium px-2 py-1 rounded-md hover:bg-stone-100"
            >
              Today
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
