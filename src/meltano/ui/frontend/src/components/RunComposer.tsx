import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api, type PluginInfo } from "../api";
import { ErrorNotice } from "./Status";

/** Starts a pipeline by choosing an extractor and a loader.
 *
 * The API accepts block *names* only and validates them against the project,
 * so this deliberately offers a pair of selects rather than free text. */
export function RunComposer({
  plugins,
  disabled,
}: {
  plugins: PluginInfo[];
  disabled: boolean;
}) {
  const extractors = useMemo(
    () => plugins.filter((plugin) => plugin.type === "extractors"),
    [plugins],
  );
  const loaders = useMemo(
    () => plugins.filter((plugin) => plugin.type === "loaders"),
    [plugins],
  );

  const [extractor, setExtractor] = useState(extractors[0]?.name ?? "");
  const [loader, setLoader] = useState(loaders[0]?.name ?? "");
  const [fullRefresh, setFullRefresh] = useState(false);
  const [force, setForce] = useState(false);

  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const start = useMutation({
    mutationFn: () =>
      api.startRun({
        blocks: [extractor, loader],
        full_refresh: fullRefresh,
        force,
      }),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${run.run_id}`);
    },
  });

  if (extractors.length === 0 || loaders.length === 0) {
    return (
      <div className="card">
        <h2>Run a pipeline</h2>
        <p className="page-sub">
          You need at least one extractor and one loader. Add them with{" "}
          <code>meltano add tap-github target-jsonl</code>.
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="section-head">
        <h2>Run a pipeline</h2>
      </div>

      <div className="composer">
        <label className="field">
          <span className="field-label">Extract from</span>
          <select
            value={extractor}
            onChange={(event) => setExtractor(event.target.value)}
            disabled={disabled}
          >
            {extractors.map((plugin) => (
              <option key={plugin.name} value={plugin.name}>
                {plugin.name}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field-label">Load into</span>
          <select
            value={loader}
            onChange={(event) => setLoader(event.target.value)}
            disabled={disabled}
          >
            {loaders.map((plugin) => (
              <option key={plugin.name} value={plugin.name}>
                {plugin.name}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          className="btn btn-primary"
          onClick={() => start.mutate()}
          disabled={disabled || start.isPending}
        >
          {start.isPending ? "Starting…" : "Run pipeline"}
        </button>

        <div className="composer-opts">
          <label className="check">
            <input
              type="checkbox"
              checked={fullRefresh}
              onChange={(event) => setFullRefresh(event.target.checked)}
              disabled={disabled}
            />
            Full refresh
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={force}
              onChange={(event) => setForce(event.target.checked)}
              disabled={disabled}
            />
            Force if already running
          </label>
        </div>
      </div>

      {start.isError && (
        <div style={{ marginTop: "16px" }}>
          <ErrorNotice error={start.error} />
        </div>
      )}
    </div>
  );
}
