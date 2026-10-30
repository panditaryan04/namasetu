import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ConditionBuilder from "../src/pages/ConditionBuilder";

function renderAt(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/condition" element={<ConditionBuilder />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("ConditionBuilder", () => {
  it("posts the build request and previews the FHIR Condition", async () => {
    const condition = {
      resourceType: "Condition",
      id: "abc",
      code: {
        coding: [{ code: "SAMPLE-AYU-0001" }, { code: "SAMPLE-TM2-0111" }, { code: "SAMPLE-BIO-0001" }],
      },
    };
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => Promise.resolve(new Response(JSON.stringify(condition), { status: 201 })));
    renderAt("/condition?namaste=SAMPLE-AYU-0001&tm2=SAMPLE-TM2-0111&basis=approved");
    expect(screen.getByLabelText(/ICD-11 TM2 code/)).toHaveValue(""); // approved: server uses the approved mapping
    fireEvent.click(screen.getByRole("button", { name: "Build Condition" }));
    expect(await screen.findByText(/"SAMPLE-BIO-0001"/)).toBeInTheDocument();
    const body = JSON.parse(String(spy.mock.calls[0]![1]?.body));
    expect(body).toMatchObject({ namaste_code: "SAMPLE-AYU-0001", purpose_of_use: "TREAT" });
    expect(body.tm2_code).toBeUndefined();
  });

  it("requires explicit confirmation for a suggestion and shows OperationOutcome errors", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            resourceType: "OperationOutcome",
            issue: [
              {
                severity: "error",
                code: "business-rule",
                diagnostics: "HIGH-band suggestion requires confirm_suggestion=true",
              },
            ],
          }),
          { status: 422 },
        ),
      ),
    );
    renderAt("/condition?namaste=SAMPLE-AYU-0040&tm2=SAMPLE-TM2-0201&basis=suggestion");
    expect(screen.getByLabelText(/ICD-11 TM2 code/)).toHaveValue("SAMPLE-TM2-0201");
    expect(screen.getByRole("checkbox", { name: /I confirm this HIGH-band suggestion/ })).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Build Condition" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("requires confirm_suggestion=true");
  });
});
