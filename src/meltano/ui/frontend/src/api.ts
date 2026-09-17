/* Typed client for the Meltano UI API.
 *
 * Authentication rides on the session cookie that `/?token=...` sets on first
 * load, so nothing here handles credentials: every request is same-origin and
 * the browser attaches the cookie. That is also what lets `EventSource` work
 * for the run stream, since it cannot send an Authorization header. */

const BASE = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly instruction: string | null;

  constructor(status: number, detail: string, instruction: string | null) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.instruction = instruction;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    let instruction: string | null = null;
    try {
      const body = await response.json();
      // FastAPI validation errors put an array in `detail`; Meltano errors put
      // a string there plus a separate instruction.
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail) && body.detail[0]?.msg)
        detail = body.detail.map((d: { msg: string }) => d.msg).join(", ");
      instruction = body.instruction ?? null;
    } catch {
      /* Response had no JSON body; keep the status line. */
    }
    throw new ApiError(response.status, detail, instruction);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/** An existing project the setup server found nearby. */
export interface ProjectCandidate {
  path: string;
  name: string;
}

/** What a projectless server reports. Absent once a project is being served. */
export interface SetupState {
  cwd: string;
  candidates: ProjectCandidate[];
  readonly: boolean;
}

export interface SetupResult {
  path: string;
  created: boolean;
  detail: string;
}

export interface ServerMeta {
  meltano_version: string;
  project_root: string;
  environment: string | null;
  readonly: boolean;
  host: string;
  port: number;
}

export interface ProjectInfo {
  root: string;
  readonly: boolean;
  environment: string | null;
  environments: string[];
}

export interface PluginInfo {
  name: string;
  type: string;
  label: string | null;
  variant: string | null;
  docs: string | null;
  is_installed: boolean;
}

export type RunStatus =
  | "starting"
  | "running"
  | "success"
  | "failed"
  | "cancelled"
  | "unknown";

/** One `Job` row a run produced. A run has several when it has several blocks. */
export interface RunJobInfo {
  job_name: string;
  state: string;
  started_at: string | null;
  ended_at: string | null;
  trigger: string | null;
}

export interface RunInfo {
  run_id: string;
  kind: string;
  status: RunStatus;
  started_at: string;
  /** Empty for runs this server did not launch. */
  argv: string[];
  /** Null for runs this server did not launch. */
  log_path: string | null;
  environment: string | null;
  pid: number | null;
  finished_at: string | null;
  exit_code: number | null;
  /** The system-database rows this run produced. */
  jobs: RunJobInfo[];
  /** Whether this server can serve the run's output. */
  has_log: boolean;
}

/** A named job declared in `meltano.yml`. Not a `Job` row - see `RunJobInfo`. */
export interface JobInfo {
  name: string;
  tasks: (string | string[])[];
  blocks: string[];
}

/** One packaging of a Hub plugin. */
export interface HubVariant {
  name: string;
  is_default: boolean;
}

/** A plugin listed on Meltano Hub. */
export interface HubPlugin {
  name: string;
  plugin_type: string;
  default_variant: string;
  variants: HubVariant[];
  logo_url: string | null;
  is_added: boolean;
}

/** The outcome of adding a Hub plugin to the project. */
export interface AddedPlugin {
  name: string;
  type: string;
  variant: string | null;
  pip_url: string | null;
  /** The install task, when one was started. */
  run_id: string | null;
}

/** One select pattern, parsed. */
export interface SelectPatternInfo {
  raw: string;
  stream_pattern: string;
  property_pattern: string | null;
  negated: boolean;
  /** False for Meltano's default or an inherited pattern: nothing to delete. */
  removable: boolean;
}

/** The select patterns in effect for an extractor. Read from `meltano.yml`. */
export interface SelectPatterns {
  extractor: string;
  patterns: SelectPatternInfo[];
  environment: string | null;
}

export interface SelectedProperty {
  name: string;
  /** Effective selection: the stream's combined with the property's own. */
  selection: string;
}

export interface SelectedStream {
  name: string;
  selection: string;
  properties: SelectedProperty[];
}

/** What an extractor reports it can produce. Requires running it. */
export interface SelectCatalog {
  extractor: string;
  streams: SelectedStream[];
  patterns: SelectPatternInfo[];
  selection_types: string[];
}

