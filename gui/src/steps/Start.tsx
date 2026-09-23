import { useState, type ReactNode } from "react";
import { asEngineError } from "../engine";
import { formatBytes, useApp } from "../store";
import type { EngineError } from "../types";
import { Button, ErrorNotice, Notice, ProgressBar } from "../components/ui";
import type { StepId } from "../components/Shell";

export function resumeStep(stages: { android_decrypt: boolean; ios_backup: boolean; ios_restore: boolean }): StepId {
  if (!stages.android_decrypt) return "android";
  if (!stages.ios_backup) return "iphone";
  return "transfer";
}

export function StartStep({ onNext }: { onNext: (step: StepId) => void }) {
  const { state, run, activity, refreshState } = useApp();
  const [err, setErr] = useState<EngineError | null>(null);
  const [cleaning, setCleaning] = useState(false);

  const stages = state?.stages;
  const inProgress = !!stages && (stages.android_decrypt || stages.ios_backup);
  const adbMissing = !!state && !state.adb;
  const lowDisk = !!state && state.free_bytes < 15e9;

  const installAdb = async () => {
    setErr(null);
    try {
      await run("adb.install", {}, { label: "Downloading Android tools", stages: ["adb_install"] });
    } catch (e) {
      setErr(asEngineError(e));
    }
  };

  const startOver = async () => {
    setErr(null);
    setCleaning(true);
    try {
      await run("clean", {}, { label: "Deleting previous data" });
      await refreshState();
    } catch (e) {
      setErr(asEngineError(e));
    } finally {
      setCleaning(false);
    }
  };

  return (
    <article className="screen">
      <h1>Move your WhatsApp chats from Android to iPhone</h1>
      <p className="lede">
        Free, open source, and nothing leaves this computer. You will need both phones, their USB cables, and about an
        hour — most of it waiting for copies. Your chats stay on the Android phone throughout.
      </p>

      <section className="checks" aria-label="Before you begin">
        <CheckRow ok={!!state} label={state ? `Engine ${state.version} ready` : "Starting the engine"} />
        <CheckRow
          ok={!adbMissing}
          label={adbMissing ? "Android tools (adb) not found" : "Android tools found"}
          action={
            adbMissing && (
              <Button kind="secondary" onClick={installAdb} busy={activity?.cmd === "adb.install"}>
                Install Android tools
              </Button>
            )
          }
        />
        <CheckRow
          ok={!lowDisk}
          warn={lowDisk}
          label={state ? `${formatBytes(state.free_bytes)} free on this computer` : "Checking disk space"}
          detail={lowDisk ? "A full iPhone backup needs roughly as much free space as the iPhone uses. You can continue; the iPhone step will tell you exactly how much." : undefined}
        />
      </section>

      {activity?.cmd === "adb.install" && <ProgressBar pct={activity.pct} label={activity.label} />}
      {err && <ErrorNotice error={err} />}

      {inProgress && stages && (
        <Notice tone="info" title="You were partway through a migration">
          <p>
            {stages.android_decrypt && "Android chats are already decrypted"}
            {stages.ios_backup && ", the iPhone is backed up"}
            {stages.ios_restore && ", and a restore has run"}. Continue from there, or start over and delete what was
            copied.
          </p>
        </Notice>
      )}

      <div className="actions">
        {inProgress && stages ? (
          <>
            <Button kind="primary" onClick={() => onNext(resumeStep(stages))} disabled={!state || !!activity}>
              Continue where I left off
            </Button>
            <Button kind="quiet" onClick={startOver} busy={cleaning} disabled={!!activity && !cleaning}>
              Start over
            </Button>
          </>
        ) : (
          <Button kind="primary" onClick={() => onNext("android")} disabled={!state || adbMissing || !!activity}>
            Begin
          </Button>
        )}
      </div>
    </article>
  );
}

function CheckRow({ ok, warn, label, detail, action }: { ok: boolean; warn?: boolean; label: string; detail?: string; action?: ReactNode }) {
  const lamp = warn ? "warn" : ok ? "ok" : "wait";
  return (
    <div className="check-row">
      <span className={`lamp lamp-${lamp}`} aria-hidden="true" />
      <div className="check-text">
        <span>{label}</span>
        {detail && <span className="check-detail">{detail}</span>}
      </div>
      {action}
    </div>
  );
}
