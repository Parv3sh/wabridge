/** Mirror of the JSON-lines protocol in `src/wabridge/serve.py`. Keep the two in sync. */

export type LogLevel = "debug" | "info" | "warning" | "error";

export interface LogEvent {
  type: "log";
  id: string | null;
  level: LogLevel;
  text: string;
  logger?: string;
}
export interface ProgressEvent {
  type: "progress";
  id: string;
  stage: string;
  pct: number;
  detail?: string;
}
export interface DataEvent {
  type: "data";
  id: string;
  key: string;
  value: unknown;
}
export interface ResultEvent {
  type: "result";
  id: string;
  data: Record<string, unknown>;
}
export interface ErrorEvent {
  type: "error";
  id: string | null;
  code: string;
  message: string;
  hint?: string;
  trace?: string;
}
export interface HelloEvent {
  type: "hello";
  version: string;
  work: string;
  pid: number;
}
export type EngineEvent = LogEvent | ProgressEvent | DataEvent | ResultEvent | ErrorEvent | HelloEvent;

export class EngineError extends Error {
  code: string;
  hint?: string;
  constructor(code: string, message: string, hint?: string) {
    super(message);
    this.code = code;
    this.hint = hint;
  }
}

// ---- command results ------------------------------------------------------------------

export interface AndroidDevice {
  serial: string;
  state: "device" | "unauthorized" | "offline" | string;
  model: string | null;
}
export interface IPhoneInfo {
  udid: string;
  name: string;
  ios_version: string;
  will_encrypt: boolean;
  disk_capacity: number | null;
  disk_available: number | null;
  disk_used: number | null;
}
export interface Devices {
  android: AndroidDevice[];
  android_error: string | null;
  iphone: IPhoneInfo | null;
  iphone_error: string | null;
  iphone_busy?: boolean;
  adb: string | null;
}
export interface Stages {
  android_decrypt: boolean;
  media: boolean;
  media_skipped: boolean;
  ios_backup: boolean;
  convert: boolean;
  convert_media: boolean;
  inject: boolean;
  ios_restore: boolean;
}
export interface EngineState {
  version: string;
  work: string;
  log_path: string;
  free_bytes: number;
  adb: string | null;
  stages: Stages;
  raw: Record<string, unknown>;
}
export interface AndroidCheck {
  serial: string;
  model: string | null;
  root: string;
  has_crypt15: boolean;
  media_bytes: number | null;
}
export interface ChatSummary {
  name: string;
  messages: number;
  is_group: boolean;
  named: boolean;
}
export interface ArchiveStats {
  chats: number;
  groups: number;
  messages: number;
  media: number;
}
export interface FetchResult {
  stats: ArchiveStats;
  top_chats: ChatSummary[];
  unnamed_chats: number;
  media_bytes: number | null;
}
export interface ConvertResult {
  sessions_created: number;
  sessions_reused: number;
  messages_written: number;
  messages_skipped_duplicate: number;
  media_linked: number;
  media_missing: number;
  warnings: string[];
  media: boolean;
}
