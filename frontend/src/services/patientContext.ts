import { useEffect, useState } from "react";

// The synthetic patient in focus: medication panels check against their active salts, and the Condition
// Builder uses them as the subject.
const KEY = "namasetu.patient";
const listeners = new Set<() => void>();

export interface SelectedPatient {
  id: string;
  display: string;
  abha_number?: string;
}

export function getSelectedPatient(): SelectedPatient | null {
  const raw = sessionStorage.getItem(KEY);
  return raw ? (JSON.parse(raw) as SelectedPatient) : null;
}

export function setSelectedPatient(id: string | null, display?: string, abha_number?: string) {
  if (id) sessionStorage.setItem(KEY, JSON.stringify({ id, display: display ?? id, abha_number }));
  else sessionStorage.removeItem(KEY);
  listeners.forEach((l) => l());
}

export function useSelectedPatient(): SelectedPatient | null {
  const [p, setP] = useState(getSelectedPatient);
  useEffect(() => {
    const l = () => setP(getSelectedPatient());
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return p;
}
