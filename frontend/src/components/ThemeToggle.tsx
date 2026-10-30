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
      className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal-600 dark:text-slate-300 dark:hover:bg-slate-800"
    >
      <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5" fill="currentColor">
        {theme === "dark" ? (
          <path d="M10 3a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0V4a1 1 0 0 1 1-1Zm0 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm6-5a1 1 0 1 1 0 2h-1a1 1 0 1 1 0-2h1ZM5 10a1 1 0 0 1-1 1H3a1 1 0 1 1 0-2h1a1 1 0 0 1 1 1Zm5 5a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0v-1a1 1 0 0 1 1-1Z" />
        ) : (
          <path d="M17.3 13.4A7.5 7.5 0 0 1 6.6 2.7a7.5 7.5 0 1 0 10.7 10.7Z" />
        )}
      </svg>
    </button>
  );
}
