import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { api, type StateSummary } from "../api";
import { Empty, ErrorNotice, Loading } from "../components/Status";

/** Editor and viewer for one state ID's payload, loaded on demand. */
function StatePanel({ stateId }: { stateId: string }) {
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["state", stateId],
    queryFn: () => api.stateDetail(stateId),
  });

  const [draft, setDraft] = useState<string | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (parsed: Record<string, unknown>) =>
      api.setState(stateId, parsed),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["state"] });
      setDraft(null);
    },
  });

  if (detail.isLoading) return <Loading rows={2} />;
  if (detail.isError) return <ErrorNotice error={detail.error} />;

  const stored = JSON.stringify(detail.data!.state, null, 2);

  const submit = () => {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(draft ?? stored);
    } catch (error) {
      // Caught here so a typo does not become a server round trip and a
      // less specific error message.
      setParseError(error instanceof Error ? error.message : String(error));
      return;
    }
    setParseError(null);
    save.mutate(parsed);
  };

  return (
    <div className="card">
      {draft === null ? (
        <>
          <pre className="state-payload">{stored || "{}"}</pre>
          <div className="actions-row" style={{ marginTop: "var(--s3)" }}>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setDraft(stored)}
            >
              Edit bookmarks
            </button>
          </div>
        </>
      ) : (
        <>
          <label className="field">
            <span className="field-label">Singer state</span>
            <textarea
              rows={12}
              value={draft}
              spellCheck={false}
              onChange={(event) => setDraft(event.target.value)}
            />
            <span className="field-help">
              Must have a top-level <code>singer_state</code> key. Saving
              replaces the current bookmarks; the old ones are not recoverable.
            </span>
          </label>
          <div className="actions-row" style={{ marginTop: "var(--s3)" }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={submit}
              disabled={save.isPending}
            >
              {save.isPending ? "Saving…" : "Overwrite bookmarks"}
            </button>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                setDraft(null);
                setParseError(null);
              }}
            >
              Cancel
            </button>
          </div>
          {parseError && (
            <div className="notice" role="alert" style={{ marginTop: "var(--s3)" }}>
              <div>Not valid JSON: {parseError}</div>
            </div>
          )}
          {save.isError && (
            <div style={{ marginTop: "var(--s3)" }}>
              <ErrorNotice error={save.error} />
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StateRow({ entry }: { entry: StateSummary }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const clear = useMutation({
    mutationFn: () => api.clearState(entry.state_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["state"] });
      queryClient.invalidateQueries({ queryKey: ["state", entry.state_id] });
    },
  });

  return (
    <>
      <tr>
        <td>
          <span className="row-link">{entry.state_id}</span>
        </td>
        <td>
          {entry.streams.length === 0 ? (
            <span className="cell-mono">
              {entry.has_state ? "no streams bookmarked" : "—"}
            </span>
          ) : (
            <div className="job-tasks">
              {entry.streams.map((stream) => (
                <code key={stream}>{stream}</code>
              ))}
            </div>
          )}
        </td>
        <td className="cell-num">
          <div className="actions-row">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setOpen((current) => !current)}
            >
              {open ? "Hide" : "Inspect"}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-danger"
              onClick={() => setConfirming(true)}
              disabled={!entry.has_state || clear.isPending}
            >
              Clear
            </button>
          </div>
        </td>
      </tr>

      {(open || confirming || clear.isError) && (
        <tr>
          <td colSpan={3}>
            {confirming && (
              <div className="confirm">
                <span>
                  Clear the bookmarks for <strong>{entry.state_id}</strong>? The
                  next run re-extracts everything from the beginning, and the
                  current bookmarks cannot be recovered.
                </span>
                <div className="actions-row">
                  <button
                    type="button"
                    className="btn btn-sm btn-danger"
                    onClick={() => {
                      setConfirming(false);
                      clear.mutate();
                    }}
                  >
                    Clear bookmarks
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => setConfirming(false)}
                  >
                    Keep them
                  </button>
                </div>
              </div>
            )}
            {clear.isError && <ErrorNotice error={clear.error} />}
            {open && <StatePanel stateId={entry.state_id} />}
          </td>
        </tr>
      )}
    </>
  );
}

export function State() {
  // Read from the URL so a run's Blocks table can link straight to the state
  // its blocks bookmarked.
  const [params, setParams] = useSearchParams();
  const pattern = params.get("pattern") ?? "";

  const state = useQuery({
    queryKey: ["state", { pattern }],
    queryFn: () => api.state(pattern || undefined),
  });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>State</h1>
          <p className="page-sub">
            Where each incremental pipeline left off. Clearing a state ID makes
            the next run start from the beginning — the same thing{" "}
            <code>--full-refresh</code> does for a single run.
          </p>
        </div>
      </div>

      <div className="card">
        <label className="field">
          <span className="field-label">Filter</span>
          <input
            type="text"
            value={pattern}
            placeholder="dev:*"
            onChange={(event) => {
              const next = event.target.value;
              setParams(next ? { pattern: next } : {}, { replace: true });
            }}
          />
          <span className="field-help">
            A glob over state IDs. Each one is read from the state backend, so
            narrowing helps on a project with many pipelines.
          </span>
        </label>
      </div>

      {state.isError ? (
        <ErrorNotice error={state.error} />
      ) : state.isLoading ? (
        <Loading rows={3} />
      ) : (state.data?.length ?? 0) === 0 ? (
        <div className="table-wrap">
          <Empty
            title={pattern ? "No state IDs match" : "No state recorded"}
            hint={
              pattern
                ? "Clear the filter to see every state ID."
                : "Incremental pipelines record their bookmarks here after their first run."
            }
          />
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>State ID</th>
                <th>Bookmarked streams</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {state.data!.map((entry) => (
                <StateRow key={entry.state_id} entry={entry} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
