import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Dashboard from "../src/pages/Dashboard";
import Evaluation from "../src/pages/Evaluation";
import type { Dashboard as DashboardData, EvalRun } from "../src/services/api";
// A real run, written by `python backend/scripts/run_eval.py`.
import generated from "./fixtures/eval-run.json";

const run = {
  id: 3,
  created_at: "2026-10-30T12:00:00+00:00",
  created_by: "admin",
  ...generated,
} as unknown as EvalRun;

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function respond(routes: Record<string, unknown>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    const key = Object.keys(routes).find((k) => url.includes(k));
    if (!key) return Promise.resolve(new Response("{}", { status: 404 }));
    return Promise.resolve(new Response(JSON.stringify(routes[key]), { status: 200 }));
  });
}

afterEach(() => vi.restoreAllMocks());

describe("Evaluation page", () => {
  it("renders the stored run: tiles, tables, safety and failures", async () => {
    respond({ "/eval/runs/latest": run });
    wrap(<Evaluation />);
    expect(await screen.findByText(/Run #3/)).toBeInTheDocument();
    expect(screen.getByText(/SAMPLE — illustrative, not official/)).toBeInTheDocument();
    expect(screen.getByText("Hybrid top-1 (TEST)")).toBeInTheDocument();

    const inter = screen.getByRole("region", { name: "Intercomparison (TEST split)" });
    fireEvent.click(within(inter).getByRole("button", { name: "table" }));
    const table = within(inter).getByRole("table");
    for (const system of ["Exact", "Lexical", "Transliteration + Lexical", "Semantic", "Hybrid"])
      expect(within(table).getByText(system)).toBeInTheDocument();

    const safety = screen.getByRole("region", { name: /Safety checks/ });
    expect(within(safety).getByText("codes outside dataset")).toBeInTheDocument();
    expect(within(safety).queryByText("fail")).not.toBeInTheDocument();

    const fails = screen.getByRole("region", { name: "Failure drill-down" });
    fireEvent.change(within(fails).getByRole("combobox"), { target: { value: "Robustness: Typo (1 edit)" } });
    expect(within(fails).getByRole("table")).toBeInTheDocument();
  });

  it("explains when no run is stored", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "not_found", message: "no run", request_id: "x" } }), {
        status: 404,
      }),
    );
    wrap(<Evaluation />);
    expect(await screen.findByText(/No evaluation run is stored yet/)).toBeInTheDocument();
  });
});

const dash: DashboardData = {
  is_sample: true,
  dataset_releases: ["namaste@SAMPLE-1"],
  kpis: {
    namaste_codes: 53,
    tm2_codes: 56,
    biomedicine_codes: 20,
    mapped_approved: 42,
    pending_review: 4,
    no_match: 2,
    approved_biomedical_links: 12,
    pending_biomedical_links: 1,
  },
  band_distribution: { high: 50, medium: 1, low: 2 },
  band_note: "Band of the top suggestion for every NAMASTE concept.",
  queue: {
    by_status: { pending: 4 },
    median_hours_to_decision: null,
    throughput: [{ date: "2026-10-30", queued: 4, decided: 0 }],
  },
  mapping_gap: {
    label: "Official figures as cited in the SIH25026 pitch deck — not computed by NamaSetu",
    source: "Team Byte Strong pitch deck",
    bars: [
      { key: "a", label: "National Ayush (NAMASTE) codes", value: 7340 },
      { key: "b", label: "Formally mapped", value: 1941 },
      { key: "c", label: "ICD-11 TM2 categories", value: 529 },
    ],
  },
  latest_eval: { ...run, results: undefined },
};

describe("Dashboard page", () => {
  it("shows KPIs, labels the pitch-deck figures and creates exports", async () => {
    const spy = respond({
      "/dashboard": dash,
      "/export/jobs": [],
    });
    wrap(<Dashboard />);
    expect(await screen.findByText("NAMASTE codes ingested")).toBeInTheDocument();
    expect(screen.getByText("53")).toBeInTheDocument();
    expect(screen.getByText(/not computed by NamaSetu/)).toBeInTheDocument();

    const gap = screen.getByRole("region", { name: "The mapping gap" });
    fireEvent.click(within(gap).getByRole("button", { name: "table" }));
    expect(within(gap).getByText("7,340")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "pdf" } });
    fireEvent.click(screen.getByRole("button", { name: "Create export" }));
    await vi.waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, init]) =>
            String(u).endsWith("/export/jobs") &&
            init?.method === "POST" &&
            JSON.parse(String(init.body)).format === "pdf",
        ),
      ).toBe(true),
    );
  });
});
