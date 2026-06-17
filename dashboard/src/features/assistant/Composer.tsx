import { useState } from "react";
import { SendHorizonal } from "lucide-react";

/** Message composer. Enter sends; Shift+Enter inserts a newline. RTL-aware. */
export function Composer({
  onSend,
  disabled,
  placeholder = "Tell me what you hold, or ask for a rebalance…",
}: {
  onSend: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [value, setValue] = useState("");

  const submit = () => {
    const t = value.trim();
    if (!t || disabled) return;
    onSend(t);
    setValue("");
  };

  return (
    <div className="flex items-end gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2 dark:border-[var(--hairline)] dark:bg-[var(--paper)] focus-within:border-stone-300 dark:focus-within:border-white/20">
      <textarea
        dir="auto"
        rows={1}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={placeholder}
        className="flex-1 resize-none bg-transparent text-[13px] text-ink placeholder:text-stone-400 dark:placeholder:text-[var(--ink-3)] outline-none max-h-32 py-1"
      />
      <button
        type="button"
        onClick={submit}
        disabled={disabled || !value.trim()}
        aria-label="Send"
        className="inline-flex items-center justify-center h-8 w-8 rounded-lg bg-stone-900 text-white disabled:opacity-40 hover:bg-stone-800 transition-colors dark:bg-white dark:text-stone-900 dark:hover:bg-stone-200"
      >
        <SendHorizonal className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}
