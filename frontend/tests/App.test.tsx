import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { tokens } from "../src/services/auth";
import type { Health } from "../src/services/api";

const health: Health = {
  status: "ok",
  app: "NamaSetu",
  version: "0.1.0",
  environment: "test",
  database: { ok: true, mode: "sqlite-local", detail: "ok" },
  redis: { ok: true, mode: "eager-local", detail: "" },
  index: { loaded: false, detail: "not built yet" },
  modes: {
    DB_MODE: "sqlite-local",
    TASK_MODE: "eager-local",
    SEMANTIC_MODE: "off",
    ICD11_MODE: "mock",
    ABHA_MODE: "mock",
    DATA_MODE: "sample",
  },
  dataset_versions: { icd11: "SAMPLE-1", namaste: "SAMPLE-1" },
  disclaimer: "",
};

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const PERMISSIONS = {
  admin: ["admin", "build_condition", "patient", "propose", "read", "review"],
  clinician: ["build_condition", "patient", "propose", "read", "review"],
  viewer: ["read"],
};

function signIn(role: keyof typeof PERMISSIONS = "admin") {
  tokens.set({
    access_token: "test-token",
    refresh_token: "test-refresh",
    token_type: "bearer",
    expires_in: 900,
    user: {
      username: role,
      display_name: `Demo ${role}`,
      role,
      permissions: PERMISSIONS[role],
      auth_method: "password",
    },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  tokens.clear();
});

describe("App shell", () => {
  it("opens without a sign-in page by starting the demo clinician session", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      urls.push(url);
      if (url.endsWith("/auth/login")) {
        expect(JSON.parse(String(init?.body))).toEqual({
          username: "clinician",
          password: "Clinician@NamaSetu1",
        });
        const user = { username: "clinician", display_name: "Dr. Demo Clinician", role: "clinician" };
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "a1",
              refresh_token: "r1",
              token_type: "bearer",
              expires_in: 900,
              user: { ...user, permissions: PERMISSIONS.clinician, auth_method: "password" },
            }),
            { status: 200 },
          ),
        );
      }
      return Promise.resolve(new Response(JSON.stringify(health), { status: 200 }));
    });
    renderAt("/");
    // First test in the file: the lazy landing chunk (framer-motion) is transformed cold, so allow longer.
    expect(await screen.findByRole("heading", { level: 1 }, { timeout: 5000 })).toHaveTextContent(
      "three code systems",
    );
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    expect(urls.some((u) => u.endsWith("/auth/login"))).toBe(true);
  });

  it("shows only Search, Medicines, Mapping and a Login link to the Care portal", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(health), { status: 200 }));
    signIn("clinician");
    renderAt("/");
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    expect([...nav.querySelectorAll("a")].map((a) => a.textContent)).toEqual([
      "Search",
      "Medicines",
      "Mapping",
    ]);
    expect(screen.getByRole("link", { name: "Mapping" })).toHaveAttribute("href", "/crosswalk");
    expect(screen.getByRole("link", { name: "Login" })).toHaveAttribute("href", "/care-portal.html");
    for (const gone of [
      "Consultation",
      "Evaluation",
      "Dashboard",
      "Review",
      "Audit",
      "Admin",
      "About",
      "Sign out",
    ])
      expect(screen.queryByRole("link", { name: gone })).not.toBeInTheDocument();
  });

  it("no longer routes Consultation, Evaluation, Dashboard or Clinical summary", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(health), { status: 200 }));
    signIn("clinician");
    for (const path of ["/consultation", "/evaluation", "/dashboard", "/summary"]) {
      const { unmount } = renderAt(path);
      expect(await screen.findByText(/not found/i)).toBeInTheDocument();
      unmount();
    }
  });

  it("shows no MOCK / SAMPLE DATA badges and no system-status panel", async () => {
    signIn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(health), { status: 200 }));
    renderAt("/");
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("three code systems");
    expect(screen.queryByText("MOCK")).not.toBeInTheDocument();
    expect(screen.queryByText("SAMPLE DATA")).not.toBeInTheDocument();
    expect(screen.queryByText("System status")).not.toBeInTheDocument();
  });

  it("opens in light mode by default", async () => {
    localStorage.clear();
    document.documentElement.classList.add("dark");
    signIn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(health), { status: 200 }));
    renderAt("/");
    await screen.findByRole("heading", { level: 1 });
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(screen.getByRole("button", { name: "Switch to dark mode" })).toBeInTheDocument();
  });

  it("landing page: one H1, section headings, a Try chip fills the search box, Siddha is disabled", async () => {
    signIn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), { status: 200 }),
    );
    renderAt("/");
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(
      screen.getByRole("heading", { level: 2, name: "How NamaSetu maps a diagnosis" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Built on trusted standards" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Fill the search with aamvaat/ }));
    expect(screen.getByRole("combobox")).toHaveValue("aamvaat");
    expect(screen.getByRole("combobox")).toHaveFocus();
    expect(screen.getByRole("tab", { name: /Siddha/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("tab", { name: "Ayurveda" })).toHaveAttribute("aria-selected", "true");
  });

  it("always renders the clinical disclaimer", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(health), { status: 200 }));
    renderAt("/no-such-page");
    expect(screen.getAllByText(/Not a certified medical device/).length).toBeGreaterThan(0);
  });
});
