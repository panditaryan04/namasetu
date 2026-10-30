import { useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { autocomplete, type Suggestion } from "../services/api";

const SCRIPT_LABEL: Record<string, string> = {
  devanagari: "Devanagari",
  iast: "IAST",
  "harvard-kyoto": "Harvard-Kyoto",
  latin: "English / Hinglish",
};

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

interface AutocompleteProps {
  initial?: string;
  /** Controlled text, for callers that fill the box (e.g. suggestion chips). */
  value?: string;
  onValueChange?: (value: string) => void;
  inputRef?: React.RefObject<HTMLInputElement>;
  /** "hero": the large landing-page search with icon and gradient Map button. */
  variant?: "default" | "hero";
}

/** Accessible combobox (WAI-ARIA 1.2 pattern) with debounced autocomplete across four scripts. */
export function Autocomplete({
  initial = "",
  value,
  onValueChange,
  inputRef: externalRef,
  variant = "default",
}: AutocompleteProps) {
  const navigate = useNavigate();
  const listId = useId();
  const ownRef = useRef<HTMLInputElement>(null);
  const inputRef = externalRef ?? ownRef;
  const [ownText, setOwnText] = useState(initial);
  const text = value ?? ownText;
  const setText = (v: string) => (onValueChange ? onValueChange(v) : setOwnText(v));
  const hero = variant === "hero";
  const [items, setItems] = useState<Suggestion[]>([]);
  const [script, setScript] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [loading, setLoading] = useState(false);
  const debounced = useDebounced(text.trim(), 180);

  useEffect(() => {
    if (!debounced) {
      setItems([]);
      setScript(null);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    autocomplete(debounced, ctrl.signal)
      .then((r) => {
        setItems(r.items);
        setScript(r.query.script);
        setActive(-1);
      })
      .catch(() => {
        if (!ctrl.signal.aborted) setItems([]);
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [debounced]);

  const choose = (s: Suggestion) => {
    setOpen(false);
    navigate(`/map?code=${encodeURIComponent(s.code)}&q=${encodeURIComponent(text.trim() || s.display)}`);
  };
  const submitText = () => {
    if (!text.trim()) return;
    setOpen(false);
    navigate(`/map?q=${encodeURIComponent(text.trim())}`);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(a + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const chosen = active >= 0 ? items[active] : undefined;
      if (open && chosen) choose(chosen);
      else submitText();
    } else if (e.key === "Escape") {
      setOpen(false);
      setActive(-1);
    }
  };

  const showList = open && items.length > 0;
  return (
    <div className="relative">
      <form
        className="relative"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          submitText();
        }}
      >
        <label htmlFor={`${listId}-input`} className="sr-only">
          Search a diagnosis in English, IAST, Devanagari or Hinglish
        </label>
        <input
          ref={inputRef}
          id={`${listId}-input`}
          type="text"
          role="combobox"
          aria-expanded={showList}
          aria-controls={`${listId}-list`}
          aria-autocomplete="list"
          aria-activedescendant={active >= 0 ? `${listId}-opt-${active}` : undefined}
          autoComplete="off"
          spellCheck={false}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={onKeyDown}
          placeholder="e.g. Amavata · आमवात · Āmavāta · aamvaat"
          className={
            hero
              ? "h-16 w-full rounded-2xl border border-med-border bg-white pl-14 pr-32 text-lg text-med-heading shadow-sm transition duration-200 placeholder:text-slate-500 focus:border-med-primary focus:outline-none focus:ring-4 focus:ring-med-primary/25 focus:shadow-glass dark:bg-slate-900"
              : "w-full rounded-2xl border border-slate-300 bg-white px-5 py-4 pr-28 text-lg shadow-sm placeholder:text-slate-500 focus:border-teal-600 focus:outline-none focus:ring-4 focus:ring-teal-600/15 dark:border-slate-700 dark:bg-slate-900"
          }
        />
        {hero && (
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            className="pointer-events-none absolute left-5 top-1/2 h-5 w-5 -translate-y-1/2 text-med-primary-dark"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
        )}
        <button
          type="submit"
          aria-label={hero ? "Map this diagnosis" : undefined}
          className={
            hero
              ? "group absolute right-2 top-2 flex h-12 items-center gap-2 rounded-xl bg-brand-gradient-strong px-5 text-sm font-semibold text-white shadow-glass-sm transition duration-200 hover:-translate-y-0.5 hover:shadow-lift focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-med-primary-dark"
              : "absolute right-2 top-2 rounded-xl bg-teal-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-teal-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
          }
        >
          Map
          {hero && (
            <svg
              aria-hidden="true"
              viewBox="0 0 20 20"
              className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-1"
              fill="currentColor"
            >
              <path d="M10.3 4.3a1 1 0 0 1 1.4 0l5 5a1 1 0 0 1 0 1.4l-5 5a1 1 0 0 1-1.4-1.4L13.6 11H4a1 1 0 1 1 0-2h9.6l-3.3-3.3a1 1 0 0 1 0-1.4Z" />
            </svg>
          )}
        </button>
      </form>
      <p className="mt-2 h-5 text-sm text-slate-600 dark:text-slate-400" aria-live="polite">
        {loading ? "Searching…" : script ? `Detected script: ${SCRIPT_LABEL[script] ?? script}` : ""}
      </p>

      {showList && (
        <ul
          id={`${listId}-list`}
          role="listbox"
          aria-label="Suggestions"
          className="absolute z-30 mt-1 max-h-96 w-full overflow-auto rounded-2xl border border-slate-200 bg-white p-1 shadow-xl dark:border-slate-700 dark:bg-slate-900"
        >
          {items.map((s, i) => (
            <li
              key={s.code}
              id={`${listId}-opt-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(s);
              }}
              onMouseEnter={() => setActive(i)}
              className={`flex cursor-pointer items-center justify-between gap-3 rounded-xl px-3 py-2.5 ${
                i === active ? "bg-teal-50 dark:bg-teal-950" : ""
              }`}
            >
              <span className="min-w-0">
                <span className="font-medium">{s.display}</span>
                {s.devanagari && (
                  <span className="ml-2 text-slate-600 dark:text-slate-400" lang="sa">
                    {s.devanagari}
                  </span>
                )}
                {s.matched_term !== s.display && (
                  <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
                    matched “{s.matched_term}” · {s.match}
                  </span>
                )}
              </span>
              <span className="shrink-0 font-mono text-xs text-slate-500 dark:text-slate-400">{s.code}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
