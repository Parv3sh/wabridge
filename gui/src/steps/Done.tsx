import { useState } from "react";
import { asEngineError, openExternal } from "../engine";
import { formatBytes, useApp } from "../store";
import type { EngineError } from "../types";
import { Button, ErrorNotice, Notice, Steps } from "../components/ui";

const REPO_URL = "https://github.com/parvesh-rm/wabridge";

export function DoneStep({ mediaPending, onMediaPass, onRestart }: { mediaPending: boolean; onMediaPass: () => void; onRestart: () => void }) {
  const { run, activity, state } = useApp();
  const [cleaned, setCleaned] = useState<number | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<EngineError | null>(null);

  const clean = async () => {
    setErr(null);
    try {
      const r = await run<{ freed_bytes: number }>("clean", {}, { label: "Deleting migration data" });
      setCleaned(r.freed_bytes);
      setConfirm(false);
    } catch (e) {
      setErr(asEngineError(e));
    }
  };

  return (
    <article className="screen">
      <h1>Your chats are on the iPhone</h1>
      <p className="lede">Two things to finish, then you can close WaBridge.</p>

      <Steps
        items={[
          <>
            Turn <b>Find My iPhone</b> back on: Settings › your name › Find My.
          </>,
          <>
            Grant WhatsApp access to <b>Contacts</b> when it asks, so names replace phone numbers.
          </>,
        ]}
      />

      {mediaPending && (
        <Notice tone="info" title="Photos and videos are still on this computer">
          <p>You copied media from the Android phone but transferred chats only. You can run the media pass now; it repeats the transfer with media attached. This part is experimental.</p>
          <div className="actions actions-tight">
            <Button kind="secondary" onClick={onMediaPass}>
              Run the media pass
            </Button>
          </div>
        </Notice>
      )}

      <h2>Your data on this computer</h2>
      {cleaned === null ? (
        <>
          <p>
            WaBridge kept a decrypted copy of your chats, the media and two iPhone backups in its data folder
            {state ? ` (${state.work})` : ""}. Delete them once you are happy with the iPhone.
          </p>
          {err && <ErrorNotice error={err} />}
          <div className="actions">
            {confirm ? (
              <>
                <Button kind="danger" onClick={clean} busy={activity?.cmd === "clean"}>
                  Yes, delete everything
                </Button>
                <Button kind="quiet" onClick={() => setConfirm(false)}>
                  Keep it for now
                </Button>
              </>
            ) : (
              <Button kind="secondary" onClick={() => setConfirm(true)}>
                Delete my data
              </Button>
            )}
          </div>
          {confirm && <p className="aside">This also deletes the untouched iPhone backup, so “put the iPhone back” will no longer be possible from WaBridge.</p>}
        </>
      ) : (
        <Notice tone="ok" title={`Deleted ${formatBytes(cleaned)}`} />
      )}

      <h2>Help the next person</h2>
      <p>WaBridge is free because people report what worked on their phones. A one-line note with your phone models and iOS version makes the tool better for everyone.</p>
      <div className="actions actions-wrap">
        <Button kind="secondary" onClick={() => void openExternal(`${REPO_URL}/issues/new?title=Result%3A+`)}>
          Report your result
        </Button>
        <Button kind="quiet" onClick={() => void openExternal(REPO_URL)}>
          WaBridge on GitHub
        </Button>
        <Button kind="quiet" onClick={onRestart} disabled={!!activity}>
          Start another migration
        </Button>
      </div>
    </article>
  );
}
