import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api, type JobInfo, type ScheduleInfo } from "../api";
import { Empty, ErrorNotice, Loading } from "../components/Status";
import { formatWhen } from "../format";

/** Aliases core accepts, paired with what each one actually means. */
const INTERVALS: { value: string; label: string }[] = [
  { value: "@hourly", label: "Hourly" },
  { value: "@daily", label: "Daily, at midnight" },
  { value: "@weekly", label: "Weekly, Sunday at midnight" },
  { value: "@monthly", label: "Monthly, on the 1st" },
  { value: "@yearly", label: "Yearly, on 1 January" },
  { value: "@manual", label: "Manual only — never fires on its own" },
];

/** Describe when a schedule fires, or that it does not. */
function cadenceOf(schedule: ScheduleInfo): string {
  if (!schedule.cron_interval) return "Manual only";
  if (schedule.interval && schedule.interval.startsWith("@")) {
    const known = INTERVALS.find((entry) => entry.value === schedule.interval);
    if (known) return known.label;
  }
  return schedule.cron_interval;
}

function ScheduleEditor({
  jobs,
  editing,
  onDone,
}: {
  jobs: JobInfo[];
  editing: ScheduleInfo | null;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(editing?.name ?? "");
  const [job, setJob] = useState(editing?.job ?? jobs[0]?.name ?? "");
  const [interval, setInterval] = useState(editing?.interval ?? "@daily");
  // A cron expression is offered alongside the aliases rather than instead of
  // them: most schedules are one of the aliases, and cron is error-prone.
  const [custom, setCustom] = useState(
    editing?.interval && !editing.interval.startsWith("@")
      ? editing.interval
      : "",
  );

  const chosenInterval = custom.trim() !== "" ? custom.trim() : interval;

  const save = useMutation({
    mutationFn: () =>
      editing
        ? api.updateSchedule(editing.name, { interval: chosenInterval, job })
        : api.createSchedule({
            name: name.trim(),
            job,
            interval: chosenInterval,
          }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["schedules"] });
      onDone();
    },
  });

  if (jobs.length === 0) {
    return (
      <div className="card">
        <h2>Define a schedule</h2>
        <p className="page-sub">
          A schedule runs a job, and this project has none yet. Define one on
          the Jobs page first.
        </p>
        <div className="actions-row" style={{ marginTop: "var(--s4)" }}>
          <button type="button" className="btn" onClick={onDone}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  const canSave = job !== "" && (editing !== null || name.trim() !== "");

  return (
    <div className="card">
      <div className="section-head">
        <h2>{editing ? `Edit ${editing.name}` : "Define a schedule"}</h2>
      </div>

      <div className="composer">
        {!editing && (
          <label className="field">
            <span className="field-label">Name</span>
            <input
              type="text"
              value={name}
              placeholder="nightly-github"
              onChange={(event) => setName(event.target.value)}
            />
          </label>
        )}

        <label className="field">
          <span className="field-label">Run job</span>
          <select value={job} onChange={(event) => setJob(event.target.value)}>
            {jobs.map((entry) => (
              <option key={entry.name} value={entry.name}>
                {entry.name}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field-label">Interval</span>
          <select
            value={interval}
            disabled={custom.trim() !== ""}
            onChange={(event) => setInterval(event.target.value)}
          >
            {INTERVALS.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field-label">Or a cron expression</span>
          <input
            type="text"
            value={custom}
            placeholder="*/15 * * * *"
            onChange={(event) => setCustom(event.target.value)}
          />
        </label>
      </div>

      <div className="actions-row" style={{ marginTop: "var(--s4)" }}>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => save.mutate()}
          disabled={!canSave || save.isPending}
        >
          {save.isPending
            ? "Saving…"
            : editing
              ? "Save changes"
              : "Create schedule"}
        </button>
        <button type="button" className="btn" onClick={onDone}>
          Cancel
        </button>
      </div>

      {save.isError && (
        <div style={{ marginTop: "var(--s4)" }}>
          <ErrorNotice error={save.error} />
        </div>
      )}
    </div>
  );
}

function ScheduleRow({
  schedule,
  jobs,
}: {
  schedule: ScheduleInfo;
  jobs: JobInfo[];
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const run = useMutation({
    mutationFn: () => api.runSchedule(schedule.name),
    onSuccess: (started) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${started.run_id}`);
    },
  });

  const remove = useMutation({
    mutationFn: () => api.deleteSchedule(schedule.name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["schedules"] }),
  });

  if (editing) {
    return (
      <tr>
        <td colSpan={4}>
          <ScheduleEditor
            jobs={jobs}
            editing={schedule}
            onDone={() => setEditing(false)}
          />
        </td>
      </tr>
    );
  }

  const isLegacy = schedule.kind === "elt";

  return (
    <>
      <tr>
        <td>
          <span className="row-link">{schedule.name}</span>
          {isLegacy && <span className="tag tag-quiet">legacy elt</span>}
        </td>
        <td>
          {isLegacy ? (
            <span className="cell-mono">
              {schedule.extractor} → {schedule.loader}
            </span>
          ) : (
            <span className="cell-mono">{schedule.job}</span>
          )}
        </td>
        <td>
          <span className="cell-mono">{cadenceOf(schedule)}</span>
          {schedule.last_successful_run_at && (
            <div className="cell-mono">
              last ok {formatWhen(schedule.last_successful_run_at)}
            </div>
          )}
        </td>
        <td className="cell-num">
          <div className="actions-row">
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={() => run.mutate()}
              disabled={!schedule.can_run || run.isPending}
              title={
                schedule.can_run
                  ? undefined
                  : "Legacy elt schedules must be run with `meltano schedule run`"
              }
            >
              {run.isPending ? "Starting…" : "Run now"}
            </button>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setEditing(true)}
              disabled={isLegacy}
              title={
                isLegacy
                  ? "Legacy elt schedules are edited with `meltano schedule set`"
                  : undefined
              }
            >
              Edit
            </button>
            <button
              type="button"
              className="btn btn-sm btn-danger"
              onClick={() => setConfirming(true)}
              disabled={remove.isPending}
            >
              Delete
            </button>
          </div>
        </td>
      </tr>

      {(confirming || run.isError || remove.isError) && (
        <tr>
          <td colSpan={4}>
            {confirming && (
              <div className="confirm">
                <span>
                  Remove <strong>{schedule.name}</strong> from{" "}
                  <code>meltano.yml</code>? The job it points at is kept.
                </span>
                <div className="actions-row">
                  <button
                    type="button"
                    className="btn btn-sm btn-danger"
                    onClick={() => {
                      setConfirming(false);
                      remove.mutate();
                    }}
                  >
                    Delete schedule
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => setConfirming(false)}
                  >
                    Keep it
                  </button>
                </div>
              </div>
            )}
            {run.isError && <ErrorNotice error={run.error} />}
            {remove.isError && <ErrorNotice error={remove.error} />}
          </td>
        </tr>
      )}
    </>
  );
}

export function Schedules() {
  const schedules = useQuery({
    queryKey: ["schedules"],
    queryFn: api.schedules,
  });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.jobs });
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const [creating, setCreating] = useState(false);

  const readonly = meta.data?.readonly ?? false;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Schedules</h1>
          <p className="page-sub">
            When each job should run, declared in <code>meltano.yml</code>.
          </p>
        </div>
        {!creating && !readonly && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setCreating(true)}
          >
            Define a schedule
          </button>
        )}
      </div>

      {/* Stated up front rather than buried: a schedule declared here does
          nothing on its own, and implying otherwise would be the single most
          misleading thing this page could do. */}
      <div className="notice notice-info">
        <div>
          Meltano records these schedules but does not fire them. An
          orchestrator — Airflow, Dagster, or cron — reads them and triggers
          the runs. <strong>Run now</strong> starts one immediately on this
          machine.
        </div>
      </div>

      {creating && (
        <div className="section">
          <ScheduleEditor
            jobs={jobs.data ?? []}
            editing={null}
            onDone={() => setCreating(false)}
          />
        </div>
      )}

      {schedules.isError ? (
        <ErrorNotice error={schedules.error} />
      ) : schedules.isLoading ? (
        <Loading rows={3} />
      ) : (schedules.data?.length ?? 0) === 0 ? (
        <div className="table-wrap">
          <Empty
            title="No schedules defined"
            hint="Define one here, or run `meltano schedule add NAME --job JOB --interval @daily`."
          />
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Runs</th>
                <th>Cadence</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {schedules.data!.map((schedule) => (
                <ScheduleRow
                  key={schedule.name}
                  schedule={schedule}
                  jobs={jobs.data ?? []}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
