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

export interface RunInfo {
  run_id: string;
  kind: string;
  status: RunStatus;
  argv: string[];
  started_at: string;
  log_path: string;
  environment: string | null;
  pid: number | null;
  finished_at: string | null;
  exit_code: number | null;
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
