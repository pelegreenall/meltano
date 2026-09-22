import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { api, type RunStatus } from "../api";
import { LogViewer } from "../components/LogViewer";
import {
  Empty,
  ErrorNotice,
  JobStateBadge,
  Loading,
  StatusBadge,
} from "../components/Status";
import { formatDuration } from "../format";
import { pipelineOf } from "./Overview";

const ACTIVE: RunStatus[] = ["starting", "running"];

export function RunDetail() {
  const { runId = "" } = useParams();
  const queryClient = useQueryClient();

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    // Polled only while the run is active; the SSE stream is the live channel.
    refetchInterval: (query) =>
      query.state.data && ACTIVE.includes(query.state.data.status) ? 3_000 : false,
  });

  const cancel = useMutation({
    mutationFn: () => api.cancelRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  const handleFinished = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["run", runId] });
    queryClient.invalidateQueries({ queryKey: ["runs"] });
  }, [queryClient, runId]);

  if (run.isLoading) return <Loading rows={5} />;
  if (run.isError) return <ErrorNotice error={run.error} />;

  const data = run.data!;
  const isActive = ACTIVE.includes(data.status);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{pipelineOf(data)}</h1>
          <p className="page-sub">
            <Link to="/runs">Runs</Link> · <span>{data.run_id}</span>
          </p>
        </div>
        {isActive && (
          <button
            type="button"
            className="btn btn-danger"
            onClick={() => cancel.mutate()}
            disabled={cancel.isPending}
          >
            {cancel.isPending ? "Stopping…" : "Cancel run"}
          </button>
        )}
      </div>

      <div className="stat-row">
        <div className="stat">
          <span className="stat-label">Status</span>
          <span className="stat-value is-text">
            <StatusBadge status={data.status} />
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Duration</span>
          <span className="stat-value">
            {formatDuration(data.started_at, data.finished_at)}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Exit code</span>
          <span className="stat-value">{data.exit_code ?? "—"}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Environment</span>
          <span className="stat-value is-text">
            {/* Only a supervised run can report that it had no environment.
                For one known solely from the database the field was never
                captured, so claiming "none" would be wrong. */}
            {data.environment ?? (data.has_log ? "none" : "unknown")}
          </span>
        </div>
      </div>

      {cancel.isError && (
        <div className="section">
          <ErrorNotice error={cancel.error} />
        </div>
      )}

      <div className="section">
        <div className="section-head">
          <h2>Output</h2>
        </div>
        {data.has_log ? (
          <LogViewer runId={runId} onFinished={handleFinished} />
        ) : (
          <div className="table-wrap">
            <Empty
              title="No output captured"
              hint="This run was recorded in the system database but not started by this server, so its output was never captured here."
            />
          </div>
        )}
      </div>

      {data.jobs.length > 0 && (
        <div className="section">
          <div className="section-head">
            <h2>Blocks</h2>
            <span className="nav-count">{data.jobs.length}</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>State ID</th>
                  <th>Result</th>
                  <th>Trigger</th>
                  <th className="cell-num">Duration</th>
                </tr>
              </thead>
              <tbody>
                {data.jobs.map((job, index) => (
                  <tr key={`${job.job_name}-${index}`}>
                    <td className="cell-mono">
                      <Link
                        to={`/state?pattern=${encodeURIComponent(job.job_name)}`}
                      >
                        {job.job_name}
                      </Link>
                    </td>
                    <td>
                      <JobStateBadge state={job.state} />
                    </td>
                    <td className="cell-mono">{job.trigger ?? "—"}</td>
                    <td className="cell-num cell-mono">
                      {job.started_at
                        ? formatDuration(job.started_at, job.ended_at)
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="section">
        <div className="section-head">
          <h2>Details</h2>
        </div>
        <div className="card">
          <div className="meta-grid">
            <span className="meta-key">Command</span>
            <span className="meta-val">
              {data.argv.length > 0 ? data.argv.join(" ") : "—"}
            </span>
            <span className="meta-key">Log file</span>
            <span className="meta-val">{data.log_path ?? "—"}</span>
            <span className="meta-key">Process ID</span>
            <span className="meta-val">{data.pid ?? "—"}</span>
            <span className="meta-key">Started</span>
            <span className="meta-val">{data.started_at}</span>
          </div>
        </div>
      </div>
    </>
  );
}
