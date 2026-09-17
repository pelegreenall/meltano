import type { RunStatus } from "../api";

const TONE: Record<RunStatus, { cls: string; label: string }> = {
  starting: { cls: "badge-info badge-running", label: "Starting" },
  running: { cls: "badge-info badge-running", label: "Running" },
  success: { cls: "badge-ok", label: "Succeeded" },
  failed: { cls: "badge-err", label: "Failed" },
  cancelled: { cls: "badge-warn", label: "Cancelled" },
  // The server could not tell what happened to a run that outlived it.
  unknown: { cls: "badge-idle", label: "Unknown" },
};

export function StatusBadge({ status }: { status: RunStatus }) {
  const tone = TONE[status] ?? TONE.unknown;
  return <span className={`badge ${tone.cls}`}>{tone.label}</span>;
}

/** Core's `State` enum, as stored on each `Job` row. */
const JOB_STATE: Record<string, { cls: string; label: string }> = {
  SUCCESS: { cls: "badge-ok", label: "Succeeded" },
  FAIL: { cls: "badge-err", label: "Failed" },
  // A run whose heartbeat stopped. Not a success, and not still going.
  DEAD: { cls: "badge-err", label: "Stalled" },
  RUNNING: { cls: "badge-info badge-running", label: "Running" },
  IDLE: { cls: "badge-idle", label: "Queued" },
};

export function JobStateBadge({ state }: { state: string }) {
  const tone = JOB_STATE[state] ?? { cls: "badge-idle", label: state };
  return <span className={`badge ${tone.cls}`}>{tone.label}</span>;
}

export function Empty({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      <div>{hint}</div>
    </div>
  );
}

export function ErrorNotice({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  const instruction =
    error && typeof error === "object" && "instruction" in error
      ? ((error as { instruction: string | null }).instruction ?? null)
      : null;

  return (
    <div className="notice" role="alert">
      <div>{message}</div>
      {instruction && <div className="notice-instruction">{instruction}</div>}
    </div>
  );
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="card" style={{ display: "grid", gap: "12px" }} aria-busy="true">
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="skeleton"
          style={{ width: `${100 - index * 14}%` }}
        />
      ))}
    </div>
  );
}
