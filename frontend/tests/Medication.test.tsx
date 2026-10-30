import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MedicationCard } from "../src/components/MedicationCard";
import MedicationSearch from "../src/pages/MedicationSearch";
import type { MedicationView } from "../src/services/api";

const combi: MedicationView = {
  code: "SAMPLE-MED-B003",
  kind: "biomedical",
  name: "Ibuprofen + Paracetamol tablet",
  name_iast: null,
  name_devanagari: null,
  discipline: null,
  dosage_form: "tablet",
  route: "oral",
  therapeutic_class: "NSAID combination",
  schedule: "H",
  nlem: false,
  composition: [
    {
      ingredient_id: 1,
      printed_name: "Ibuprofen",
      canonical_name: "Ibuprofen",
      kind: "salt",
      botanical_name: null,
      part_used: null,
      proportion: null,
      strength: "400 mg",
      metal_mineral: false,
    },
    {
      ingredient_id: 2,
      printed_name: "Paracetamol",
      canonical_name: "Paracetamol",
      kind: "salt",
      botanical_name: null,
      part_used: null,
      proportion: null,
      strength: "325 mg",
      metal_mineral: false,
    },
  ],
  badges: [{ code: "schedule-h", label: "Schedule H — prescription-only", severity: "warning" }],
  brands: ["Combiflam"],
  source: {
    title: "CDSCO approved drug list (SAMPLE transcription)",
    edition: "SAMPLE",
    page: "verify against source",
  },
  caution_note: null,
  is_sample: true,
  sample_label: "SAMPLE — illustrative, not official",
  active: true,
  disclaimer: "Reference only — not a prescription. Consult a qualified clinician.",
};
const kamadugha: MedicationView = {
  ...combi,
  code: "SAMPLE-MED-A006",
  kind: "ayush_formulation",
  name: "Kamadugha Rasa",
  name_devanagari: "कामदुघा रस",
  dosage_form: "vati",
  schedule: null,
  brands: [],
  composition: [
    {
      ingredient_id: 9,
      printed_name: "Pravala bhasma",
      canonical_name: "Pravala bhasma",
      kind: "mineral",
      botanical_name: null,
      part_used: "Processed coral",
      proportion: "1 part",
      strength: null,
      metal_mineral: true,
    },
  ],
  badges: [
    {
      code: "metal-mineral",
      label: "Contains processed metals/minerals (Pravala bhasma): use only under qualified supervision",
      severity: "caution",
    },
  ],
};

function wrap(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Medication views", () => {
  it("shows salts with strengths, badges and the citation for a biomedical product", () => {
    wrap(
      <MedicationCard
        m={combi}
        link={{
          id: 1,
          status: "approved",
          status_label: "illustrative sample",
          reviewer: "SAMPLE",
          source: combi.source,
          note: null,
        }}
      />,
    );
    expect(screen.getByRole("link", { name: /Paracetamol 325 mg/ })).toHaveAttribute("href", "/salts/2");
    expect(screen.getByText("Schedule H — prescription-only")).toBeInTheDocument();
    expect(screen.getByText(/CDSCO approved drug list/)).toBeInTheDocument();
    expect(screen.getByText("illustrative sample")).toBeInTheDocument();
    expect(screen.getByText(/Brand examples: Combiflam/)).toBeInTheDocument();
  });

  it("expands an Ayush composition table with the metal/mineral marker", () => {
    wrap(<MedicationCard m={kamadugha} />);
    expect(screen.getByText(/use only under qualified supervision/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Composition \(1 ingredient\)/ }));
    expect(screen.getByRole("table", { name: /Composition as printed/ })).toBeInTheDocument();
    expect(screen.getByText("1 part")).toBeInTheDocument();
  });

  it("resolves a brand, shows the reference-only banner and warns on duplicate salts", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      let body: unknown = combi;
      if (url.includes("/medications/search")) {
        body = {
          query: "Combiflam",
          brand: {
            found: true,
            brand: "Combiflam",
            medication_code: "SAMPLE-MED-B003",
            match: "exact",
            strength_note: null,
            message: "ok",
          },
          hits: [
            {
              type: "brand",
              ref: "SAMPLE-MED-B003",
              name: "Combiflam (Ibuprofen + Paracetamol tablet)",
              matched: "Combiflam",
              kind: "biomedical",
              score: 1,
            },
          ],
          disclaimer: "x",
        };
      }
      if (url.endsWith("/medications/check")) {
        body = {
          medications: [],
          unknown_codes: [],
          prescription_only: ["SAMPLE-MED-B003"],
          disclaimer: "x",
          interaction_check: "Interaction checking is not available yet.",
          duplicates: [
            {
              kind: "salt",
              severity: "warning",
              ingredient_id: 2,
              ingredient: "Paracetamol",
              medications: [],
              message: "Duplicate salt: Paracetamol is present in A and B.",
            },
          ],
        };
      }
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    });
    wrap(<MedicationSearch />);
    expect(screen.getAllByRole("note")[0]).toHaveTextContent("Reference only — not a prescription");
    fireEvent.change(screen.getByPlaceholderText(/Combiflam/), { target: { value: "Combiflam" } });
    expect(await screen.findByText(/exact match/)).toBeInTheDocument();
    const add = await screen.findByRole("button", { name: "Add to check list" });
    fireEvent.click(add);
    // A second product is needed before the check runs.
    expect(screen.queryByText(/Duplicate salt/)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "In check list" })).toBeDisabled());
  });
});
