import { useEffect, useMemo, useState } from "react";
import { asEngineError } from "../engine";
import { formatBytes, formatCount, useApp } from "../store";
import type { AndroidCheck, AndroidDevice, EngineError, FetchResult } from "../types";
import { Berth, Button, ErrorNotice, Notice, ProgressBar, SecretField, Stat, Steps, type LampState } from "../components/ui";

type Phase = "connect" | "key" | "fetched";

export function AndroidStep({ onNext }: { onNext: () => void }) {
  const { devices, setPolling, run, activity, state, client } = useApp();
  const [check, setCheck] = useState<AndroidCheck | null>(null);
  const [key, setKey] = useState("");
  const [err, setErr] = useState<EngineError | null>(null);
  const [fetched, setFetched] = useState<FetchResult | null>(null);
  const [mediaDone, setMediaDone] = useState<boolean>(!!state?.stages.media);

  useEffect(() => {
    setPolling(true);
    return () => setPolling(false);
  }, [setPolling]);

  // Already decrypted in an earlier session: show the summary straight away.
  useEffect(() => {
    if (state?.stages.android_decrypt && !fetched) {
      client
        .request<FetchResult>("android.inspect")
        .then((r) => setFetched(r))
        .catch(() => undefined);
    }
  }, [state?.stages.android_decrypt, fetched, client]);

  const phone: AndroidDevice | undefined = devices?.android.find((d) => d.state === "device") ?? devices?.android[0];
  const connected = phone?.state === "device";

  // Once a phone is ready, ask the engine whether WhatsApp and a crypt15 backup are there.
  // Re-asked on every device poll until a backup is found, so tapping "Back up" is noticed.
  const [checkError, setCheckError] = useState<EngineError | null>(null);
  useEffect(() => {
    if (!connected || activity || fetched) return;
    if (check?.has_crypt15) return;
    let cancelled = false;
    client
      .request<AndroidCheck>("android.check", { serial: phone?.serial })
      .then((c) => {
        if (cancelled) return;
        setCheck(c);
        setCheckError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setCheckError(asEngineError(e));
      });
    return () => {
      cancelled = true;
    };
    // `devices` in the deps makes this run once per poll tick.
  }, [connected, check?.has_crypt15, activity, fetched, client, phone?.serial, devices]);

  useEffect(() => {
    if (!connected) {
      setCheck(null);
      setCheckError(null);
    }
  }, [connected]);

  const phase: Phase = fetched ? "fetched" : connected ? "key" : "connect";

  const keyDigits = useMemo(() => key.replace(/[\s\-:]/g, ""), [key]);
  const keyValid = /^[0-9a-fA-F]{64}$/.test(keyDigits);
  const keyProblem = key && !keyValid ? `${keyDigits.length} of 64 characters` : null;

  const lamp: LampState = !devices ? "wait" : connected ? "ok" : phone ? "warn" : "wait";
  const berthTitle = connected
    ? phone?.model?.replace(/_/g, " ") || phone?.serial || "Android phone"
    : phone?.state === "unauthorized"
      ? "Waiting for permission on the phone"
      : devices?.android_error
        ? "Android tools problem"
        : "Waiting for the Android phone";
  const berthDetail = connected
    ? check
      ? check.has_crypt15
        ? `ready, encrypted backup found${check.media_bytes ? `, ${formatBytes(check.media_bytes)} of media` : ""}`
        : "connected, but no end-to-end encrypted backup yet"
      : checkError
        ? checkError.message
        : "connected, checking WhatsApp"
    : phone?.state === "unauthorized"
      ? "Tap Allow on the Android screen (tick Always allow)"
      : devices?.android_error ?? "Plug it in with USB debugging on";

  const fetch = async () => {
    setErr(null);
    try {
      const r = await run<FetchResult>("android.fetch", { serial: phone?.serial, key: keyDigits }, { label: "Decrypting your chats" });
      setFetched(r);
      setKey("");
    } catch (e) {
      setErr(asEngineError(e));
    }
  };

  const pullMedia = async () => {
    setErr(null);
    try {
      await run("android.pull_media", { serial: phone?.serial }, { label: "Copying media", stages: ["android_media"] });
      setMediaDone(true);
    } catch (e) {
      setErr(asEngineError(e));
    }
  };

  const busy = !!activity;

  return (
    <article className="screen">
      <h1>Your Android phone</h1>

      <Berth kind="android" lamp={lamp} title={berthTitle} detail={berthDetail} />

      {phase === "connect" && (
        <>
          <p className="lede">First, let this computer talk to the phone.</p>
          <Steps
            items={[
              <>
                Open <b>Settings</b>, then <b>About phone</b>, and tap <b>Build number</b> seven times.
              </>,
              <>
                Back in Settings, open <b>Developer options</b> and turn on <b>USB debugging</b>.
              </>,
              <>Plug the phone in. When it asks “Allow USB debugging?”, tick “Always allow” and tap Allow.</>,
            ]}
          />
          {devices?.android_error && <Notice tone="warn" title={devices.android_error} />}
        </>
      )}

      {phase === "key" && (
        <>
          {checkError && checkError.code === "no_whatsapp" && (
            <Notice tone="warn" title="WhatsApp isn’t set up on this phone yet">
              <p>Open WhatsApp on the Android phone once and make a backup (steps below); this updates by itself.</p>
            </Notice>
          )}
          {checkError && checkError.code !== "no_whatsapp" && <ErrorNotice error={checkError} />}
          {check && !check.has_crypt15 && (
            <Notice tone="warn" title="Turn on end-to-end encrypted backup first">
              <p>WaBridge needs the backup format that comes with a 64-digit key. Follow the steps below, then this notice disappears on its own.</p>
            </Notice>
          )}
          <p className="lede">WhatsApp protects its backup with a 64-digit key that only you can see. Get it, make a fresh backup, then paste the key here.</p>
          <Steps
            items={[
              <>
                In WhatsApp open <b>⋮ › Settings › Chats › Chat backup › End-to-end encrypted backup</b> and tap <b>Turn on</b>.
              </>,
              <>
                If it offers a passkey or password, choose <b>More options › Use 64-digit encryption key instead</b>, then <b>Generate your 64-digit key</b>.
              </>,
              <>Long-press the key to copy it and paste it into a note. WhatsApp shows it only once. Tap Continue, then Create.</>,
              <>
                Back on <b>Chat backup</b>, tap the green <b>Back up</b> button and wait for it to finish.
              </>,
            ]}
          />
          <p className="aside">Already on with a password? Open End-to-end encrypted backup › Change password › “I lost my encryption key” to get a fresh key, then back up again.</p>

          <SecretField
            label="64-digit key"
            value={key}
            onChange={setKey}
            placeholder="Paste the key; spaces are fine"
            hint="Used only on this computer to open the backup. It is never saved or sent anywhere."
            invalid={keyProblem}
            autoFocus
            onSubmit={() => keyValid && !busy && void fetch()}
          />
          {err && <ErrorNotice error={err} />}
          {activity?.cmd === "android.fetch" && <ProgressBar pct={null} label={activity.lastLog || activity.label} />}
          <div className="actions">
            <Button kind="primary" onClick={fetch} disabled={!keyValid || !check?.has_crypt15} busy={activity?.cmd === "android.fetch"}>
              Decrypt backup
            </Button>
          </div>
        </>
      )}

      {phase === "fetched" && fetched && (
        <>
          <div className="stats">
            <Stat value={formatCount(fetched.stats.chats)} label="chats" />
            <Stat value={formatCount(fetched.stats.groups)} label="groups" />
            <Stat value={formatCount(fetched.stats.messages)} label="messages" />
            <Stat value={formatCount(fetched.stats.media)} label="photos, videos, files" />
          </div>
          <p className="lede">Your biggest conversations, so you can check this is the right account:</p>
          <ul className="chatlist">
            {fetched.top_chats.map((c) => (
              <li key={c.name + c.messages}>
                <span className={`chatlist-name${c.named ? "" : " chatlist-unnamed"}`}>
                  {c.is_group ? <span className="tag">group</span> : null}
                  {c.name}
                </span>
                <span className="chatlist-count">{formatCount(c.messages)}</span>
              </li>
            ))}
          </ul>
          {fetched.unnamed_chats > 0 && (
            <p className="aside">
              {fetched.unnamed_chats} chats show a phone number instead of a name. WhatsApp on the iPhone fills names in from its own Contacts once you grant it access.
            </p>
          )}

          <h2>Photos, videos and voice notes</h2>
          {mediaDone ? (
            <Notice tone="ok" title="Media copied from the phone" />
          ) : (
            <>
              <p>
                About {formatBytes(fetched.media_bytes)} to copy. This is the slow part, and media on the iPhone is still experimental — you can always skip it now and come back.
              </p>
              {err && err.code !== "wrong_key" && <ErrorNotice error={err} />}
              {activity?.cmd === "android.pull_media" && <ProgressBar pct={activity.pct} label="Copying media" />}
            </>
          )}
          <div className="actions">
            {!mediaDone && (
              <Button kind="secondary" onClick={pullMedia} busy={activity?.cmd === "android.pull_media"} disabled={busy || !connected}>
                Copy media
              </Button>
            )}
            <Button kind="primary" onClick={onNext} disabled={busy}>
              {mediaDone ? "Continue to the iPhone" : "Skip media for now"}
            </Button>
          </div>
        </>
      )}
    </article>
  );
}
