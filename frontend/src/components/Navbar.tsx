import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { can } from "../services/auth";
import { useAuth } from "../services/useAuth";
import { ThemeToggle } from "./ThemeToggle";

export interface NavItem {
  to: string;
  label: string;
  end?: boolean;
  permission: string;
}

const focusRing =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-med-primary-dark";

/** Sticky glass header: wordmark, primary links (a hamburger panel on mobile) and the Care portal Login. */
export function Navbar({ items }: { items: NavItem[] }) {
  const user = useAuth();
  const { pathname } = useLocation();
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Close the mobile menu after navigating.
  useEffect(() => setOpen(false), [pathname]);

  return (
    <header
      className={`sticky top-0 z-40 border-b bg-white/70 backdrop-blur-xl transition-colors duration-200 dark:bg-slate-950/70 ${
        scrolled || open ? "border-med-border" : "border-transparent"
      }`}
    >
      <div className="relative mx-auto flex max-w-6xl items-center gap-3 px-4 py-3 sm:gap-6 sm:px-6 sm:py-4">
        <NavLink
          to="/"
          className={`group flex items-center gap-2.5 rounded-xl ${focusRing}`}
          aria-label="NamaSetu home"
        >
          <img
            src="/favicon.svg"
            alt=""
            className="h-9 w-9 transition-transform duration-200 group-hover:scale-110 sm:h-11 sm:w-11"
          />
          <span className="font-display text-xl font-extrabold tracking-tight text-med-heading sm:text-2xl">
            Nama<span className="bg-brand-gradient-text bg-clip-text text-transparent">Setu</span>
          </span>
        </NavLink>

        <nav
          id="primary-nav"
          aria-label="Primary"
          className={`${
            open ? "flex" : "hidden"
          } absolute inset-x-0 top-full flex-col gap-2 border-b border-med-border bg-white p-4 shadow-glass-sm dark:bg-slate-950 md:static md:mx-auto md:flex md:flex-row md:border-0 md:bg-transparent md:p-0 md:shadow-none md:dark:bg-transparent`}
        >
          {items
            .filter((item) => !user || can(user, item.permission))
            .map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-full border px-5 py-2.5 text-base font-semibold transition duration-200 hover:-translate-y-0.5 hover:border-sky-300 hover:shadow-glow dark:hover:border-sky-700 ${focusRing} ${
                    isActive
                      ? "border-sky-200 bg-sky-100 text-med-primary-dark dark:border-sky-900 dark:bg-sky-950 dark:text-sky-200"
                      : "border-transparent text-med-body hover:bg-white hover:text-med-primary-dark dark:hover:bg-slate-900 dark:hover:text-sky-200"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
        </nav>

        <div className="ml-auto flex items-center gap-2 sm:gap-3 md:ml-0">
          <ThemeToggle />
          {/* The Care portal (public/care-portal.html): Hospital, Doctor and Patient sign-in. */}
          <a
            href="/care-portal.html"
            className={`rounded-xl border-2 border-transparent bg-brand-gradient-strong px-4 py-2 text-sm font-semibold text-white shadow-glass-sm transition duration-200 hover:-translate-y-0.5 hover:border-sky-300 hover:shadow-lift sm:px-6 sm:py-2.5 sm:text-base ${focusRing}`}
          >
            Login
          </a>
          <button
            type="button"
            className={`rounded-xl border border-transparent p-2 text-med-heading transition duration-200 hover:border-sky-300 hover:bg-white hover:shadow-glow dark:hover:bg-slate-800 md:hidden ${focusRing}`}
            aria-expanded={open}
            aria-controls="primary-nav"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((o) => !o)}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              className="h-6 w-6"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              {open ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
            </svg>
          </button>
        </div>
      </div>
    </header>
  );
}
