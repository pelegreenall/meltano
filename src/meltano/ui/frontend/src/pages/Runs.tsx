import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api";
import { Empty, ErrorNotice, Loading, StatusBadge } from "../components/Status";
import { formatDuration, formatWhen } from "../format";
import { pipelineOf } from "./Overview";

export function Runs() {
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    refetchInterval: 4_000,
  });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Runs</h1>
          <p className="page-sub">
            Every run this server has supervised. Runs keep going if you close
            the browser, and survive restarting the server.
          </p>
        </div>
      </div>

      {runs.isError ? (
        <ErrorNotice error={runs.error} />
      ) : runs.isLoading ? (
        <Loading rows={4} />
      ) : (runs.data?.length ?? 0) === 0 ? (
        <div className="table-wrap">
          <Empty
            title="No runs recorded"
            hint="Start a pipeline from the overview page to see it here."
          />
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Pipeline</th>
                <th>Status</th>
                <th>Environment</th>
                <th>Started</th>
                <th className="cell-num">Duration</th>
                <th className="cell-num">Exit</th>
              </tr>
            </thead>
            <tbody>
              {runs.data!.map((run) => (
                <tr key={run.run_id}>
                  <td>
                    <Link className="row-link" to={`/runs/${run.run_id}`}>
                      {pipelineOf(run.argv)}
                    </Link>
                    <div className="cell-mono">{run.run_id.slice(0, 8)}</div>
                  </td>
                  <td>
                    <StatusBadge status={run.status} />
                  </td>
                  <td>
                    {run.environment ? (
                      <span className="tag">{run.environment}</span>
                    ) : (
                      <span className="cell-mono">—</span>
                    )}
                  </td>
                  <td className="cell-mono">{formatWhen(run.started_at)}</td>
                  <td className="cell-num cell-mono">
                    {formatDuration(run.started_at, run.finished_at)}
                  </td>
                  <td className="cell-num cell-mono">
                    {run.exit_code ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
