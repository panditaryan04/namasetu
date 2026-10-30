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
      <div className="relative mx-auto flex max-w-6xl items-center gap-4 px-6 py-3">
        <NavLink
          to="/"
          className={`flex items-center gap-2 rounded-lg ${focusRing}`}
          aria-label="NamaSetu home"
        >
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <span className="font-display text-xl font-extrabold tracking-tight text-med-heading">
            Nama<span className="bg-brand-gradient-text bg-clip-text text-transparent">Setu</span>
          </span>
        </NavLink>

        <nav
          id="primary-nav"
          aria-label="Primary"
          className={`${
            open ? "flex" : "hidden"
          } absolute inset-x-0 top-full flex-col gap-1 border-b border-med-border bg-white p-4 shadow-glass-sm dark:bg-slate-950 md:static md:mx-auto md:flex md:flex-row md:border-0 md:bg-transparent md:p-0 md:shadow-none md:dark:bg-transparent`}
        >
          {items
            .filter((item) => !user || can(user, item.permission))
            .map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-full px-4 py-2 text-sm font-semibold transition-colors duration-200 ${focusRing} ${
                    isActive
                      ? "bg-sky-100 text-med-primary-dark dark:bg-sky-950 dark:text-sky-200"
                      : "text-med-body hover:bg-sky-50 hover:text-med-heading dark:hover:bg-slate-800"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
        </nav>

        <div className="ml-auto flex items-center gap-2 md:ml-0">
          <ThemeToggle />
          {/* The Care portal (public/care-portal.html): Hospital, Doctor and Patient sign-in. */}
          <a
            href="/care-portal.html"
            className={`rounded-xl bg-brand-gradient-strong px-5 py-2 text-sm font-semibold text-white shadow-glass-sm transition duration-200 hover:-translate-y-0.5 hover:shadow-lift ${focusRing}`}
          >
            Login
          </a>
          <button
            type="button"
            className={`rounded-lg p-2 text-med-heading hover:bg-sky-50 dark:hover:bg-slate-800 md:hidden ${focusRing}`}
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
