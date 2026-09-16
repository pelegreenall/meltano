import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { api, type RunStatus } from "../api";
import { LogViewer } from "../components/LogViewer";
import { ErrorNotice, Loading, StatusBadge } from "../components/Status";
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
          <h1>{pipelineOf(data.argv)}</h1>
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
          <span className="stat-value is-text">{data.environment ?? "none"}</span>
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
        <LogViewer runId={runId} onFinished={handleFinished} />
      </div>

      <div className="section">
        <div className="section-head">
          <h2>Details</h2>
        </div>
        <div className="card">
          <div className="meta-grid">
            <span className="meta-key">Command</span>
            <span className="meta-val">{data.argv.join(" ")}</span>
            <span className="meta-key">Log file</span>
            <span className="meta-val">{data.log_path}</span>
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
