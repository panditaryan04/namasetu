import { DISCLAIMER } from "../constants";

export function Footer() {
  return (
    <footer className="border-t border-med-border bg-white/60 py-6 text-center text-xs text-med-body backdrop-blur-xl dark:bg-slate-950/60">
      <div className="mx-auto max-w-6xl px-6">
        <p className="hover-body">{DISCLAIMER}</p>
        <p className="hover-heading mt-1 font-medium text-med-heading">
          Team Byte Strong · Chandigarh University · SIH25026
        </p>
      </div>
    </footer>
  );
}
