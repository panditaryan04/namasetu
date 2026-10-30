import { Suspense, lazy, useEffect, useState } from "react";
import { Outlet, Route, Routes } from "react-router-dom";
import { Footer } from "./components/Footer";
import { Navbar, type NavItem } from "./components/Navbar";
import { ROLE_LABELS, can, startDemoSession } from "./services/auth";
import { useAuth } from "./services/useAuth";

// Route-level code splitting: each page is its own chunk.
const SearchPage = lazy(() => import("./pages/SearchPage"));
const MappingResult = lazy(() => import("./pages/MappingResult"));
const MedicationSearch = lazy(() => import("./pages/MedicationSearch"));
const SaltDetail = lazy(() => import("./pages/SaltDetail"));
const Crosswalk = lazy(() => import("./pages/Crosswalk"));
const ConditionBuilder = lazy(() => import("./pages/ConditionBuilder")); // opened from a Search result
const NotFound = lazy(() => import("./pages/NotFound"));

const navItems: NavItem[] = [
  { to: "/", label: "Search", end: true, permission: "read" },
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
  return (
    // No background here: the page colour sits on <body> so the landing page's fixed 3D canvas shows through.
    <div className="flex min-h-screen flex-col text-slate-900 dark:text-slate-100">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-med-primary-dark"
      >
        Skip to content
      </a>
      <Navbar items={navItems} />

      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-8 sm:px-6">
        <Suspense fallback={<div className={pulse} />}>
          <Outlet />
        </Suspense>
      </main>

      <Footer />
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
        <Route element={<RequireSession permission="build_condition" />}>
          <Route path="condition" element={<ConditionBuilder />} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
