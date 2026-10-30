import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ConfidenceBar } from "../components/ConfidenceBar";
import { EvidencePanel } from "../components/EvidencePanel";
import { HierarchyBreadcrumb } from "../components/HierarchyBreadcrumb";
import {
  decideReview,
  getThresholds,
  reviewItem,
  reviewQueue,
  searchTm2,
  type DecisionAction,
  type Explanation,
  type ReviewDetail,
} from "../services/api";
import { can } from "../services/auth";
import { useAuth } from "../services/useAuth";

const BAND_CHIP: Record<string, string> = {
  high: "bg-teal-700 text-white",
  medium: "bg-saffron-500 text-slate-950",
  low: "bg-red-600 text-white",
};
const REASON: Record<string, string> = {
  "medium-band": "Medium confidence",
  "high-band-confirm": "Confirm high-band suggestion",
  proposed: "Proposed by medical records officer",
  "no-safe-match": "No safe automated match",
  "re-review": "Re-review after dataset release",
};

function isTyping(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null;
  return Boolean(
    t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable),
  );
}

function Candidate({
  c,
  selected,
  onSelect,
  thresholds,
}: {
  c: Explanation;
  selected: boolean;
  onSelect: () => void;
  thresholds: { high: number; low: number };
}) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className={`rounded-xl border p-3 ${selected ? "border-teal-600 ring-2 ring-teal-600/20" : "border-slate-200 dark:border-slate-800"}`}
    >
      <div className="flex items-start gap-2">
        <input
          type="radio"
          aria-label={`Select ${c.tm2.display} (${c.tm2.code})`}
          name="candidate"
          checked={selected}
          onChange={onSelect}
          className="mt-1"
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{c.tm2.display}</span>
            <span className="font-mono text-xs text-slate-500">{c.tm2.code}</span>
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${BAND_CHIP[c.band]}`}>
              {c.band}
            </span>
            <span className="rounded-full px-2 py-0.5 text-[11px] ring-1 ring-inset ring-slate-300 dark:ring-slate-600">
              {c.relationship}
            </span>
          </span>
          <span className="mt-1 block">
            <HierarchyBreadcrumb path={c.evidence.hierarchy_path} />
          </span>
        </span>
      </div>
      <div className="mt-2">
        <ConfidenceBar value={c.confidence} band={c.band} thresholds={thresholds} />
      </div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="mt-2 text-sm font-medium text-teal-800 underline dark:text-teal-300"
      >
        {open ? "Hide evidence" : "Evidence"}
      </button>
      {open && <EvidencePanel c={c} semanticMethod="see /eval/ranker" />}
    </div>
  );
}

function Detail({ item, canReview, onDone }: { item: ReviewDetail; canReview: boolean; onDone: () => void }) {
  const qc = useQueryClient();
  const top = item.candidates[0];
  const [selected, setSelected] = useState<string | null>(top?.tm2.code ?? null);
  const [comment, setComment] = useState("");
  const [manual, setManual] = useState("");
  const [manualCode, setManualCode] = useState<{ code: string; display: string } | null>(null);
  const manualHits = useQuery({
    queryKey: ["tm2-search", manual],
    queryFn: () => searchTm2(manual),
    enabled: manual.trim().length >= 2,
  });
  const decide = useMutation({
    mutationFn: (body: {
      action: DecisionAction;
      tm2_code?: string;
      relationship?: string;
      comment?: string;
    }) => decideReview(item.id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review"] });
      setComment("");
      onDone();
    },
  });
  const run = useCallback(
    (action: DecisionAction) => {
      const target = manualCode?.code ?? selected ?? undefined;
      const chosenOther = Boolean(target && target !== top?.tm2.code);
      const finalAction: DecisionAction = action === "approve" && chosenOther ? "choose_other" : action;
      decide.mutate({
        action: finalAction,
        tm2_code: action === "reject" ? undefined : target,
        comment: comment || undefined,
      });
    },
    [manualCode, selected, top, comment, decide],
  );

  useEffect(() => {
    if (!canReview || item.status !== "pending") return;
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.ctrlKey || e.metaKey || e.altKey) return;
      const map: Record<string, DecisionAction> = {
        a: "approve",
        w: "approve_wider",
        n: "approve_narrower",
        r: "reject",
      };
      const action = map[e.key];
      if (action) {
        e.preventDefault();
        run(action);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run, canReview, item.status]);

  const th = useQuery({ queryKey: ["thresholds"], queryFn: getThresholds, staleTime: 60_000 });
  const thresholds = th.data ?? { high: 0.85, low: 0.5 };
  const btn = "rounded-lg px-3 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50";
  return (
    <section aria-label={`Review item ${item.id}`} className="space-y-4">
      <header>
        <p className="text-xs uppercase tracking-wider text-slate-500">
          #{item.id} · {REASON[item.reason] ?? item.reason} · queued by {item.created_by}
        </p>
        <h2 className="text-xl font-bold">
          {item.source_display}{" "}
          <span className="font-mono text-sm font-normal text-slate-500">{item.source_code}</span>
        </h2>
        {item.current_mapping && (
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Current approved mapping: {item.current_mapping.target_display} (
            {item.current_mapping.relationship}, v{item.current_mapping.version})
          </p>
        )}
      </header>

      {item.candidates.length === 0 ? (
        <p className="rounded-xl bg-slate-100 p-3 text-sm dark:bg-slate-800">
          No safe automated match. Use manual search below, or reject.
        </p>
      ) : (
        <div className="grid gap-3 xl:grid-cols-2">
          {item.candidates.map((c) => (
            <Candidate
              key={c.tm2.code}
              c={c}
              thresholds={thresholds}
              selected={!manualCode && selected === c.tm2.code}
              onSelect={() => {
                setSelected(c.tm2.code);
                setManualCode(null);
              }}
            />
          ))}
        </div>
      )}

      {item.status === "pending" ? (
        <div className="space-y-3 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
          <label className="block text-sm font-medium">
            Manual TM2 search
            <input
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              placeholder="e.g. fever, joint"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
            />
          </label>
          {manualHits.data && manual.trim().length >= 2 && (
            <ul className="flex flex-wrap gap-2">
              {manualHits.data.expansion.contains.map((h) => (
                <li key={h.code}>
                  <button
                    type="button"
                    onClick={() => setManualCode(h)}
                    aria-pressed={manualCode?.code === h.code}
                    className={`rounded-full px-3 py-1 text-sm ring-1 ring-inset ${manualCode?.code === h.code ? "bg-teal-700 text-white ring-teal-700" : "ring-slate-300 dark:ring-slate-600"}`}
                  >
                    {h.display}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <label className="block text-sm font-medium">
            Comment{" "}
            <span className="font-normal text-slate-500">(mandatory for any override or rejection)</span>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={2}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
            />
          </label>
          {decide.isError && (
            <p role="alert" className="text-sm text-red-700 dark:text-red-300">
              {decide.error.message}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              disabled={!canReview || decide.isPending}
              onClick={() => run("approve")}
              className={`${btn} bg-teal-700 text-white hover:bg-teal-800`}
            >
              Approve <kbd className="ml-1 text-xs opacity-75">a</kbd>
            </button>
            <button
              disabled={!canReview || decide.isPending}
              onClick={() => run("approve_wider")}
              className={`${btn} ring-1 ring-inset ring-slate-300 dark:ring-slate-600`}
            >
              Approve as wider <kbd className="ml-1 text-xs opacity-75">w</kbd>
            </button>
            <button
              disabled={!canReview || decide.isPending}
              onClick={() => run("approve_narrower")}
              className={`${btn} ring-1 ring-inset ring-slate-300 dark:ring-slate-600`}
            >
              Approve as narrower <kbd className="ml-1 text-xs opacity-75">n</kbd>
            </button>
            <button
              disabled={!canReview || decide.isPending}
              onClick={() => run("reject")}
              className={`${btn} bg-red-600 text-white hover:bg-red-700`}
            >
              Reject (no safe match) <kbd className="ml-1 text-xs opacity-75">r</kbd>
            </button>
          </div>
          {!canReview && (
            <p className="text-sm text-slate-500">Your role can view the queue but not decide.</p>
          )}
        </div>
      ) : (
        <p className="rounded-xl bg-slate-100 p-3 text-sm dark:bg-slate-800">
          {item.status} by {item.decided_by}: {item.decision?.action}
          {item.decision?.target && ` → ${item.decision.target} (${item.decision.relationship})`}
          {item.comment && ` — “${item.comment}”`}
          {item.mapping_id && (
            <>
              {" · "}
              <Link to={`/mappings?id=${item.mapping_id}`} className="underline">
                mapping #{item.mapping_id}
              </Link>
            </>
          )}
        </p>
      )}
    </section>
  );
}

export default function ReviewQueue() {
  const user = useAuth();
  const [status, setStatus] = useState("pending");
  const [band, setBand] = useState("");
  const [activeId, setActiveId] = useState<number | null>(null);
  const list = useQuery({
    queryKey: ["review", "queue", status, band],
    queryFn: () => reviewQueue({ status, band, limit: 200 }),
  });
  const items = useMemo(() => list.data?.items ?? [], [list.data]);
  const current = activeId ?? items[0]?.id ?? null;
  const detail = useQuery({
    queryKey: ["review", "item", current],
    queryFn: () => reviewItem(current!),
    enabled: current !== null,
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || !items.length) return;
      const idx = items.findIndex((i) => i.id === current);
      if (e.key === "j") setActiveId(items[Math.min(idx + 1, items.length - 1)]!.id);
      if (e.key === "k") setActiveId(items[Math.max(idx - 1, 0)]!.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, current]);

  const select =
    "rounded-lg border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900";
  return (
    <div className="grid gap-6 lg:grid-cols-[340px_1fr]">
      <section aria-labelledby="queue-heading">
        <h1 id="queue-heading" className="text-2xl font-bold tracking-tight">
          Review queue
        </h1>
        <p className="mt-1 text-xs text-slate-500">
          Keys: <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>a</kbd> approve · <kbd>w</kbd>/<kbd>n</kbd>{" "}
          wider/narrower · <kbd>r</kbd> reject
        </p>
        <div className="mt-3 flex gap-2">
          <label className="sr-only" htmlFor="q-status">
            Status
          </label>
          <select
            id="q-status"
            className={select}
            value={status}
            onChange={(e) => (setStatus(e.target.value), setActiveId(null))}
          >
            {["pending", "approved", "rejected", "all"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          <label className="sr-only" htmlFor="q-band">
            Band
          </label>
          <select
            id="q-band"
            className={select}
            value={band}
            onChange={(e) => (setBand(e.target.value), setActiveId(null))}
          >
            <option value="">all bands</option>
            {["high", "medium", "low"].map((b) => (
              <option key={b}>{b}</option>
            ))}
          </select>
        </div>
        {list.isPending && (
          <div className="mt-3 h-40 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-900" />
        )}
        {list.data && (
          <ul className="mt-3 space-y-1" aria-label="Queue items">
            {items.length === 0 && <li className="text-sm text-slate-500">Nothing here. 🎉</li>}
            {items.map((i) => (
              <li key={i.id}>
                <button
                  type="button"
                  onClick={() => setActiveId(i.id)}
                  aria-current={i.id === current}
                  className={`w-full rounded-xl px-3 py-2 text-left text-sm ${i.id === current ? "bg-teal-50 ring-1 ring-teal-300 dark:bg-teal-950 dark:ring-teal-800" : "hover:bg-slate-100 dark:hover:bg-slate-900"}`}
                >
                  <span className="flex items-center gap-2">
                    <span
                      className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${BAND_CHIP[i.band]}`}
                    >
                      {i.band}
                    </span>
                    <span className="font-medium">{i.source_display}</span>
                  </span>
                  <span className="block truncate text-xs text-slate-500">
                    → {i.top_target_display ?? "no candidate"} · {REASON[i.reason] ?? i.reason}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-xs text-slate-500">{list.data?.total ?? 0} item(s)</p>
      </section>
      <div className="min-w-0">
        {detail.data ? (
          <Detail
            key={detail.data.id + detail.data.status}
            item={detail.data}
            canReview={can(user, "review")}
            onDone={() => setActiveId(null)}
          />
        ) : (
          <div className="grid h-64 place-items-center rounded-2xl border border-dashed border-slate-300 text-sm text-slate-500 dark:border-slate-700">
            Select an item.
          </div>
        )}
      </div>
    </div>
  );
}
