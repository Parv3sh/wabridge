import { useId, useState, type ReactNode } from "react";
import type { EngineError } from "../types";

// ---------------------------------------------------------------- buttons

type ButtonKind = "primary" | "secondary" | "quiet" | "danger";

export function Button({
  kind = "secondary",
  disabled,
  busy,
  onClick,
  children,
  type = "button",
}: {
  kind?: ButtonKind;
  disabled?: boolean;
  busy?: boolean;
  onClick?: () => void;
  children: ReactNode;
  type?: "button" | "submit";
}) {
  return (
    <button type={type} className={`btn btn-${kind}`} disabled={disabled || busy} onClick={onClick} aria-busy={busy || undefined}>
      {busy && <span className="spinner" aria-hidden="true" />}
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- notices

export function Notice({
  tone = "info",
  title,
  children,
  actions,
}: {
  tone?: "info" | "warn" | "error" | "ok";
  title?: string;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className={`notice notice-${tone}`} role={tone === "error" ? "alert" : "status"}>
      <span className={`lamp lamp-${tone}`} aria-hidden="true" />
      <div className="notice-body">
        {title && <p className="notice-title">{title}</p>}
        {children && <div className="notice-text">{children}</div>}
        {actions && <div className="notice-actions">{actions}</div>}
      </div>
    </div>
  );
}

export function ErrorNotice({ error, actions }: { error: EngineError; actions?: ReactNode }) {
  return (
    <Notice tone="error" title={error.message} actions={actions}>
      {error.hint && <p>{error.hint}</p>}
    </Notice>
  );
}

// ---------------------------------------------------------------- progress

export function ProgressBar({ pct, label }: { pct: number | null; label?: string }) {
  const known = pct !== null;
  return (
    <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={known ? Math.round(pct) : undefined} aria-label={label}>
      <div className={`progress-track${known ? "" : " progress-indeterminate"}`}>
        <div className="progress-fill" style={known ? { width: `${Math.max(2, Math.min(100, pct))}%` } : undefined} />
      </div>
      <div className="progress-meta">
        <span className="progress-label">{label}</span>
        {known && <span className="progress-pct">{Math.round(pct)}%</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- device "berth"

export type LampState = "off" | "wait" | "ok" | "warn" | "error";

export function Berth({
  kind,
  lamp,
  title,
  detail,
}: {
  kind: "android" | "iphone";
  lamp: LampState;
  title: string;
  detail?: ReactNode;
}) {
  return (
    <div className="berth">
      <PhoneGlyph kind={kind} lit={lamp === "ok"} />
      <div className="berth-text">
        <p className="berth-title">
          <span className={`lamp lamp-${lamp}`} aria-hidden="true" />
          {title}
        </p>
        {detail && <p className="berth-detail">{detail}</p>}
      </div>
    </div>
  );
}

function PhoneGlyph({ kind, lit }: { kind: "android" | "iphone"; lit: boolean }) {
  // Two silhouettes drawn with the same stroke so they read as a pair across the bridge.
  return (
    <svg className={`phone phone-${kind}${lit ? " phone-lit" : ""}`} viewBox="0 0 28 48" width="28" height="48" aria-hidden="true">
      <rect x="1.5" y="1.5" width="25" height="45" rx={kind === "iphone" ? 6 : 3.5} />
      {kind === "iphone" ? <rect x="9" y="4" width="10" height="3" rx="1.5" className="phone-notch" /> : <circle cx="14" cy="5.5" r="1.4" className="phone-notch" />}
      <rect x="4" y="9" width="20" height="30" rx="1" className="phone-screen" />
    </svg>
  );
}

// ---------------------------------------------------------------- fields

export function SecretField({
  label,
  value,
  onChange,
  placeholder,
  hint,
  invalid,
  autoFocus,
  onSubmit,
  onBlur,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  hint?: string;
  invalid?: string | null;
  autoFocus?: boolean;
  onSubmit?: () => void;
  onBlur?: () => void;
}) {
  const id = useId();
  const [show, setShow] = useState(false);
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className="field-row">
        <input
          id={id}
          className={`input mono${invalid ? " input-invalid" : ""}`}
          type={show ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          onKeyDown={(e) => {
            if (e.key === "Enter" && onSubmit) onSubmit();
          }}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          autoFocus={autoFocus}
          aria-invalid={!!invalid}
          aria-describedby={hint || invalid ? `${id}-hint` : undefined}
        />
        <button type="button" className="btn btn-quiet btn-small" onClick={() => setShow((s) => !s)} aria-pressed={show}>
          {show ? "Hide" : "Show"}
        </button>
      </div>
      {(invalid || hint) && (
        <p id={`${id}-hint`} className={`field-hint${invalid ? " field-hint-invalid" : ""}`}>
          {invalid || hint}
        </p>
      )}
    </div>
  );
}

export function Choice({
  name,
  value,
  onChange,
  options,
}: {
  name: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string; detail?: ReactNode; disabled?: boolean }[];
}) {
  return (
    <div className="choice" role="radiogroup">
      {options.map((o) => (
        <label key={o.value} className={`choice-item${o.disabled ? " choice-disabled" : ""}`}>
          <input type="radio" name={name} value={o.value} checked={value === o.value} disabled={o.disabled} onChange={() => onChange(o.value)} />
          <span className="choice-text">
            <span className="choice-label">{o.label}</span>
            {o.detail && <span className="choice-detail">{o.detail}</span>}
          </span>
        </label>
      ))}
    </div>
  );
}

export function Check({ checked, onChange, label, detail }: { checked: boolean; onChange: (v: boolean) => void; label: string; detail?: ReactNode }) {
  return (
    <label className="choice-item">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="choice-text">
        <span className="choice-label">{label}</span>
        {detail && <span className="choice-detail">{detail}</span>}
      </span>
    </label>
  );
}

// ---------------------------------------------------------------- instruction lists

export function Steps({ items }: { items: ReactNode[] }) {
  // A real sequence of taps on a phone — numbering is information here, not decoration.
  return (
    <ol className="steps">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ol>
  );
}

export function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}
