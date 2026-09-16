import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api";
import { RunComposer } from "../components/RunComposer";
import { Empty, ErrorNotice, Loading, StatusBadge } from "../components/Status";
import { formatDuration, formatWhen } from "../format";

export function Overview() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: api.plugins });
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    refetchInterval: 4_000,
  });

  if (meta.isError) return <ErrorNotice error={meta.error} />;

  const extractors =
    plugins.data?.filter((plugin) => plugin.type === "extractors").length ?? 0;
  const loaders =
    plugins.data?.filter((plugin) => plugin.type === "loaders").length ?? 0;
  const recent = runs.data?.slice(0, 5) ?? [];
  const lastFailure = runs.data?.find((run) => run.status === "failed");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p className="page-sub">
            {meta.data
              ? meta.data.project_root
              : "Loading the project this server is bound to…"}
          </p>
        </div>
      </div>

      <div className="stat-row">
        <div className="stat">
          <span className="stat-label">Environment</span>
          <span className="stat-value is-text">
            {meta.data?.environment ?? "none"}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Extractors</span>
          <span className="stat-value">{extractors}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Loaders</span>
          <span className="stat-value">{loaders}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Runs this session</span>
          <span className="stat-value">{runs.data?.length ?? 0}</span>
        </div>
      </div>

      <div className="section">
        {plugins.isLoading ? (
          <Loading rows={2} />
        ) : (
          <RunComposer
            plugins={plugins.data ?? []}
            disabled={meta.data?.readonly ?? false}
          />
        )}
      </div>

      {lastFailure && (
        <div className="section">
          <div className="notice">
            <div>
              The most recent failed run exited with code {lastFailure.exit_code}.
            </div>
            <div className="notice-instruction">
              <Link to={`/runs/${lastFailure.run_id}`}>Open its log</Link> to see
              what went wrong.
            </div>
          </div>
        </div>
      )}

      <div className="section">
        <div className="section-head">
          <h2>Recent runs</h2>
          <Link to="/runs">View all</Link>
        </div>

        {runs.isLoading ? (
          <Loading />
        ) : recent.length === 0 ? (
          <div className="table-wrap">
            <Empty
              title="No runs yet"
              hint="Start one above and its output will stream here live."
            />
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Pipeline</th>
                  <th>Status</th>
                  <th>Started</th>
                  <th className="cell-num">Duration</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <Link className="row-link" to={`/runs/${run.run_id}`}>
                        {pipelineOf(run.argv)}
                      </Link>
                    </td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="cell-mono">{formatWhen(run.started_at)}</td>
                    <td className="cell-num cell-mono">
                      {formatDuration(run.started_at, run.finished_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

/** Recover the block names from a stored argv for display. */
export function pipelineOf(argv: string[]): string {
  const blocks = argv.slice(argv.findIndex((arg) => arg === "run") + 1);
  const names = blocks.filter((arg) => !arg.startsWith("--"));
  return names.length > 0 ? names.join(" → ") : "pipeline";
}
