import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ROLE_LABELS, login, loginAbha, type Role } from "../services/auth";

// Sandbox demo accounts (documented in README / .env.example; production requires env-set passwords).
const DEMO: { role: Role; password: string; hint: string }[] = [
  {
    role: "clinician",
    password: "Clinician@NamaSetu1",
    hint: "reviews and approves mappings, records Conditions",
  },
  {
    role: "coder",
    password: "Coder@NamaSetu1",
    hint: "finds codes for a diagnosis and sends mappings to a doctor for approval",
  },
  { role: "admin", password: "Admin@NamaSetu1", hint: "everything, incl. data import and thresholds" },
  { role: "viewer", password: "Viewer@NamaSetu1", hint: "can look, cannot change anything" },
];

const field =
  "mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/20 dark:border-slate-700 dark:bg-slate-950";

export default function Login() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") ?? "/";
  const [mode, setMode] = useState<"password" | "abha">("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [abha, setAbha] = useState("dr.demo@sbx");
  const [otp, setOtp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      navigate(next.startsWith("/") ? next : "/", { replace: true });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto grid max-w-4xl gap-8 md:grid-cols-2">
      <section aria-labelledby="login-heading">
        <h1 id="login-heading" className="text-2xl font-bold tracking-tight">
          Sign in
        </h1>
        <div
          role="tablist"
          aria-label="Sign-in method"
          className="mt-4 inline-flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800"
        >
          {(["password", "abha"] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={mode === m}
              onClick={() => setMode(m)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${mode === m ? "bg-white shadow dark:bg-slate-950" : "text-slate-600 dark:text-slate-300"}`}
            >
              {m === "password" ? "Username" : "ABHA (mock)"}
            </button>
          ))}
        </div>

        {mode === "password" ? (
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => (e.preventDefault(), submit(() => login(username, password)))}
          >
            <label className="block text-sm font-medium">
              Username
              <input
                className={field}
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label className="block text-sm font-medium">
              Password
              <input
                className={field}
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <button
              disabled={busy}
              className="w-full rounded-xl bg-brand-700 px-4 py-2.5 font-semibold text-white hover:bg-brand-800 disabled:opacity-60"
            >
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        ) : (
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => (e.preventDefault(), submit(() => loginAbha(abha, otp)))}
          >
            <label className="block text-sm font-medium">
              ABHA address
              <input className={field} required value={abha} onChange={(e) => setAbha(e.target.value)} />
            </label>
            <label className="block text-sm font-medium">
              One-time code <span className="font-normal text-slate-500">(sandbox mock: 123456)</span>
              <input
                className={field}
                inputMode="numeric"
                required
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
              />
            </label>
            <button
              disabled={busy}
              className="w-full rounded-xl bg-brand-700 px-4 py-2.5 font-semibold text-white hover:bg-brand-800 disabled:opacity-60"
            >
              {busy ? "Verifying…" : "Verify ABHA"}
            </button>
          </form>
        )}
        {error && (
          <p
            role="alert"
            className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800 dark:bg-red-950 dark:text-red-200"
          >
            {error}
          </p>
        )}
      </section>

      <aside
        aria-labelledby="demo-heading"
        className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900"
      >
        <h2 id="demo-heading" className="font-semibold">
          Sandbox demo accounts
        </h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">One per role. Click to fill in.</p>
        <ul className="mt-3 space-y-2">
          {DEMO.map((d) => (
            <li key={d.role}>
              <button
                type="button"
                onClick={() => {
                  setMode("password");
                  setUsername(d.role);
                  setPassword(d.password);
                }}
                className="w-full rounded-xl px-3 py-2 text-left ring-1 ring-inset ring-slate-200 hover:bg-brand-50 dark:ring-slate-700 dark:hover:bg-brand-950"
              >
                <span className="font-medium">{ROLE_LABELS[d.role]}</span>
                <span className="block text-xs text-slate-500">
                  username <code>{d.role}</code> · {d.hint}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
    </div>
  );
}
