/** Shapes returned by the local server. Mirrors src/cccenter/{scanner,analysis}.py. */

export interface Commit {
  sha: string;
  short: string;
  at: string | null;
  author: string;
  subject: string;
  files: string[];
  added: number;
  removed: number;
}

export interface FileEdit {
  n: number;
  a: number;
  d: number;
}

/** One question, plus everything the agent did until you spoke again. */
export interface Turn {
  kind: "human" | "slash";
  text: string;
  ts: string | null;
  end: string | null;
  files: Record<string, FileEdit>;
  commands: string[];
  commit_msgs: string[];
  commits: Commit[];
  tools: Record<string, number>;
  subagents: string[];
}

export interface DaySlice {
  start: string | null;
  end: string | null;
  active: number;
  assistant_turns: number;
  prompts: string[];
  prompt_count: number;
  slash: Record<string, number>;
  files: Record<string, number>;
  added: number;
  removed: number;
  scratch: Record<string, number>;
  commands: string[];
  commits: string[];
  tools: Record<string, number>;
  subagents: Record<string, number>;
  ide_files: Record<string, number>;
  artifacts: { title: string | null; url: string }[];
  turns: Turn[];
  tok_in: number;
  tok_out: number;
  cache_read: number;
  cache_write: number;
  errors: number;
  cost: number;
}

export type TailState = "waiting" | "tool" | "running";

export interface Tail {
  state: TailState;
  tool: string | null;
  since: string;
  at: string;
  pending?: number;
  interrupted?: boolean;
}

export interface Attention {
  kind: "waiting" | "permission" | "stuck" | "died";
  label: string;
  tool: string | null;
  since: string;
}

/** What the last API call sent as input: the context as it stands right now. */
export interface Context {
  used: number;
  size: number;
  model: string | null;
  at: string | null;
}

export interface Session {
  host: string;
  file: string;
  session_id: string;
  cwd: string | null;
  branch: string | null;
  version: string | null;
  entrypoint: string | null;
  sidechain: boolean;
  ai_title: string | null;
  summary: string | null;
  last_prompt: string | null;
  models: Record<string, number>;
  cost_usd: number | null;
  lines_added_total: number | null;
  tail: Tail | null;
  ctx: Context | null;
  days: Record<string, DaySlice>;
  title: string;
  alive: boolean | null;
  attention: Attention | null;
}

export interface Project {
  dir: string;
  cwd: string;
  resolved: boolean;
  sessions: number;
  last_active: number;
  exists: boolean;
  is_git: boolean;
  host: string;
}

/** A journal entry or a note. The database is the record; markdown is a copy. */
export interface Entry {
  id: number;
  kind: "journal" | "note" | "mindmap";
  cwd: string;
  host: string;
  ref: string;
  day: string;
  /** Notes carry their own title; a journal's title is its date. */
  title: string;
  body: string;
  created_at: string;
  updated_at: string;
  exported_at: string | null;
  exported_path: string | null;
  /** The row's id in Supabase, once it has one. */
  cloud_id: string | null;
  /** When the cloud last confirmed this row; null means it never has. */
  synced_at: string | null;
}

export interface Uncommitted {
  path: string;
  still_dirty: boolean;
  a: number;
  d: number;
  turns: number[];
}

/** What changed on one day in one project. Computed, never generated. */
export interface DayChanges {
  day: string;
  active: number;
  sessions: string[];
  commits: Commit[];
  linked: number;
  loose: number;
  commit_files: number;
  commit_added: number;
  commit_removed: number;
  uncommitted: Uncommitted[];
  turn_stats: { with_commit: number; touched_only: number; looked_only: number };
  has_git: boolean;
}

export interface HostState {
  host: string;
  status: "ok" | "error" | string;
  at: number;
  n?: number;
  ms: number | null;
  error?: string;
  local?: boolean;
  busy: boolean;
  procs: number | null;
}

