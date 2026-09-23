import { useEffect, useRef, type ReactNode } from "react";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { useApp } from "../store";
import { Button } from "./ui";

export type StepId = "start" | "android" | "iphone" | "transfer" | "done";
export const STEPS: { id: StepId; label: string }[] = [
  { id: "start", label: "Start" },
  { id: "android", label: "Android" },
  { id: "iphone", label: "iPhone" },
  { id: "transfer", label: "Transfer" },
  { id: "done", label: "Done" },
];

export function stepIndex(id: StepId): number {
  return STEPS.findIndex((s) => s.id === id);
}

/**
 * The window: title strip, the transit line on the left, the current screen, and the console
 * drawer. The line is the one bold element — its travelled span fills in harbour teal, the
 * current station carries the amber lamp, and while something runs the span to the next
 * station doubles as the progress bar.
 */
export function Shell({ step, children }: { step: StepId; children: ReactNode }) {
  const { activity, consoleOpen, setConsoleOpen, client } = useApp();
  const current = stepIndex(step);
  return (
    <div className={`shell${consoleOpen ? " shell-console" : ""}`}>
      <header className="titlebar">
        <span className="brand">WaBridge</span>
        <span className="titlebar-spacer" />
        <span className="titlebar-version">{client.version ? `engine ${client.version}` : ""}</span>
        <button type="button" className="btn btn-quiet btn-small" onClick={() => setConsoleOpen(!consoleOpen)} aria-expanded={consoleOpen} aria-controls="console">
          Console
        </button>
      </header>

      <nav className="rail" aria-label="Progress">
        <ol className="line">
          {STEPS.map((s, i) => {
            const state = i < current ? "done" : i === current ? "current" : "todo";
            const spanFill = i === current && activity && activity.pct !== null ? activity.pct : i < current ? 100 : 0;
            const spanBusy = i === current && activity && activity.pct === null;
            return (
              <li key={s.id} className={`station station-${state}`} aria-current={i === current ? "step" : undefined}>
                <span className="station-mark" aria-hidden="true">
                  <span className="station-dot" />
                </span>
                <span className="station-label">{s.label}</span>
                {i < STEPS.length - 1 && (
                  <span className={`span${spanBusy ? " span-busy" : ""}`} aria-hidden="true">
                    <span className="span-fill" style={{ height: `${spanFill}%` }} />
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      </nav>

      <main className="content" id="main">
        {children}
      </main>

      <ConsoleDrawer />
    </div>
  );
}

function ConsoleDrawer() {
  const { lines, consoleOpen, state } = useApp();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (consoleOpen && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines, consoleOpen]);
  if (!consoleOpen) return null;
  const reveal = async () => {
    if (!state?.log_path) return;
    try {
      await revealItemInDir(state.log_path);
    } catch {
      /* permission or platform quirk: the path is shown in the drawer header anyway */
    }
  };
  return (
    <section className="console" id="console" aria-label="Engine console">
      <div className="console-head">
        <span className="console-path mono">{state?.log_path ?? ""}</span>
        <Button kind="quiet" onClick={reveal} disabled={!state?.log_path}>
          Open log folder
        </Button>
      </div>
      <div className="console-lines mono" ref={ref}>
        {lines.length === 0 && <div className="console-empty">Nothing yet.</div>}
        {lines.map((l) => (
          <div key={l.n} className={`console-line console-${l.level}`}>
            {l.text}
          </div>
        ))}
      </div>
    </section>
  );
}
