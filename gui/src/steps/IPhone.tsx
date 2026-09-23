import { useEffect, useState } from "react";
import { asEngineError } from "../engine";
import { formatBytes, useApp } from "../store";
import type { EngineError, IPhoneInfo } from "../types";
import { Berth, Button, ErrorNotice, Notice, ProgressBar, SecretField, Steps, type LampState } from "../components/ui";

export function IPhoneStep({ onNext }: { onNext: () => void }) {
  const { devices, setPolling, run, activity, state } = useApp();
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<EngineError | null>(null);
  const [backedUp, setBackedUp] = useState<boolean>(!!state?.stages.ios_backup);

  useEffect(() => {
    setPolling(true);
    return () => setPolling(false);
  }, [setPolling]);

  const phone: IPhoneInfo | null = devices?.iphone ?? null;
  const connected = !!phone;
  const encrypted = !!phone?.will_encrypt;
  const free = state?.free_bytes ?? 0;
  const used = phone?.disk_used ?? null;
  const notEnough = used !== null && free < used * 1.05;
  // The generic "no iPhone" message also says "tap Trust"; only a real pairing failure is a warning.
  const pairingProblem = !connected && !!devices?.iphone_error && /pair/i.test(devices.iphone_error);

  const lamp: LampState = !devices ? "wait" : connected ? (encrypted ? "warn" : "ok") : pairingProblem ? "warn" : "wait";
  const title = connected ? phone!.name || "iPhone" : "Waiting for the iPhone";
  const detail = connected
    ? `iOS ${phone!.ios_version}${used !== null ? `, ${formatBytes(used)} in use` : ""}${encrypted ? ", encrypted backups on" : ", ready"}`
    : pairingProblem
      ? devices!.iphone_error!
      : "plug it in, unlock it, and tap Trust if asked";

  const disable = async () => {
    setErr(null);
    try {
      await run("ios.disable_encryption", { password }, { label: "Turning off backup encryption" });
      setPassword("");
    } catch (e) {
      setErr(asEngineError(e));
    }
  };

  const backup = async (force = false) => {
    setErr(null);
    try {
      await run("ios.backup", { force }, { label: "Backing up the iPhone", stages: ["ios_backup"] });
      setBackedUp(true);
    } catch (e) {
      setErr(asEngineError(e));
    }
  };

  const busy = !!activity;
  const backingUp = activity?.cmd === "ios.backup";
  const needsDecrypt = connected && encrypted;

  const installSteps = [
    <>
      Install WhatsApp from the App Store and sign in with the <b>same phone number</b>. WhatsApp on the Android phone will stop working — that is expected; its chats are already safe on this computer. If the iPhone offers to move chats from Android or restore from iCloud, skip it.
    </>,
    <>Send one message to anyone, so WhatsApp creates its database.</>,
  ];
  const plugStep = (
    <>
      Plug the iPhone into this computer, unlock it, and tap <b>Trust</b> if it asks.
    </>
  );

  return (
    <article className="screen">
      <h1>Your iPhone</h1>
      <Berth kind="iphone" lamp={lamp} title={title} detail={detail} />

      {!backedUp && (
        <>
          {needsDecrypt && (
            <Notice tone="warn" title="Turn off backup encryption first">
              <p>This iPhone encrypts its computer backups, and WaBridge can only edit an unencrypted one. Type the backup password and WaBridge switches it off (the iPhone may ask for its passcode). Forgotten it? In Finder select the iPhone and untick “Encrypt local backup”.</p>
              <SecretField label="Backup password" value={password} onChange={setPassword} onSubmit={() => password && !busy && void disable()} />
              <div className="actions actions-tight">
                <Button kind="primary" onClick={disable} disabled={!password} busy={activity?.cmd === "ios.disable_encryption"}>
                  Turn off backup encryption
                </Button>
              </div>
            </Notice>
          )}

          {connected ? (
            <>
              <p className="lede">WhatsApp must be installed and signed in with your number on this iPhone. If it is, back it up now.</p>
              <details className="steps-disclosure">
                <summary>WhatsApp not on the iPhone yet? Do this first</summary>
                <Steps items={installSteps} />
              </details>
            </>
          ) : (
            <>
              <p className="lede">WhatsApp has to be installed and signed in on the iPhone before its data can be replaced.</p>
              <Steps items={[...installSteps, plugStep]} />
            </>
          )}

          {connected && !encrypted && used !== null && !notEnough && (
            <p>
              The backup will need about <b>{formatBytes(used)}</b>; this computer has <b>{formatBytes(free)}</b> free.
            </p>
          )}
          {connected && !encrypted && used !== null && notEnough && (
            <Notice
              tone="error"
              title="Not enough space for the backup"
              actions={
                <Button kind="quiet" onClick={() => backup(true)} disabled={busy}>
                  Try anyway
                </Button>
              }
            >
              <p>
                The iPhone holds {formatBytes(used)}; this computer has {formatBytes(free)} free. Free up about {formatBytes(used * 1.05 - free)} — empty the Trash, clear Downloads, delete old iPhone backups — and this notice clears by itself.
              </p>
            </Notice>
          )}

          {err && (
            <ErrorNotice
              error={err}
              actions={
                err.code === "disk_space" && (
                  <Button kind="quiet" onClick={() => backup(true)}>
                    Try anyway
                  </Button>
                )
              }
            />
          )}
          {backingUp && activity && (
            <>
              <ProgressBar pct={activity.pct} label={`Backing up the iPhone${used !== null ? ` — about ${formatBytes(used)}` : ""}`} />
              <p className="aside">If the iPhone asks for its passcode, enter it. Keep it unlocked and plugged in; this is the longest step, often 15–40 minutes.</p>
            </>
          )}

          {!needsDecrypt && (
            <>
              <p className="aside">Keep the iPhone unlocked and plugged in. A copy of this backup is kept untouched so the iPhone can always be put back exactly as it is now.</p>
              <div className="actions actions-sticky">
                <Button kind="primary" onClick={() => backup(false)} disabled={!connected || encrypted || busy || notEnough} busy={backingUp}>
                  Back up iPhone
                </Button>
              </div>
            </>
          )}
        </>
      )}

      {backedUp && (
        <>
          <Notice tone="ok" title="iPhone backed up">
            <p>An untouched copy is saved as the rollback point.</p>
          </Notice>
          <div className="actions">
            <Button kind="primary" onClick={onNext}>
              Continue to transfer
            </Button>
            <Button kind="quiet" onClick={() => setBackedUp(false)} disabled={busy}>
              Back up again
            </Button>
          </div>
        </>
      )}
    </article>
  );
}