export interface LimitWindow {
  used_percentage: number;
  resets_at: number | null;
}

/**
 * The subscription's rolling limits, as Claude Code last reported them to its
 * status line. Account-wide, so one snapshot covers every machine.
 */
export interface PlanUsage {
  at: number;
  host: string;
  model: string | null;
  rate_limits: { five_hour?: LimitWindow; seven_day?: LimitWindow };
}

/** One day's API traffic, priced at list. Every day on record, whatever the range. */
export interface DailyTotal {
  calls: number;
  in: number;
  out: number;
  cache_read: number;
  cache_write: number;
  cost: number;
  models: Record<string, number>;
  hosts: Record<string, number>;
}

export interface Report {
  rev: number;
  window: { since: string; until: string; tz: string; days: string[] | null } | null;
  usage: PlanUsage | null;
  daily: Record<string, DailyTotal>;
  host_list: string[];
  generated_at: string;
  server_now: number;
  local_host: string;
  hosts: HostState[];
  last_local: number;
  last_remote: number;
  repo_commits: Record<string, Record<string, Commit[]>>;
  projects: Project[];
  entries: Record<string, Entry[]>;
  store: {
    journals: number; notes: number; projects: number; path: string;
    /** Epoch seconds of the last successful pull from Supabase. */
    synced_at: number | null;
    /** Rows only this machine has, waiting to go up. */
    pending: number;
  };
  auth: Auth;
  changes: Record<string, Record<string, DayChanges>>;
  sessions: Session[];
}

export interface Settings {
  tz: string;
  days: number;
  date: string;
  include_local: boolean;
  disabled_hosts: string[];
  sidechains: boolean;
  oneshot: boolean;
  prompts: number;
  entrypoints: string[];
  ssh_timeout: number;
  remote_enabled: boolean;
  sidebar_w: number;
  local_poll: number;
  remote_poll: number;
  remote_poll_hot: number;
  live_window: number;
  theme: string;
  browser: string;
  notify_scope: "off" | "remote" | "all";
  notify_sound: boolean;
  notify_waiting_after: number;
  notify_tool_after: number;
  notify_stuck_after: number;
  attention_max: number;
  journal_auto: boolean;
  journal_at: string;
  journal_model: string;
  journal_input_max: number;
}

export interface LogLine {
  t: number;
  level: "info" | "warn" | "error";
  msg: string;
  host: string | null;
}

/**
 * Who this machine is signed in as. What you wrote lives in Supabase, so the
 * page is a sign-in form until `signed_in` is true; nothing else is served.
 */
export interface Auth {
  /** SUPABASE_URL and SUPABASE_ANON_KEY are set in .env. */
  configured: boolean;
  /** The Supabase project's host, for the sign-in screen. */
  project: string | null;
  signed_in: boolean;
  email: string | null;
  synced_at: number | null;
  pending: number;
  /** What the last sync said went wrong, if anything. */
  error: string | null;
}

/** What /api/state returns before sign-in: enough for the sign-in screen. */
export interface LockedState {
  auth: Auth;
  cwd: string;
  pid: number;
  started: number;
}

export interface AppState {
  auth: Auth;
  settings: Settings;
  hosts: string[];
  local_host: string;
  claude: boolean;
  cwd: string;
  pid: number;
  started: number;
  log: LogLine[];
  report: Report;
}

export type PaneName = "agents" | "projects" | "sessions" | "activity" | "claude" | "settings";

/**
 * One CLAUDE.md as it sits on disk: the global one on a machine, or a
 * project's `.claude/CLAUDE.md`. The file is the truth; nothing is cached.
 */
export interface ClaudeFile {
  scope: "global" | "project";
  host: string;
  /** null for the global file. */
  cwd: string | null;
  path: string;
  exists: boolean;
  body: string;
  mtime: number | null;
  /** Why it could not be read, if it could not. */
  error: string | null;
  last_active: number;
}
