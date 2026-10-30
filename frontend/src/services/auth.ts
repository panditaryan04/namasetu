// Token storage. The short-lived access token lives in memory; the refresh token and user profile live in
// sessionStorage (cleared when the tab closes). A production deployment should prefer an httpOnly cookie.

export type Role = "admin" | "clinician" | "coder" | "viewer";

// Plain-language names shown in the UI; the role ids stay as they are in the API and the audit ledger.
export const ROLE_LABELS: Record<Role, string> = {
  admin: "Administrator",
  clinician: "Clinician (doctor)",
  coder: "Medical records officer",
  viewer: "Viewer (read-only)",
};

export interface UserInfo {
  username: string;
  display_name: string;
  role: Role;
  permissions: string[];
  auth_method: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: UserInfo;
}

const API = import.meta.env.VITE_API_BASE ?? "/api";
const REFRESH_KEY = "namasetu.refresh";
const USER_KEY = "namasetu.user";

let accessToken: string | null = null;
// One refresh at a time: a page load fires several requests that all get 401 together.
let refreshing: Promise<boolean> | null = null;
const listeners = new Set<(user: UserInfo | null) => void>();

function emit(user: UserInfo | null) {
  listeners.forEach((l) => l(user));
}

export const tokens = {
  access: () => accessToken,
  refresh: () => sessionStorage.getItem(REFRESH_KEY),
  user: (): UserInfo | null => {
    const raw = sessionStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as UserInfo) : null;
  },
  set(t: TokenResponse) {
    accessToken = t.access_token;
    sessionStorage.setItem(REFRESH_KEY, t.refresh_token);
    sessionStorage.setItem(USER_KEY, JSON.stringify(t.user));
    emit(t.user);
  },
  clear() {
    accessToken = null;
    sessionStorage.removeItem(REFRESH_KEY);
    sessionStorage.removeItem(USER_KEY);
    emit(null);
  },
  tryRefresh(): Promise<boolean> {
    const refresh = sessionStorage.getItem(REFRESH_KEY);
    if (!refresh) return Promise.resolve(false);
    refreshing ??= (async () => {
      try {
        const resp = await fetch(`${API}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refresh }),
        });
        if (!resp.ok) return false;
        tokens.set((await resp.json()) as TokenResponse);
        return true;
      } finally {
        refreshing = null;
      }
    })();
    return refreshing;
  },
  subscribe(listener: (user: UserInfo | null) => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

async function authPost(path: string, body: unknown): Promise<TokenResponse> {
  const resp = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = (await resp.json()) as TokenResponse & { error?: { message: string } };
  if (!resp.ok) throw new Error(json.error?.message ?? `Login failed (HTTP ${resp.status})`);
  tokens.set(json);
  return json;
}

export const login = (username: string, password: string) => authPost("/auth/login", { username, password });
export const loginAbha = (abha_address: string, otp: string) =>
  authPost("/auth/abha/token", { abha_address, otp });

// This build has no sign-in screen: it opens a session with the sandbox clinician account. The API only creates
// demo accounts outside production (or when DEMO_<ROLE>_PASSWORD is set), so a production API refuses this.
const DEMO_USERNAME = import.meta.env.VITE_DEMO_USERNAME ?? "clinician";
const DEMO_PASSWORD = import.meta.env.VITE_DEMO_PASSWORD ?? "Clinician@NamaSetu1";
let starting: Promise<boolean> | null = null;

/** Resume the tab's session if its refresh token is still valid, otherwise sign in as the demo user. */
export function startDemoSession(): Promise<boolean> {
  starting ??= (async () => {
    try {
      if (tokens.refresh() && (await tokens.tryRefresh())) return true;
      await login(DEMO_USERNAME, DEMO_PASSWORD);
      return true;
    } catch {
      return false;
    } finally {
      starting = null;
    }
  })();
  return starting;
}

export const can = (user: UserInfo | null, permission: string) =>
  Boolean(user?.permissions.includes(permission));
