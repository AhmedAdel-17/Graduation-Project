"use client";

import * as React from "react";
import { format } from "date-fns";
import { CalendarIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

interface DatePickerProps {
  /** Selected date as a yyyy-MM-dd string (or undefined / "") */
  value: string;
  /** Called with a yyyy-MM-dd string */
  onChange: (v: string) => void;
  /** Earliest selectable date (inclusive). yyyy-MM-dd or Date. */
  minDate?: string | Date;
  /** Latest selectable date (inclusive). yyyy-MM-dd or Date. */
  maxDate?: string | Date;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
}

/**
 * Pretty calendar date picker with built-in min/max validation.
 *
 *  - Click the field → pops a react-day-picker calendar
 *  - Dates outside [minDate, maxDate] are visually dimmed and unclickable
 *  - Outputs a yyyy-MM-dd string (so it drops into form state cleanly)
 */
export function DatePicker({
  value,
  onChange,
  minDate,
  maxDate,
  placeholder = "Pick a date",
  className,
  disabled,
}: DatePickerProps) {
  const [open, setOpen] = React.useState(false);

  const selected = value ? parseISODate(value) : undefined;
  const min = minDate ? toDate(minDate) : undefined;
  const max = maxDate ? toDate(maxDate) : undefined;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          className={cn(
            "w-full justify-start text-left font-mono tabular",
            !selected && "text-muted-foreground",
            className,
          )}
        >
          <CalendarIcon className="mr-2 h-4 w-4 shrink-0 opacity-60" />
          {selected ? format(selected, "MMM d, yyyy") : placeholder}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          captionLayout="dropdown"
          selected={selected}
          onSelect={(d) => {
            if (d) {
              onChange(format(d, "yyyy-MM-dd"));
              setOpen(false);
            }
          }}
          disabled={(date) => {
            if (min && date < min) return true;
            if (max && date > max) return true;
            return false;
          }}
          defaultMonth={selected ?? max ?? new Date()}
          // Bound the year dropdown so users can't scroll into nonsense decades
          startMonth={min ?? new Date(2000, 0, 1)}
          endMonth={max ?? new Date()}
        />
      </PopoverContent>
    </Popover>
  );
}

// ─── helpers ───────────────────────────────────────────────────────────────

function toDate(v: string | Date): Date {
  return v instanceof Date ? v : parseISODate(v);
}

/** Parse a yyyy-MM-dd string as a local-date (not UTC midnight). */
function parseISODate(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}
