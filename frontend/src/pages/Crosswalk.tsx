import { useNavigate } from "react-router-dom";
import { CrosswalkTable } from "../components/CrosswalkTable";

/** The full AYUSH (NAMASTE) <-> WHO ICD-11 crosswalk, readable by every signed-in role. */
export default function Crosswalk() {
  const navigate = useNavigate();
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">AYUSH ↔ WHO ICD-11 crosswalk</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Each NAMASTE (AYUSH) code, its WHO ICD-11 Traditional Medicine (TM2, Chapter 26) code and, where
          approved, the ICD-11 biomedicine code it relates to. “Use” opens the full mapping evidence.
        </p>
      </div>
      <div className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
        <CrosswalkTable onPick={(r) => navigate(`/map?q=${encodeURIComponent(r.namaste.display)}`)} />
      </div>
    </div>
  );
}
