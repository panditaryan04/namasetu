import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReviewQueue from "../src/pages/ReviewQueue";
import { tokens } from "../src/services/auth";

const summary = {
  id: 7,
  status: "pending",
  kind: "mapping",
  source_code: "SAMPLE-AYU-0040",
  source_display: "Vataja Jvara",
  discipline: "ayurveda",
  band: "medium",
  top_confidence: 0.6,
  top_target: "SAMPLE-TM2-0201",
  top_target_display: "Fever disorder (TM2)",
  reason: "medium-band",
  created_by: "coder",
  created_at: "2026-10-30T10:00:00Z",
  decided_by: null,
  decided_at: null,
};
const candidate = {
  query: { text: "Vataja Jvara", normalised: "vataja jvara", script: "latin" },
  namaste: { system: "x", code: "SAMPLE-AYU-0040", display: "Vataja Jvara" },
  tm2: { system: "y", code: "SAMPLE-TM2-0201", display: "Fever disorder (TM2)", entity_type: "disorder" },
  biomedicine: {
    system: null,
    code: null,
    display: null,
    relationship: "relatedto",
    source: "none",
    status: "none",
    message: "No biomedical link (clinician may add)",
  },
  confidence: 0.6,
  band: "medium",
  relationship: "wider",
  scores: { exact: 0, lexical: 0.9, ngram: 0.8, transliteration: 0, semantic: 0.2, hierarchy: 1, final: 0.6 },
  evidence: {
    matched_tokens: [],
    matched_root_terms: [],
    matched_terms: [],
    shared_concepts: [],
    hierarchy_path: ["Fever disorder (TM2)"],
    entity_type_check: "disorder→disorder ✓",
    transliteration_hit: false,
    stages_hit: ["lexical"],
    baseline: null,
  },
  reasoning: "r",
  dataset_releases: [],
  model_version: "ranker@1.0.0",
};
const detail = {
  ...summary,
  query_text: null,
  proposed_target: null,
  candidates: [candidate],
  dataset_releases: [],
  ranker_version: "ranker@1.0.0",
  decision: null,
  comment: null,
  mapping_id: null,
  current_mapping: null,
};

afterEach(() => {
  vi.restoreAllMocks();
  tokens.clear();
});

describe("ReviewQueue", () => {
  it("lists items and approves with the keyboard", async () => {
    tokens.set({
      access_token: "t",
      refresh_token: "r",
      token_type: "bearer",
      expires_in: 900,
      user: {
        username: "clinician",
        display_name: "Dr",
        role: "clinician",
        permissions: ["read", "propose", "review", "build_condition"],
        auth_method: "password",
      },
    });
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      calls.push({ url, init });
      let body: unknown = detail;
      if (url.includes("/review/queue")) body = { items: [summary], total: 1, limit: 200, offset: 0 };
      if (url.includes("/admin/thresholds"))
        body = { high: 0.85, low: 0.5, source: "calibrated", calibrated: { high: 0.85, low: 0.5 } };
      if (url.endsWith("/decision")) body = { ...detail, status: "approved", decided_by: "clinician" };
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <ReviewQueue />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { level: 2, name: /Vataja Jvara/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Fever disorder/ })).toBeChecked();
    fireEvent.keyDown(window, { key: "a" });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/review/7/decision"))).toBe(true));
    const post = calls.find((c) => c.url.endsWith("/decision"))!;
    expect(JSON.parse(String(post.init?.body))).toMatchObject({
      action: "approve",
      tm2_code: "SAMPLE-TM2-0201",
    });
    expect((post.init?.headers as Record<string, string>).Authorization).toBe("Bearer t");
  });
});
