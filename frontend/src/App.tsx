import { Suspense, lazy, useEffect, useState } from "react";
import { NavLink, Outlet, Route, Routes } from "react-router-dom";
import { ThemeToggle } from "./components/ThemeToggle";
import { DISCLAIMER } from "./constants";
import { ROLE_LABELS, can, startDemoSession } from "./services/auth";
import { useAuth } from "./services/useAuth";

// Route-level code splitting: each page is its own chunk.
const SearchPage = lazy(() => import("./pages/SearchPage"));
const MappingResult = lazy(() => import("./pages/MappingResult"));
const MedicationSearch = lazy(() => import("./pages/MedicationSearch"));
const SaltDetail = lazy(() => import("./pages/SaltDetail"));
const Crosswalk = lazy(() => import("./pages/Crosswalk"));
const ClinicalSummary = lazy(() => import("./pages/ClinicalSummary"));
const ConditionBuilder = lazy(() => import("./pages/ConditionBuilder")); // opened from a Search result
const NotFound = lazy(() => import("./pages/NotFound"));

const navItems: { to: string; label: string; end?: boolean; permission: string }[] = [
  { to: "/", label: "Search", end: true, permission: "read" },
  { to: "/summary", label: "Clinical summary", permission: "patient" },
  { to: "/medicines", label: "Medicines", permission: "read" },
  { to: "/crosswalk", label: "Mapping", permission: "read" },
];

const pulse = "h-40 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-900";

/** No sign-in screen: pages open with the demo session, started here when the tab has none. */
function RequireSession({ permission = "read" }: { permission?: string }) {
  const user = useAuth();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!user && !failed) void startDemoSession().then((ok) => setFailed(!ok));
  }, [user, failed]);
  if (!user) {
    return failed ? (
      <div
        role="alert"
        className="rounded-xl bg-saffron-50 p-4 text-saffron-900 dark:bg-saffron-900/30 dark:text-saffron-100"
      >
        <p>The demo session could not start. Check that the API is running, then try again.</p>
        <button type="button" onClick={() => setFailed(false)} className="mt-2 font-semibold underline">
          Try again
        </button>
      </div>
    ) : (
      <div className={pulse} role="status" aria-label="Starting the demo session" />
    );
  }
  if (!can(user, permission)) {
    return (
      <p
        role="alert"
        className="rounded-xl bg-saffron-50 p-4 text-saffron-900 dark:bg-saffron-900/30 dark:text-saffron-100"
      >
        Your role ({ROLE_LABELS[user.role] ?? user.role}) cannot open this page.
      </p>
    );
  }
  return <Outlet />;
}

function Layout() {
  const user = useAuth();
  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-teal-800"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <NavLink to="/" className="flex items-center gap-2" aria-label="NamaSetu home">
            <img src="/favicon.svg" alt="" className="h-7 w-7" />
            <span className="text-lg font-bold tracking-tight">
              Nama<span className="text-teal-700 dark:text-teal-400">Setu</span>
            </span>
          </NavLink>
          <nav aria-label="Primary" className="flex flex-wrap gap-1">
            {navItems
              .filter((item) => !user || can(user, item.permission))
              .map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `rounded-lg px-3 py-1.5 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal-600 ${
                      isActive
                        ? "bg-teal-50 text-teal-800 dark:bg-teal-950 dark:text-teal-200"
                        : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <ThemeToggle />
            {/* The Care portal (public/care-portal.html): Hospital, Doctor and Patient sign-in. */}
            <a
              href="/care-portal.html"
              className="rounded-lg bg-teal-700 px-4 py-1.5 text-sm font-semibold text-white hover:bg-teal-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-600"
            >
              Login
            </a>
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-8">
        <Suspense fallback={<div className={pulse} />}>
          <Outlet />
        </Suspense>
      </main>

      <footer className="border-t border-slate-200 py-4 text-center text-xs text-slate-600 dark:border-slate-800 dark:text-slate-400">
        <p>{DISCLAIMER}</p>
        <p className="mt-1">Team Byte Strong · Chandigarh University · SIH25026</p>
      </footer>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route element={<RequireSession />}>
          <Route index element={<SearchPage />} />
          <Route path="map" element={<MappingResult />} />
          <Route path="medicines" element={<MedicationSearch />} />
          <Route path="salts/:id" element={<SaltDetail />} />
          <Route path="crosswalk" element={<Crosswalk />} />
        </Route>
        <Route element={<RequireSession permission="patient" />}>
          <Route path="summary" element={<ClinicalSummary />} />
        </Route>
        <Route element={<RequireSession permission="build_condition" />}>
          <Route path="condition" element={<ConditionBuilder />} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
