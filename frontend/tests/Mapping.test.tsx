import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Autocomplete } from "../src/components/Autocomplete";
import MappingResult from "../src/pages/MappingResult";
import type { Explanation, MapResult } from "../src/services/api";

const candidate: Explanation = {
  query: { text: "आमवात", normalised: "amavata", script: "devanagari" },
  namaste: { system: "x/namaste", code: "SAMPLE-AYU-0001", display: "Amavata" },
  tm2: {
    system: "x/icd11-mms-sample",
    code: "SAMPLE-TM2-0111",
    display: "Ama-vata joint disorder",
    entity_type: "disorder",
  },
  biomedicine: {
    system: "x/icd11-mms-sample",
    code: "SAMPLE-BIO-0001",
    display: "Rheumatoid arthritis",
    relationship: "relatedto",
    source: "who-index",
    status: "approved",
    message: null,
  },
  confidence: 0.9985,
  band: "high",
  relationship: "equivalent",
  scores: {
    exact: 1,
    lexical: 1,
    ngram: 0.9,
    transliteration: 1,
    semantic: 0.4,
    hierarchy: 0.2,
    final: 0.9985,
  },
  evidence: {
    matched_tokens: ["amavata"],
    matched_root_terms: ["ama", "vata"],
    matched_terms: [{ source: "आमवात", target: "Amavata", via: "exact+transliteration" }],
    shared_concepts: ["ama", "vata", "joint"],
    hierarchy_path: [
      "Traditional medicine conditions – Module 2",
      "Ayurveda disorders (TM2)",
      "Ama-vata joint disorder",
    ],
    entity_type_check: "disorder→disorder ✓",
    transliteration_hit: true,
    stages_hit: ["exact", "transliteration", "lexical", "ngram", "semantic"],
    baseline: null,
  },
  reasoning: "Matched 'आमवात' to 'Amavata' via exact+transliteration; shared root terms 'ama', 'vata'.",
  dataset_releases: ["namaste@SAMPLE-1", "icd11@SAMPLE-1"],
  model_version: "ranker@1.0.0",
};

const result: MapResult = {
  query: candidate.query,
  resolved: {
    code: "SAMPLE-AYU-0001",
    display: "Amavata",
    matched_term: "आमवात",
    method: "exact",
    score: 1,
    iast: "Āmavāta",
    devanagari: "आमवात",
  },
  alternatives: [],
  status: "suggested",
  message: "High-confidence suggestion; confirm with one click",
  candidates: [candidate],
  suppressed_low: 0,
  thresholds: { high: 0.5, low: 0.45, source: "calibrated" },
  semantic_method: "weighted-lexical-overlap",
  dataset_releases: candidate.dataset_releases,
  model_version: "ranker@1.0.0",
};

function wrap(ui: React.ReactNode, path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        {ui}
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function LocationProbe() {
  const loc = useLocation();
  return <output data-testid="location">{loc.pathname + loc.search}</output>;
}

/** Fresh Response per call (a body can only be read once), routed by URL. */
function mockFetch(routes: Record<string, unknown>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    const key = Object.keys(routes).find((k) => url.includes(k));
    const body = key ? routes[key] : { query: { text: "", normalised: "", script: "latin" }, items: [] };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
}

afterEach(() => vi.restoreAllMocks());

describe("MappingResult", () => {
  it("renders the tri-coded candidate with evidence", async () => {
    const fetchSpy = mockFetch({ "/map": result });
    wrap(
      <Routes>
        <Route path="/map" element={<MappingResult />} />
      </Routes>,
      "/map?q=%E0%A4%86%E0%A4%AE%E0%A4%B5%E0%A4%BE%E0%A4%A4",
    );
    expect(await screen.findByRole("article", { name: /Ama-vata joint disorder/ })).toBeInTheDocument();
    const [url, init] = fetchSpy.mock.calls.find(([u]) => String(u).endsWith("/map"))!;
    expect(String(url)).toContain("/map");
    expect(JSON.parse(String(init?.body))).toEqual({ text: "आमवात" });

    expect(screen.getByText("High · suggested")).toBeInTheDocument();
    expect(screen.getByText("= equivalent")).toBeInTheDocument();
    expect(screen.getByText("Rheumatoid arthritis")).toBeInTheDocument();
    expect(screen.getByText("Biomedical code available for claims workflow")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Confidence" })).toHaveAttribute("aria-valuenow", "100");
    // First card opens its evidence panel by default.
    expect(screen.getByText(/template from evidence/)).toBeInTheDocument();
    expect(screen.getByText("exact+transliteration")).toBeInTheDocument();
    expect(screen.getByText(/script Devanagari/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Hide evidence" }));
    expect(screen.queryByText(/template from evidence/)).not.toBeInTheDocument();
  });

  it("shows the no-match state with alternatives", async () => {
    const noMatch: MapResult = {
      ...result,
      resolved: null,
      status: "no_match",
      message: "No safe automated match found",
      candidates: [],
      alternatives: [
        {
          match: "root",
          system: "namaste",
          code: "SAMPLE-AYU-0009",
          display: "Amlapitta",
          matched_term: "Amlapitta",
          term_kind: "primary",
          devanagari: "अम्लपित्त",
          entity_type: "disorder",
          score: 0.85,
        },
      ],
    };
    mockFetch({ "/map": noMatch });
    wrap(
      <Routes>
        <Route path="/map" element={<MappingResult />} />
      </Routes>,
      "/map?q=pitta",
    );
    expect(await screen.findByText("No safe automated match found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Amlapitta/ })).toHaveAttribute(
      "href",
      "/map?code=SAMPLE-AYU-0009&q=Amlapitta",
    );
  });
});

describe("Autocomplete", () => {
  it("debounces, supports arrow keys and navigates on Enter", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          query: { text: "amla", normalised: "amla", script: "latin" },
          items: [
            {
              ...result.alternatives[0],
              match: "prefix",
              code: "SAMPLE-AYU-0009",
              display: "Amlapitta",
              matched_term: "Amlapitta",
              term_kind: "primary",
              devanagari: "अम्लपित्त",
              entity_type: "disorder",
              score: 0.9,
              system: "namaste",
            },
          ],
        }),
        { status: 200 },
      ),
    );
    wrap(
      <>
        <Autocomplete />
        <LocationProbe />
      </>,
      "/",
    );
    const box = screen.getByRole("combobox");
    fireEvent.change(box, { target: { value: "amla" } });
    expect(await screen.findByRole("option", { name: /Amlapitta/ })).toBeInTheDocument();
    expect(screen.getByText("Detected script: English / Hinglish")).toBeInTheDocument();
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(box).toHaveAttribute("aria-activedescendant");
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/map?code=SAMPLE-AYU-0009&q=amla"),
    );
  });
});
