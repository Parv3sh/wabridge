import { useState } from "react";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
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

  const showFolder = async () => {
    if (!state?.work) return;
    try {
      await revealItemInDir(state.work);
    } catch {
      /* browser preview, or a platform without a file manager hook: the console shows the path */
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

      {mediaPending && cleaned === null && (
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
          <p>WaBridge kept a decrypted copy of your chats, the media and two iPhone backups on this computer. Delete them once you are happy with the iPhone.</p>
          {err && <ErrorNotice error={err} />}
          {confirm && (
            <p>
              This deletes WaBridge’s copies from this computer: the decrypted chats, the media and both iPhone backups. After that WaBridge can no longer put the iPhone back to how it was. Your chats stay on both phones.
            </p>
          )}
          <div className="actions">
            {confirm ? (
              <>
                <Button kind="danger" onClick={clean} busy={activity?.cmd === "clean"}>
                  Delete WaBridge’s copies
                </Button>
                <Button kind="quiet" onClick={() => setConfirm(false)}>
                  Keep them for now
                </Button>
              </>
            ) : (
              <>
                <Button kind="secondary" onClick={() => setConfirm(true)}>
                  Delete my data
                </Button>
                <Button kind="quiet" onClick={() => void showFolder()} disabled={!state?.work}>
                  Show the folder
                </Button>
              </>
            )}
          </div>
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
      </div>
      <div className="actions actions-sticky">
        <Button kind="quiet" onClick={onRestart} disabled={!!activity}>
          Start another migration
        </Button>
      </div>
    </article>
  );
}
