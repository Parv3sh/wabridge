import { useState } from "react";
import { asEngineError, openExternal } from "../engine";
import { formatCount, useApp } from "../store";
import type { ConvertResult, EngineError } from "../types";
import { Button, Check, Choice, ErrorNotice, Notice, ProgressBar, Steps } from "../components/ui";

const ISSUES_URL = "https://github.com/parveshkumar/wabridge/issues/new";

type Phase = "options" | "running" | "verify" | "failed";

export function TransferStep({ onDone }: { onDone: (mediaPending: boolean) => void }) {
  const { state, run, activity } = useApp();
  const hasMedia = !!state?.stages.media;
  const [mode, setMode] = useState<"text" | "media">("text");
  const [systemMsgs, setSystemMsgs] = useState(false);
  const [phase, setPhase] = useState<Phase>("options");
  const [err, setErr] = useState<EngineError | null>(null);
  const [result, setResult] = useState<ConvertResult | null>(null);
  const [usedSystem, setUsedSystem] = useState(false);
  const [rolledBack, setRolledBack] = useState(false);

  const start = async (system = false) => {
    setErr(null);
    setPhase("running");
    setUsedSystem(system);
    try {
      const conv = await run<ConvertResult>(
        "convert",
        { media: mode === "media", include_system: systemMsgs },
        { label: "Converting chats" }
      );
      setResult(conv);
      await run("inject", {}, { label: "Adding chats to the iPhone backup", stages: ["inject"] });
      await run("ios.restore", { system }, { label: "Restoring to the iPhone", stages: ["ios_restore"] });
      setPhase("verify");
    } catch (e) {
      setErr(asEngineError(e));
      setPhase("failed");
    }
  };

  const restoreOnly = async (system: boolean) => {
    setErr(null);
    setPhase("running");
    setUsedSystem(system);
    try {
      await run("ios.restore", { system }, { label: "Restoring to the iPhone", stages: ["ios_restore"] });
      setPhase("verify");
    } catch (e) {
      setErr(asEngineError(e));
      setPhase("failed");
    }
  };

  const rollback = async () => {
    setErr(null);
    setPhase("running");
    try {
      await run("ios.rollback", { system: usedSystem }, { label: "Putting the iPhone back", stages: ["ios_rollback"] });
      setRolledBack(true);
      setPhase("failed");
    } catch (e) {
      setErr(asEngineError(e));
      setPhase("failed");
    }
  };

  const running = phase === "running";

  return (
    <article className="screen">
      <h1>Move the chats across</h1>

      {phase === "options" && (
        <>
          <p className="lede">
            WaBridge writes your Android chats into WhatsApp’s database inside the iPhone backup, then restores that
            backup. The iPhone restarts once.
          </p>
          <Choice
            name="mode"
            value={mode}
            onChange={(v) => setMode(v as "text" | "media")}
            options={[
              { value: "text", label: "Chats only", detail: "Recommended first. Messages, groups, names and dates. Verified on real devices." },
              {
                value: "media",
                label: "Chats and media",
                detail: hasMedia
                  ? "Experimental: in testing so far, media arrived as placeholders that would not open. Run chats-only first, then try this."
                  : "Media was not copied from the Android phone. Go back to the Android step to copy it.",
                disabled: !hasMedia,
              },
            ]}
          />
          <Check checked={systemMsgs} onChange={setSystemMsgs} label="Include group events" detail="“X joined”, “Y changed the subject” and similar. Off by default; the messages themselves are unaffected." />

          <Notice tone="warn" title="Before you start: turn off Find My iPhone">
            <p>Apple refuses to restore any backup while it is on. Turn it back on as soon as the transfer is done.</p>
            <Steps items={[<>On the iPhone open <b>Settings</b>, tap your name, then <b>Find My</b>.</>, <>Tap <b>Find My iPhone</b> and switch it off (it asks for your Apple ID password).</>, <>Keep the iPhone unlocked and plugged in until it has restarted.</>]} />
            <p className="aside">Greyed out? Settings › Screen Time › Content &amp; Privacy Restrictions › Location Services must be set to “Allow changes”.</p>
          </Notice>

          <div className="actions">
            <Button kind="primary" onClick={() => start(false)}>
              Convert and restore
            </Button>
          </div>
        </>
      )}

      {running && activity && (
        <>
          <ProgressBar pct={activity.pct} label={activity.cmd === "ios.restore" || activity.cmd === "ios.rollback" ? `${activity.label} — do not unplug the iPhone` : activity.lastLog || activity.label} />
          {result && <p className="aside">{formatCount(result.messages_written)} messages written{result.media ? `, ${formatCount(result.media_linked)} media files attached` : ""}.</p>}
        </>
      )}

      {phase === "verify" && (
        <>
          <Notice tone="ok" title="Restore finished; the iPhone is restarting">
            <p>Once it shows the lock screen, unlock it and open WhatsApp. Groups appear straight away; some one-to-one chats only show in the list after the next message arrives, but their history is there.</p>
          </Notice>
          {result && (
            <p>
              {formatCount(result.messages_written)} messages went across{result.messages_skipped_duplicate ? ` (${formatCount(result.messages_skipped_duplicate)} were already on the iPhone)` : ""}.
            </p>
          )}
          <h2>Do your chats show in WhatsApp on the iPhone?</h2>
          <div className="actions">
            <Button kind="primary" onClick={() => onDone(mode === "text" && hasMedia)}>
              Yes, they’re there
            </Button>
            <Button kind="secondary" onClick={() => setPhase("failed")}>
              No, something’s wrong
            </Button>
          </div>
        </>
      )}

      {phase === "failed" && (
        <>
          {err && <ErrorNotice error={err} />}
          {rolledBack && <Notice tone="ok" title="The iPhone was put back to how it was before the transfer" />}
          {!err && !rolledBack && (
            <Notice tone="warn" title="The restore ran, but WhatsApp doesn’t show the chats">
              <p>Nothing is lost — the Android phone still has everything. Two things to try, in order.</p>
            </Notice>
          )}
          <div className="actions actions-wrap">
            {err?.code === "find_my" || (!err && !rolledBack) ? (
              <Button kind="primary" onClick={() => restoreOnly(err ? usedSystem : true)} disabled={running}>
                {err?.code === "find_my" ? "Find My is off now, restore again" : "Restore again including system files"}
              </Button>
            ) : (
              <Button kind="primary" onClick={() => setPhase("options")} disabled={running}>
                Change options and try again
              </Button>
            )}
            {!rolledBack && (
              <Button kind="secondary" onClick={rollback} disabled={running}>
                Put the iPhone back how it was
              </Button>
            )}
            <Button kind="quiet" onClick={() => void openExternal(ISSUES_URL)}>
              Report what happened
            </Button>
          </div>
          <p className="aside">The console (top right) has the full log; attach it to the report. It contains chat names and counts but never message text or your key.</p>
        </>
      )}
    </article>
  );
}
