import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CrosswalkTable } from "../src/components/CrosswalkTable";

const maps: Record<string, unknown> = {
  "namaste-to-tm2": {
    resourceType: "ConceptMap",
    id: "namaste-to-tm2",
    version: "SAMPLE-1",
    group: [
      {
        element: [
          {
            code: "SAMPLE-AYU-0001",
            display: "Amavata",
            target: [
              { code: "SAMPLE-TM2-0111", display: "Ama-vata joint disorder", equivalence: "equivalent" },
            ],
          },
          {
            code: "SAMPLE-AYU-0017",
            display: "Kasa",
            target: [{ code: "SAMPLE-TM2-0301", display: "Cough disorder (TM2)", equivalence: "equivalent" }],
          },
        ],
      },
    ],
  },
  "tm2-to-biomedicine": {
    resourceType: "ConceptMap",
    id: "tm2-to-biomedicine",
    group: [
      {
        element: [
          {
            code: "SAMPLE-TM2-0111",
            target: [{ code: "SAMPLE-BIO-0001", display: "Rheumatoid arthritis", equivalence: "relatedto" }],
          },
        ],
      },
    ],
  },
};

afterEach(() => vi.restoreAllMocks());

describe("CrosswalkTable", () => {
  it("joins NAMASTE -> TM2 -> biomedicine from the two published ConceptMaps", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const id = String(input).split("/ConceptMap/")[1]!;
      return Promise.resolve(new Response(JSON.stringify(maps[id]), { status: 200 }));
    });
    const onPick = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <CrosswalkTable onPick={onPick} />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByText(
        "2 NAMASTE terms mapped to WHO ICD-11 TM2; 1 of them also linked to an ICD-11 biomedicine code.",
      ),
    ).toBeInTheDocument();
    const amavata = screen.getByText("SAMPLE-AYU-0001").closest("tr")!;
    expect(within(amavata).getByText("SAMPLE-TM2-0111")).toBeInTheDocument();
    expect(within(amavata).getByText("SAMPLE-BIO-0001")).toBeInTheDocument();
    expect(within(amavata).getByText("→ Related to (not equivalent)")).toBeInTheDocument();
    const kasa = screen.getByText("SAMPLE-AYU-0017").closest("tr")!;
    expect(within(kasa).getByText("No approved biomedicine link")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter"), { target: { value: "kasa" } });
    expect(screen.queryByText("SAMPLE-AYU-0001")).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByText("SAMPLE-AYU-0017").closest("tr")!).getByRole("button"));
    expect(onPick).toHaveBeenCalledWith(
      expect.objectContaining({ namaste: { code: "SAMPLE-AYU-0017", display: "Kasa" } }),
    );
  });
});