/** One state ID's bookmarks, without the payload. */
export interface StateSummary {
  state_id: string;
  has_state: boolean;
  streams: string[];
}

/** One state ID and the payload a run would resume from. */
export interface StateDetail {
  state_id: string;
  state: Record<string, unknown>;
  streams: string[];
}

/**
 * A schedule declared in `meltano.yml`.
 *
 * Meltano declares schedules; an orchestrator (Airflow, Dagster, cron) is what
 * fires them. Nothing in this server runs them on a timer.
 */
export interface ScheduleInfo {
  name: string;
  kind: "job" | "elt";
  /** As declared: a cron expression or an alias like `@daily`. */
  interval: string | null;
  /** Null when the schedule never fires on its own (`@manual`, `@once`, `@none`). */
  cron_interval: string | null;
  env: Record<string, string>;
  job: string | null;
  extractor: string | null;
  loader: string | null;
  transform: string | null;
  /** Only `elt` schedules record this; always null for `job` schedules. */
  last_successful_run_at: string | null;
  /** False for legacy `elt` schedules, which must be run from the CLI. */
  can_run: boolean;
}

export interface StartRunBody {
  blocks: string[];
  full_refresh?: boolean;
  no_state_update?: boolean;
  force?: boolean;
}

export const api = {
  meta: () => request<ServerMeta>("/meta"),
  project: () => request<ProjectInfo>("/project"),
  plugins: () => request<PluginInfo[]>("/plugins"),
  runs: () => request<RunInfo[]>("/runs"),
  run: (id: string) => request<RunInfo>(`/runs/${id}`),
  runLog: (id: string) => request<{ run_id: string; lines: string[] }>(`/runs/${id}/log`),
  startRun: (body: StartRunBody) =>
    request<RunInfo>("/runs", { method: "POST", body: JSON.stringify(body) }),
  cancelRun: (id: string) => request<RunInfo>(`/runs/${id}`, { method: "DELETE" }),
  jobs: () => request<JobInfo[]>("/jobs"),
  job: (name: string) => request<JobInfo>(`/jobs/${name}`),
  createJob: (body: { name: string; tasks: (string | string[])[] }) =>
    request<JobInfo>("/jobs", { method: "POST", body: JSON.stringify(body) }),
  updateJob: (name: string, body: { tasks: (string | string[])[] }) =>
    request<JobInfo>(`/jobs/${name}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteJob: (name: string) =>
    request<void>(`/jobs/${name}`, { method: "DELETE" }),
  schedules: () => request<ScheduleInfo[]>("/schedules"),
  createSchedule: (body: { name: string; job: string; interval: string }) =>
    request<ScheduleInfo>("/schedules", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateSchedule: (
    name: string,
    body: { interval?: string; job?: string },
  ) =>
    request<ScheduleInfo>(`/schedules/${name}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteSchedule: (name: string) =>
    request<void>(`/schedules/${name}`, { method: "DELETE" }),
  runSchedule: (name: string) =>
    request<RunInfo>(`/schedules/${name}/run`, { method: "POST" }),
  state: (pattern?: string) =>
    request<StateSummary[]>(
      pattern ? `/state?pattern=${encodeURIComponent(pattern)}` : "/state",
    ),
  // State IDs contain ':' separators, so every one of these encodes the id
  // rather than interpolating it raw.
  stateDetail: (stateId: string) =>
    request<StateDetail>(`/state/${encodeURIComponent(stateId)}`),
  setState: (stateId: string, state: Record<string, unknown>) =>
    request<StateDetail>(`/state/${encodeURIComponent(stateId)}`, {
      method: "PUT",
      body: JSON.stringify({ state }),
    }),
  clearState: (stateId: string) =>
    request<void>(`/state/${encodeURIComponent(stateId)}`, { method: "DELETE" }),
  select: (type: string, name: string) =>
    request<SelectPatterns>(`/plugins/${type}/${name}/select`),
  // Runs the extractor in discovery mode, so this can be slow or fail in ways
  // the pattern endpoints never do.
  selectCatalog: (type: string, name: string, refresh = false) =>
    request<SelectCatalog>(
      `/plugins/${type}/${name}/select/catalog${refresh ? "?refresh=true" : ""}`,
    ),
  addSelectPattern: (
    type: string,
    name: string,
    body: { streams: string; properties: string; exclude?: boolean },
  ) =>
    request<SelectPatterns>(`/plugins/${type}/${name}/select`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  removeSelectPattern: (type: string, name: string, pattern: string) =>
    request<SelectPatterns>(
      `/plugins/${type}/${name}/select/${encodeURIComponent(pattern)}`,
      { method: "DELETE" },
    ),
  clearSelectPatterns: (type: string, name: string) =>
    request<SelectPatterns>(`/plugins/${type}/${name}/select`, {
      method: "DELETE",
    }),
  // Reaches out to Hub, so this can be slow or fail where the rest cannot.
  hub: (type: string, q?: string) =>
    request<HubPlugin[]>(
      q ? `/hub/${type}?q=${encodeURIComponent(q)}` : `/hub/${type}`,
    ),
  addPlugin: (body: {
    plugin_type: string;
    name: string;
    variant?: string;
    install?: boolean;
  }) =>
    request<AddedPlugin>("/plugins", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // Only a projectless server answers these; a serving one 404s, which is how
  // the app decides which of its two faces to show.
  setupState: () => request<SetupState>("/setup"),
  createProject: (body: { path: string; force?: boolean }) =>
    request<SetupResult>("/setup/create", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  openProject: (path: string) =>
    request<SetupResult>("/setup/open", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
};

export const runEventsUrl = (id: string) => `${BASE}/runs/${id}/events`;

/** One decoded line from a run's output stream. */
export interface LogRecord {
  id: number;
  structured: boolean;
  message: string;
  level: string;
  timestamp: string | null;
  stream: string | null;
  metric: { metric: string; value: number; stream: string | null } | null;
}

interface RawLogPayload {
  structured: boolean;
  message?: string;
  event?: string;
  level?: string;
  timestamp?: string;
  name?: string;
  stream_name?: string | null;
  metric_info?: {
    metric?: string;
    value?: number;
    tags?: { stream?: string };
  };
}

/** Normalize an SSE `log` payload into something renderable.
 *
 * Meltano runs with `--log-format=json`, and core configures Singer SDK
 * plugins to emit structured JSON too, so most lines carry real fields rather
 * than prose. Plain lines still arrive and are passed through. */
export function toLogRecord(id: number, payload: RawLogPayload): LogRecord {
  const metricInfo = payload.metric_info;
  return {
    id,
    structured: Boolean(payload.structured),
    message: payload.event ?? payload.message ?? "",
    level: (payload.level ?? "info").toLowerCase(),
    timestamp: payload.timestamp ?? null,
    stream: payload.stream_name ?? null,
    metric:
      metricInfo?.metric && typeof metricInfo.value === "number"
        ? {
            metric: metricInfo.metric,
            value: metricInfo.value,
            stream: metricInfo.tags?.stream ?? null,
          }
        : null,
  };
}

export interface SettingInfo {
  name: string;
  label: string | null;
  description: string | null;
  kind: string;
  sensitive: boolean;
  required: boolean;
  options: { label?: string; value: unknown }[];
  env: string | null;
  value: unknown;
  source: string;
  is_set: boolean;
}

export interface PluginConfig {
  name: string;
  type: string;
  settings: SettingInfo[];
}

export interface SetSettingResponse {
  name: string;
  store: string;
  source: string;
  is_set: boolean;
  value: unknown;
}

export const configApi = {
  read: (type: string, name: string) =>
    request<PluginConfig>(`/plugins/${type}/${name}/config`),
  set: (type: string, name: string, setting: string, value: unknown) =>
    request<SetSettingResponse>(`/plugins/${type}/${name}/config/${setting}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  unset: (type: string, name: string, setting: string) =>
    request<SetSettingResponse>(`/plugins/${type}/${name}/config/${setting}`, {
      method: "DELETE",
    }),
};

export interface PluginTaskAccepted {
  run_id: string;
  kind: string;
  warning: string | null;
}

export const pluginTasks = {
  install: (type: string, name: string, clean = false) =>
    request<PluginTaskAccepted>(`/plugins/${type}/${name}/install`, {
      method: "POST",
      body: JSON.stringify({ clean }),
    }),
  test: (type: string, name: string) =>
    request<PluginTaskAccepted>(`/plugins/${type}/${name}/test`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
};
