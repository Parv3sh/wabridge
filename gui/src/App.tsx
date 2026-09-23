import { useState } from "react";
import { Shell, type StepId } from "./components/Shell";
import { Button, ErrorNotice } from "./components/ui";
import { AppProvider, useApp } from "./store";
import { AndroidStep } from "./steps/Android";
import { DoneStep } from "./steps/Done";
import { IPhoneStep } from "./steps/IPhone";
import { StartStep } from "./steps/Start";
import { TransferStep } from "./steps/Transfer";

export default function App() {
  return (
    <AppProvider>
      <Router />
    </AppProvider>
  );
}

function Router() {
  const { booted, bootError, retryBoot, consoleOpen, setConsoleOpen } = useApp();
  const [step, setStep] = useState<StepId>("start");
  const [mediaPending, setMediaPending] = useState(false);

  if (bootError) {
    return (
      <Shell step="start">
        <article className="screen">
          <h1>WaBridge couldn’t start its engine</h1>
          <ErrorNotice
            error={bootError}
            actions={
              <>
                <Button kind="primary" onClick={retryBoot}>
                  Try again
                </Button>
                <Button kind="quiet" onClick={() => setConsoleOpen(!consoleOpen)}>
                  Show console
                </Button>
              </>
            }
          />
        </article>
      </Shell>
    );
  }

  if (!booted) {
    return (
      <Shell step="start">
        <article className="screen screen-boot" aria-busy="true">
          <h1>WaBridge</h1>
          <p className="lede">Starting the engine…</p>
        </article>
      </Shell>
    );
  }

  return (
    <Shell step={step}>
      {step === "start" && <StartStep onNext={setStep} />}
      {step === "android" && <AndroidStep onNext={() => setStep("iphone")} />}
      {step === "iphone" && <IPhoneStep onNext={() => setStep("transfer")} />}
      {step === "transfer" && (
        <TransferStep
          key={mediaPending ? "media" : "first"}
          onDone={(pending) => {
            setMediaPending(pending);
            setStep("done");
          }}
        />
      )}
      {step === "done" && (
        <DoneStep
          mediaPending={mediaPending}
          onMediaPass={() => {
            setMediaPending(false);
            setStep("transfer");
          }}
          onRestart={() => setStep("start")}
        />
      )}
    </Shell>
  );
}
