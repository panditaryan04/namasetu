import { useEffect, useState } from "react";

type Theme = "light" | "dark";

// Light by default (not the OS preference); dark only when the user switches to it.
const KEY = "namasetu.theme";

function initialTheme(): Theme {
  return localStorage.getItem(KEY) === "dark" ? "dark" : "light";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem(KEY, theme);
  }, [theme]);

  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      aria-label={`Switch to ${next} mode`}
      className="rounded-xl border border-transparent p-2 text-slate-600 transition duration-200 hover:-translate-y-0.5 hover:border-sky-300 hover:bg-white hover:shadow-glow focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600 dark:text-slate-300 dark:hover:border-sky-700 dark:hover:bg-slate-800 sm:p-2.5"
    >
      <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5 sm:h-6 sm:w-6" fill="currentColor">
        {theme === "dark" ? (
          <path d="M10 3a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0V4a1 1 0 0 1 1-1Zm0 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm6-5a1 1 0 1 1 0 2h-1a1 1 0 1 1 0-2h1ZM5 10a1 1 0 0 1-1 1H3a1 1 0 1 1 0-2h1a1 1 0 0 1 1 1Zm5 5a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0v-1a1 1 0 0 1 1-1Z" />
        ) : (
          <path d="M7.46 2a.75.75 0 0 1 .26.77 7 7 0 0 0 9.96 7.97.75.75 0 0 1 1.06.85A8.5 8.5 0 1 1 6.65 1.92a.75.75 0 0 1 .81.08Z" />
        )}
      </svg>
    </button>
  );
}
