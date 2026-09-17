import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { api, type SetupState } from "../api";
import { ErrorNotice } from "../components/Status";

/**
 * Wait for the server to come back serving a project, then reload.
 *
 * Choosing a project stops the setup server; a fresh one starts in its place
 * on the same port with the same token. `/project` is the positive signal that
 * the swap is done - it exists only on the serving app - so this polls for it
 * rather than guessing at a delay.
 */
async function waitForProject(): Promise<void> {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch("/api/v1/project", {
        credentials: "same-origin",
      });
      if (response.ok) {
        window.location.assign("/");
        return;
      }
    } catch {
      /* The old server has stopped and the new one is not listening yet. */
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  // Out of patience rather than out of luck: reload anyway and let the app
  // report whatever it finds.
  window.location.assign("/");
}

export function Setup({ state }: { state: SetupState }) {
  const [path, setPath] = useState("");
  const [force, setForce] = useState(false);

  const create = useMutation({
    mutationFn: () => api.createProject({ path: path.trim(), force }),
    onSuccess: waitForProject,
  });

  const open = useMutation({
    mutationFn: (target: string) => api.openProject(target),
    onSuccess: waitForProject,
  });

  const busy = create.isPending || open.isPending;
  const settled = create.isSuccess || open.isSuccess;

  if (settled) {
    return (
      <div className="setup">
        <div className="card">
          <h1>Starting up</h1>
          <p className="page-sub">
            {(create.data ?? open.data)!.detail} This page will reload itself.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="setup">
      <div className="page-head">
        <div>
          <h1>Choose a project</h1>
          <p className="page-sub">
            This server was started outside a Meltano project. Point it at one
            and it will restart to serve it.
          </p>
        </div>
      </div>

      {state.readonly && (
        <div className="notice" role="status">
          <div>This server is running in read-only mode.</div>
          <div className="notice-instruction">
            It can open an existing project but not create one. Restart without{" "}
            <code>--readonly</code> to create.
          </div>
        </div>
      )}

      {state.candidates.length > 0 && (
        <div className="section">
          <div className="section-head">
            <h2>Found nearby</h2>
          </div>
          <div className="table-wrap">
            <table>
              <tbody>
                {state.candidates.map((candidate) => (
                  <tr key={candidate.path}>
                    <td>
                      <span className="row-link">{candidate.name}</span>
                      <div className="cell-mono">{candidate.path}</div>
                    </td>
                    <td className="cell-num">
                      <button
                        type="button"
                        className="btn btn-sm btn-primary"
                        onClick={() => open.mutate(candidate.path)}
                        disabled={busy}
                      >
                        {open.isPending ? "Opening…" : "Open"}
                      </button>
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
          <h2>{state.readonly ? "Open a project" : "Create a project"}</h2>
        </div>
        <div className="card">
          <div className="fields">
            <label className="field">
              <span className="field-label">Directory</span>
              <input
                type="text"
                value={path}
                placeholder="~/projects/analytics"
                onChange={(event) => setPath(event.target.value)}
              />
              <span className="field-help">
                Absolute, or relative to <code>{state.cwd}</code>.
              </span>
            </label>

            {!state.readonly && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={force}
                  onChange={(event) => setForce(event.target.checked)}
                />
                Overwrite an existing <code>meltano.yml</code> there
              </label>
            )}
          </div>

          <div className="actions-row" style={{ marginTop: "var(--s4)" }}>
            {!state.readonly && (
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => create.mutate()}
                disabled={busy || path.trim() === ""}
              >
                {create.isPending ? "Creating…" : "Create project"}
              </button>
            )}
            <button
              type="button"
              className="btn"
              onClick={() => open.mutate(path.trim())}
              disabled={busy || path.trim() === ""}
            >
              {open.isPending ? "Opening…" : "Open existing"}
            </button>
          </div>

          {(create.isError || open.isError) && (
            <div style={{ marginTop: "var(--s4)" }}>
              <ErrorNotice error={create.error ?? open.error} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
