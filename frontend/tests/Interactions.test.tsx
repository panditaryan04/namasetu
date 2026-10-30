import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AlertList, InteractionAlert } from "../src/components/InteractionAlert";
import { MedicationTimeline } from "../src/components/MedicationTimeline";
import type { Alert, TimelineItem } from "../src/services/api";

const alert: Alert = {
  id: 7,
  kind: "herb-drug",
  severity: "moderate",
  evidence_level: "theoretical",
  agents: ["Curcumin", "Glimepiride"],
  path: "Nisha Amalaki Churna → contains herb Haridra → constituent Curcumin × Glimepiride 1 mg tablet → salt Glimepiride",
  mechanism: "Additive blood-glucose lowering reported in experimental studies.",
  management_note: "Clinician to review glycaemic control if combined.",
  citation: "Herb–drug interaction review (SAMPLE transcription; verify against source)",
  dataset: "interactions@SAMPLE-1 (row source SAMPLE-1)",
  candidate_code: "SAMPLE-MED-A008",
  other_medication: "Glimepiride 1 mg tablet (Amaryl 1 OD)",
  other_status: "active",
  other_last_active: "active (since 2025-02-01)",
  status: "open",
  decided_by: null,
  decision_reason: null,
};

function wrap(ui: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

describe("InteractionAlert", () => {
  it("shows severity, path, evidence and citation; override needs a reason", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            ...alert,
            status: "overridden",
            decided_by: "clinician",
            decision_reason: "HbA1c monitored",
          }),
          { status: 200 },
        ),
      ),
    );
    wrap(<InteractionAlert a={alert} />);
    expect(screen.getByRole("alert", { name: /moderate Herb–drug interaction/ })).toBeInTheDocument();
    expect(screen.getByText(/constituent Curcumin × Glimepiride/)).toBeInTheDocument();
    expect(screen.getByText(/evidence: theoretical/)).toBeInTheDocument();
    expect(screen.getByText(/verify against source/)).toBeInTheDocument();
    const override = screen.getByRole("button", { name: "Override" });
    expect(override).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "HbA1c monitored" } });
    fireEvent.click(override);
    expect(await screen.findByText(/overridden by clinician/)).toBeInTheDocument();
    expect(JSON.parse(String(spy.mock.calls[0]![1]?.body))).toEqual({
      action: "override",
      reason: "HbA1c monitored",
    });
  });

  it("states the dataset when nothing is found (never 'no interactions')", () => {
    wrap(
      <AlertList
        result={{
          alerts: [],
          message:
            "No interaction found in the loaded dataset (interactions@SAMPLE-1). Absence of data is not proof of safety.",
        }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("loaded dataset (interactions@SAMPLE-1)");
  });
});

describe("MedicationTimeline", () => {
  const items: TimelineItem[] = [
    {
      id: 1,
      text: "Folitrax 2.5",
      status: "active",
      active_now: true,
      period_start: "2024-11-05",
      period_end: null,
      encounter_id: "e1",
      source_system: "fhir:x",
      resolution: {
        kind: "brand",
        medication_code: "SAMPLE-MED-B004",
        ingredient_ids: [1],
        label: "Methotrexate 2.5 mg tablet (Folitrax 2.5)",
        note: null,
      },
      agents: [
        { type: "salt", name: "Methotrexate", path: "Methotrexate 2.5 mg tablet → salt Methotrexate" },
      ],
    },
    {
      id: 2,
      text: "Brufen 400 TDS",
      status: "stopped",
      active_now: false,
      period_start: "2024-11-05",
      period_end: "2025-01-10",
      encounter_id: "e1",
      source_system: "fhir:x",
      resolution: {
        kind: "brand",
        medication_code: "SAMPLE-MED-B002",
        ingredient_ids: [2],
        label: "Ibuprofen 400 mg tablet",
        note: null,
      },
      agents: [],
    },
    {
      id: 3,
      text: "Syp Xyzol",
      status: "active",
      active_now: true,
      period_start: "2026-05-02",
      period_end: null,
      encounter_id: null,
      source_system: "free-text",
      resolution: {
        kind: "unresolved",
        medication_code: null,
        ingredient_ids: [],
        label: "Unrecognised — please verify",
        note: null,
      },
      agents: [],
    },
  ];
  it("renders bars and expands salts with their path", async () => {
    wrap(<MedicationTimeline items={items} asOf="2026-10-30" />);
    expect(screen.getAllByText(/please verify/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /Methotrexate 2.5 mg tablet/ }));
    await waitFor(() =>
      expect(screen.getByText("Methotrexate 2.5 mg tablet → salt Methotrexate")).toBeInTheDocument(),
    );
    expect(screen.getByTitle("2024-11-05 → 2025-01-10")).toBeInTheDocument();
  });
});
