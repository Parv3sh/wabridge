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

  const lamp: LampState = !devices ? "wait" : connected ? (encrypted ? "warn" : "ok") : devices.iphone_error?.toLowerCase().includes("trust") ? "warn" : "wait";
  const title = connected ? phone!.name || "iPhone" : "Waiting for the iPhone";
  const detail = connected
    ? `iOS ${phone!.ios_version}${used !== null ? `, ${formatBytes(used)} in use` : ""}${encrypted ? ", encrypted backups on" : ", ready"}`
    : devices?.iphone_error ?? "Plug it in, unlock it, and tap Trust if asked";

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

  return (
    <article className="screen">
      <h1>Your iPhone</h1>
      <Berth kind="iphone" lamp={lamp} title={title} detail={detail} />

      {!backedUp && (
        <>
          <p className="lede">WhatsApp has to be installed and signed in on the iPhone before its data can be replaced.</p>
          <Steps
            items={[
              <>
                Install WhatsApp from the App Store and register with the <b>same phone number</b>. It will say the number is in use elsewhere — that is expected. Skip any iCloud restore.
              </>,
              <>Send one message to anyone, so WhatsApp creates its database.</>,
              <>Plug the iPhone into this computer, unlock it, and tap <b>Trust</b> if it asks.</>,
            ]}
          />

          {connected && encrypted && (
            <Notice tone="warn" title="This iPhone encrypts its backups">
              <p>WaBridge can only edit an unencrypted backup. Type your backup password and WaBridge turns encryption off (the iPhone may ask for its passcode), or untick “Encrypt local backup” in Finder.</p>
              <SecretField label="Backup password" value={password} onChange={setPassword} onSubmit={() => password && !busy && void disable()} />
              <div className="actions actions-tight">
                <Button kind="secondary" onClick={disable} disabled={!password} busy={activity?.cmd === "ios.disable_encryption"}>
                  Turn off backup encryption
                </Button>
              </div>
            </Notice>
          )}

          {connected && !encrypted && used !== null && (
            <p className={notEnough ? "warn-text" : undefined}>
              The backup will need about <b>{formatBytes(used)}</b>; this computer has <b>{formatBytes(free)}</b> free.
              {notEnough && " Free up space first, or move WaBridge’s data folder to an external drive — the iPhone refuses to back up otherwise."}
            </p>
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
          {activity?.cmd === "ios.backup" && <ProgressBar pct={activity.pct} label={activity.lastLog.includes("passcode") ? "Enter the passcode on the iPhone" : "Backing up the iPhone"} />}

          <div className="actions">
            <Button kind="primary" onClick={() => backup(false)} disabled={!connected || encrypted || busy} busy={activity?.cmd === "ios.backup"}>
              Back up iPhone
            </Button>
          </div>
          <p className="aside">Keep the iPhone unlocked and plugged in. A copy of this backup is kept untouched so the iPhone can always be put back exactly as it is now.</p>
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
