import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api, type JobInfo } from "../api";
import { Empty, ErrorNotice, Loading } from "../components/Status";

/**
 * Render a job's tasks the way they are declared.
 *
 * A task is either a block string or an already-split list, so both shapes
 * come back to one line apiece - matching what `meltano job add --tasks`
 * accepts and what the editor below writes.
 */
function taskLines(tasks: JobInfo["tasks"]): string {
  return tasks
    .map((task) => (Array.isArray(task) ? task.join(" ") : task))
    .join("\n");
}

/** Parse the editor's textarea back into the API's task list. */
function parseTasks(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function JobEditor({
  editing,
  onDone,
}: {
  editing: JobInfo | null;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(editing?.name ?? "");
  const [tasks, setTasks] = useState(editing ? taskLines(editing.tasks) : "");

  const save = useMutation({
    mutationFn: () =>
      editing
        ? api.updateJob(editing.name, { tasks: parseTasks(tasks) })
        : api.createJob({ name: name.trim(), tasks: parseTasks(tasks) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      onDone();
    },
  });

  const parsed = parseTasks(tasks);
  const canSave = parsed.length > 0 && (editing !== null || name.trim() !== "");

  return (
    <div className="card">
      <div className="section-head">
        <h2>{editing ? `Edit ${editing.name}` : "Define a job"}</h2>
      </div>

      <div className="fields">
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
          <span className="field-label">Tasks</span>
          <textarea
            rows={4}
            value={tasks}
            placeholder={"tap-github target-jsonl\ndbt-postgres:run"}
            onChange={(event) => setTasks(event.target.value)}
          />
          <span className="field-help">
            One task per line, blocks separated by spaces. Every block must
            already be declared in <code>meltano.yml</code>.
          </span>
        </label>
      </div>

      <div className="actions-row" style={{ marginTop: "var(--s4)" }}>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => save.mutate()}
          disabled={!canSave || save.isPending}
        >
          {save.isPending ? "Saving…" : editing ? "Save changes" : "Create job"}
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

function JobRow({ job }: { job: JobInfo }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);

  // A job name is a valid block, so running one needs no special endpoint.
  const run = useMutation({
    mutationFn: () => api.startRun({ blocks: [job.name] }),
    onSuccess: (started) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${started.run_id}`);
    },
  });

  const remove = useMutation({
    mutationFn: () => api.deleteJob(job.name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });

  if (editing) {
    return (
      <tr>
        <td colSpan={3}>
          <JobEditor editing={job} onDone={() => setEditing(false)} />
        </td>
      </tr>
    );
  }

  return (
    <>
      <tr>
        <td>
          <span className="row-link">{job.name}</span>
          <div className="cell-mono">{job.blocks.join(" → ")}</div>
        </td>
        <td>
          <div className="job-tasks">
            {job.tasks.map((task, index) => (
              <code key={index}>
                {Array.isArray(task) ? task.join(" ") : task}
              </code>
            ))}
          </div>
        </td>
        <td className="cell-num">
          <div className="actions-row">
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={() => run.mutate()}
              disabled={run.isPending}
            >
              {run.isPending ? "Starting…" : "Run"}
            </button>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setEditing(true)}
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
          <td colSpan={3}>
            {confirming && (
              <div className="confirm">
                <span>
                  Remove <strong>{job.name}</strong> from{" "}
                  <code>meltano.yml</code>? Runs it already produced are kept.
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
                    Delete job
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

export function Jobs() {
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.jobs });
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const [creating, setCreating] = useState(false);

  const readonly = meta.data?.readonly ?? false;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Jobs</h1>
          <p className="page-sub">
            Named pipelines declared in <code>meltano.yml</code>. A job can be
            run by name, and is what a schedule points at.
          </p>
        </div>
        {!creating && !readonly && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setCreating(true)}
          >
            Define a job
          </button>
        )}
      </div>

      {creating && (
        <div className="section">
          <JobEditor editing={null} onDone={() => setCreating(false)} />
        </div>
      )}

      {jobs.isError ? (
        <ErrorNotice error={jobs.error} />
      ) : jobs.isLoading ? (
        <Loading rows={3} />
      ) : (jobs.data?.length ?? 0) === 0 ? (
        <div className="table-wrap">
          <Empty
            title="No jobs defined"
            hint="Define one here, or run `meltano job add NAME --tasks 'tap-github target-jsonl'`."
          />
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Tasks</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.data!.map((job) => (
                <JobRow key={job.name} job={job} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
