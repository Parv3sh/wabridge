import { useEffect, useMemo, useRef, useState } from "react";
import { asEngineError } from "../engine";
import { formatBytes, formatCount, useApp } from "../store";
import type { AndroidCheck, AndroidDevice, EngineError, FetchResult } from "../types";
import { Berth, Button, ErrorNotice, Notice, ProgressBar, SecretField, Stat, Steps, type LampState } from "../components/ui";

type Phase = "resume" | "connect" | "key" | "fetched";
const TOP_CHATS_SHOWN = 6;

export function AndroidStep({ onNext }: { onNext: () => void }) {
  const { devices, setPolling, run, activity, state, client } = useApp();
  const [check, setCheck] = useState<AndroidCheck | null>(null);
  const [key, setKey] = useState("");
  const [touched, setTouched] = useState(false);
  const [err, setErr] = useState<EngineError | null>(null);
  const [fetched, setFetched] = useState<FetchResult | null>(null);
  const [mediaDone, setMediaDone] = useState<boolean>(!!state?.stages.media);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    setPolling(true);
    return () => setPolling(false);
  }, [setPolling]);

  // Already decrypted in an earlier session: read the summary instead of asking for the key again.
  // Until it arrives the screen shows a "resume" phase rather than the connect/key instructions.
  const [inspectError, setInspectError] = useState<EngineError | null>(null);
  const resuming = !!state?.stages.android_decrypt && !fetched && !inspectError;
  useEffect(() => {
    if (!resuming) return;
    let cancelled = false;
    client
      .request<FetchResult>("android.inspect")
      .then((r) => {
        if (!cancelled) setFetched(r);
      })
      .catch((e: unknown) => {
        if (!cancelled) setInspectError(asEngineError(e));
      });
    return () => {
      cancelled = true;
    };
  }, [resuming, client]);

  const phone: AndroidDevice | undefined = devices?.android.find((d) => d.state === "device") ?? devices?.android[0];
  const connected = phone?.state === "device";

  // Once a phone is ready, ask the engine whether WhatsApp and a crypt15 backup are there.
  // Re-asked on every device poll until a backup is found, so tapping "Back up" is noticed — but
  // only one check is in flight at a time and its answer is never discarded: on a full phone the
  // check can take longer than the 3 s poll, and cancelling it each tick would leave the screen on
  // "checking WhatsApp" forever.
  const [checkError, setCheckError] = useState<EngineError | null>(null);
  const checking = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!connected || activity || fetched || resuming) return;
    if (check?.has_crypt15 || checking.current) return;
    checking.current = true;
    client
      .request<AndroidCheck>("android.check", { serial: phone?.serial })
      .then((c) => {
        if (!mounted.current) return;
        setCheck(c);
        setCheckError(null);
      })
      .catch((e: unknown) => {
        if (mounted.current) setCheckError(asEngineError(e));
      })
      .finally(() => {
        checking.current = false;
      });
    // `devices` in the deps makes this run once per poll tick.
  }, [connected, check?.has_crypt15, activity, fetched, resuming, client, phone?.serial, devices]);

  useEffect(() => {
    if (!connected) {
      setCheck(null);
      setCheckError(null);
    }
  }, [connected]);

  const phase: Phase = fetched ? "fetched" : resuming ? "resume" : connected ? "key" : "connect";

  const keyDigits = useMemo(() => key.replace(/[\s\-:]/g, ""), [key]);
  const keyValid = /^[0-9a-fA-F]{64}$/.test(keyDigits);
  // While typing, the count is a neutral hint; it only becomes an error once the field is left or submitted.
  const keyProblem = touched && key && !keyValid ? `${keyDigits.length} of 64 characters` : null;
  const keyHint = key ? `${keyDigits.length} of 64` : "Used only on this computer to open the backup. It is never saved or sent anywhere.";

  const noBackupYet = !!check && !check.has_crypt15;
  const lamp: LampState = !devices ? "wait" : connected ? (checkError || noBackupYet ? "wait" : "ok") : phone ? "warn" : "wait";
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
        ? checkError.code === "no_whatsapp"
          ? "connected, but WhatsApp isn’t installed or hasn’t been opened yet"
          : checkError.message
        : "connected, checking WhatsApp"
    : phone?.state === "unauthorized"
      ? "tap Allow on the Android screen (tick Always allow)"
      : devices?.android_error ?? "plug it in with USB debugging on (steps below)";

  const fetch = async () => {
    setTouched(true);
    if (!keyValid) return;
    setErr(null);
    try {
      const r = await run<FetchResult>("android.fetch", { serial: phone?.serial, key: keyDigits }, { label: "Decrypting your chats" });
      setFetched(r);
      setKey("");
      setTouched(false);
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
  const decrypting = activity?.cmd === "android.fetch";

  const howToGetKey = (
    <details className="steps-disclosure" open={noBackupYet || undefined}>
      <summary>Don’t have the key yet? How to get it</summary>
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
    </details>
  );

  return (
    <article className="screen">
      <h1>Your Android phone</h1>

      <Berth kind="android" lamp={lamp} title={berthTitle} detail={berthDetail} />

      {phase === "resume" && <p className="lede">Reading the chats decrypted last time …</p>}
      {inspectError && !fetched && (
        <Notice tone="warn" title="The chats decrypted last time could not be read">
          <p>{inspectError.message} Decrypt the backup again below.</p>
        </Notice>
      )}

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
              <p>Install WhatsApp on the Android phone, sign in, and open it once. WaBridge re-checks every few seconds and this notice goes away on its own.</p>
            </Notice>
          )}
          {checkError && checkError.code !== "no_whatsapp" && <ErrorNotice error={checkError} />}
          {noBackupYet && (
            <Notice tone="warn" title="Turn on end-to-end encrypted backup first">
              <p>WaBridge needs the backup format that comes with a 64-digit key. Follow the steps below, then this notice disappears on its own.</p>
            </Notice>
          )}
          {err && <ErrorNotice error={err} />}
          {decrypting && (
            <>
              <ProgressBar pct={null} label="Decrypting your chats — usually one to three minutes" />
              {activity?.lastLog && <p className="aside">{activity.lastLog}</p>}
            </>
          )}

          <p className="lede">Paste the 64-digit key WhatsApp showed you when you turned on end-to-end encrypted backup.</p>
          <SecretField
            label="64-digit key"
            value={key}
            onChange={setKey}
            onBlur={() => setTouched(true)}
            placeholder="Paste the key; spaces are fine"
            hint={keyHint}
            invalid={keyProblem}
            autoFocus
            onSubmit={() => {
              if (!busy) void fetch();
            }}
          />
          <div className="actions actions-sticky">
            <Button kind="primary" onClick={fetch} disabled={!keyValid || !check?.has_crypt15} busy={decrypting}>
              Decrypt backup
            </Button>
          </div>
          {howToGetKey}
        </>
      )}

      {phase === "fetched" && fetched && (
        <>
          <div className="stats">
            <Stat value={formatCount(fetched.stats.chats)} label="chats" />
            <Stat value={formatCount(fetched.stats.groups)} label="groups" />
            <Stat value={formatCount(fetched.stats.messages)} label="messages" />
            <Stat value={formatCount(fetched.stats.media)} label="media files" />
          </div>

          <h2>Photos, videos and voice notes</h2>
          {mediaDone ? (
            <Notice tone="ok" title="Media copied from the phone" />
          ) : (
            <>
              <p>
                {fetched.media_bytes ? `About ${formatBytes(fetched.media_bytes)} to copy. ` : ""}This is the slow part, and media on the iPhone is still experimental — you can always skip it now and come back.
              </p>
              {err && err.code !== "wrong_key" && <ErrorNotice error={err} />}
              {activity?.cmd === "android.pull_media" && <ProgressBar pct={activity.pct} label="Copying media" />}
            </>
          )}
          <div className="actions actions-sticky">
            {!mediaDone && (
              <Button kind="secondary" onClick={pullMedia} busy={activity?.cmd === "android.pull_media"} disabled={busy || !connected}>
                Copy media
              </Button>
            )}
            <Button kind="primary" onClick={onNext} disabled={busy}>
              {mediaDone ? "Continue to the iPhone" : "Skip media for now"}
            </Button>
          </div>

          <h2>Your biggest conversations</h2>
          <p className="aside">So you can check this is the right account.</p>
          <ul className="chatlist">
            {fetched.top_chats.slice(0, showAll ? undefined : TOP_CHATS_SHOWN).map((c) => (
              <li key={c.name + c.messages}>
                <span className={`chatlist-name${c.named ? "" : " chatlist-unnamed"}`}>{c.name}</span>
                <span className="chatlist-count">
                  {c.is_group && <span className="tag">group</span>}
                  {formatCount(c.messages)}
                </span>
              </li>
            ))}
          </ul>
          {fetched.top_chats.length > TOP_CHATS_SHOWN && (
            <Button kind="quiet" onClick={() => setShowAll((s) => !s)}>
              {showAll ? "Show fewer" : `Show all ${fetched.top_chats.length}`}
            </Button>
          )}
          {fetched.unnamed_chats > 0 && (
            <p className="aside">
              {fetched.unnamed_chats} chats show a phone number instead of a name. WhatsApp on the iPhone fills names in from its own Contacts once you grant it access.
            </p>
          )}
        </>
      )}
    </article>
  );
}
