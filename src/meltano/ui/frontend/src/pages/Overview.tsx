import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api";
import type { RunInfo } from "../api";
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
                        {pipelineOf(run)}
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

/**
 * Recover the block names from a state ID.
 *
 * `generate_state_id` builds these as `{environment}:{tap}-to-{target}` with
 * an optional trailing suffix component, so the middle component carries the
 * names. A state ID that does not follow the shape is returned unchanged
 * rather than mangled.
 */
function blocksOfStateId(stateId: string): string {
  const components = stateId.split(":");
  const pair = components.length > 1 ? components[1] : components[0];

  // Split on the first `-to-`: core joins the two names with it, so a plugin
  // whose own name contains `-to-` is inherently ambiguous either way.
  const at = pair.indexOf("-to-");
  if (at === -1) return pair;
  return `${pair.slice(0, at)} → ${pair.slice(at + 4)}`;
}

/** The plugin names following a subcommand, ignoring its flags. */
function argsAfter(argv: string[], token: string): string[] {
  const at = argv.indexOf(token);
  if (at === -1) return [];
  return argv.slice(at + 1).filter((arg) => !arg.startsWith("--"));
}

/**
 * Name a run for display.
 *
 * Not every supervised task is a pipeline: installing and testing a plugin go
 * through the same machinery, and naming them from their blocks would render
 * the interpreter's own argv. Runs known only from the system database have no
 * argv at all, so their name is recovered from the state IDs of the rows they
 * produced - in the same shape, so one pipeline does not read differently
 * depending on who started it.
 */
export function pipelineOf(
  run: Pick<RunInfo, "argv" | "jobs" | "kind">,
): string {
  if (run.kind === "install") {
    const names = argsAfter(run.argv, "install");
    return names.length > 0 ? `Install ${names.join(" ")}` : "Install";
  }

  if (run.kind === "test") {
    // `config --plugin-type=... test <name>`, so the name follows "test".
    const names = argsAfter(run.argv, "test");
    return names.length > 0 ? `Test ${names.join(" ")}` : "Test connection";
  }

  const names = argsAfter(run.argv, "run");
  if (names.length > 0) return names.join(" → ");

  const fromState = run.jobs.map((job) => blocksOfStateId(job.job_name));
  return fromState.length > 0 ? fromState.join(", ") : "pipeline";
}
