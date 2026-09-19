import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type SelectPatternInfo, type SelectedStream } from "../api";
import { ErrorNotice, Loading } from "./Status";

/**
 * How a selection value should read.
 *
 * These are core's `SelectionType` values. "automatic" is the one worth
 * spelling out: the extractor requires the property (a key, usually) and it
 * comes through whether or not a pattern asks for it.
 */
const SELECTION: Record<string, { cls: string; label: string }> = {
  selected: { cls: "badge-ok", label: "selected" },
  automatic: { cls: "badge-info", label: "always sent" },
  excluded: { cls: "badge-err", label: "excluded" },
  unsupported: { cls: "badge-idle", label: "unsupported" },
};

function SelectionBadge({ selection }: { selection: string }) {
  const tone = SELECTION[selection] ?? { cls: "badge-idle", label: selection };
  return <span className={`badge ${tone.cls}`}>{tone.label}</span>;
}

function PatternList({
  patterns,
  onRemove,
  pending,
}: {
  patterns: SelectPatternInfo[];
  onRemove: (raw: string) => void;
  pending: boolean;
}) {
  if (patterns.length === 0) {
    return (
      <p className="page-sub">
        No patterns, which means the default: everything the extractor offers.
      </p>
    );
  }

  return (
    <div className="pattern-list">
      {patterns.map((pattern) => (
        <span
          key={pattern.raw}
          className={`pattern ${pattern.negated ? "pattern-negated" : ""}`}
          title={
            pattern.removable
              ? undefined
              : "Meltano's default — nothing is declared to remove"
          }
        >
          <code>{pattern.raw}</code>
          {/* A pattern that is not declared has nothing to delete, so it
              carries a marker instead of a control that would 404. */}
          {pattern.removable ? (
            <button
              type="button"
              className="pattern-remove"
              aria-label={`Remove pattern ${pattern.raw}`}
              onClick={() => onRemove(pattern.raw)}
              disabled={pending}
            >
              ×
            </button>
          ) : (
            <span className="pattern-default">default</span>
          )}
        </span>
      ))}
    </div>
  );
}

function StreamRow({
  stream,
  onPattern,
  pending,
}: {
  stream: SelectedStream;
  onPattern: (streams: string, properties: string, exclude: boolean) => void;
  pending: boolean;
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <tr>
        <td>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setOpen((current) => !current)}
          >
            {open ? "−" : "+"}
          </button>{" "}
          <span className="row-link">{stream.name}</span>
        </td>
        <td>
          <SelectionBadge selection={stream.selection} />
        </td>
        <td className="cell-num">
          <div className="actions-row">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => onPattern(stream.name, "*", false)}
              disabled={pending}
            >
              Select
            </button>
            <button
              type="button"
              className="btn btn-sm btn-danger"
              onClick={() => onPattern(stream.name, "*", true)}
              disabled={pending}
            >
              Exclude
            </button>
          </div>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={3}>
            <div className="card">
              <table>
                <tbody>
                  {stream.properties.map((property) => (
                    <tr key={property.name}>
                      <td className="cell-mono">{property.name}</td>
                      <td>
                        <SelectionBadge selection={property.selection} />
                      </td>
                      <td className="cell-num">
                        <div className="actions-row">
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() =>
                              onPattern(stream.name, property.name, false)
                            }
                            disabled={pending}
                          >
                            Select
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm btn-danger"
                            onClick={() =>
                              onPattern(stream.name, property.name, true)
                            }
                            disabled={pending}
                          >
                            Exclude
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Choose what an extractor extracts.
 *
 * Patterns load immediately from the project file. The catalog is only fetched
 * when asked for, because reading it means running the extractor against the
 * source: it needs credentials that work, and it can be slow or fail.
 */
export function SelectEditor({
  pluginType,
  name,
}: {
  pluginType: string;
  name: string;
}) {
  const queryClient = useQueryClient();
  const [wantCatalog, setWantCatalog] = useState(false);

  const patterns = useQuery({
    queryKey: ["select", pluginType, name],
    queryFn: () => api.select(pluginType, name),
  });

  const catalog = useQuery({
    queryKey: ["select-catalog", pluginType, name],
    queryFn: () => api.selectCatalog(pluginType, name),
    enabled: wantCatalog,
    // Discovery is expensive and its result changes only when the source's
    // schema does, so this is not something to refetch on every focus.
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["select", pluginType, name] });
    queryClient.invalidateQueries({
      queryKey: ["select-catalog", pluginType, name],
    });
  };

  const add = useMutation({
    mutationFn: (body: {
      streams: string;
      properties: string;
      exclude: boolean;
    }) => api.addSelectPattern(pluginType, name, body),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (raw: string) =>
      api.removeSelectPattern(pluginType, name, raw),
    onSuccess: invalidate,
  });

  const refresh = useMutation({
    mutationFn: () => api.selectCatalog(pluginType, name, true),
    onSuccess: (fresh) => {
      queryClient.setQueryData(["select-catalog", pluginType, name], fresh);
    },
  });

  if (patterns.isLoading) return <Loading rows={2} />;
  if (patterns.isError) return <ErrorNotice error={patterns.error} />;

  const pending = add.isPending || remove.isPending;
  const onlyExclusions =
    patterns.data!.patterns.length > 0 &&
    patterns.data!.patterns.every((pattern) => pattern.negated);

  return (
    <>
      <div className="section">
        <div className="section-head">
          <h2>Select patterns</h2>
        </div>
        <div className="card">
          <p className="page-sub">
            Meltano selects by pattern, not by ticking entities. Choosing a
            stream below writes a pattern such as <code>orders.*</code>; what it
            matches depends on the catalog.
            {patterns.data!.environment && (
              <>
                {" "}
                Edits are stored under the{" "}
                <span className="tag">{patterns.data!.environment}</span>{" "}
                environment.
              </>
            )}
          </p>

          <PatternList
            patterns={patterns.data!.patterns}
            onRemove={(raw) => remove.mutate(raw)}
            pending={pending}
          />

          {/* The trap this surface most easily leads people into: declaring
              any pattern replaces Meltano's `*.*` default, so a list of only
              exclusions selects nothing at all rather than "everything but
              these". */}
          {onlyExclusions && (
            <div className="notice" role="status" style={{ marginTop: "var(--s3)" }}>
              <div>
                Every pattern here is an exclusion, so nothing is selected.
              </div>
              <div className="notice-instruction">
                Declaring any pattern replaces Meltano&apos;s default of
                selecting everything. Add <code>*.*</code> to mean “everything
                except these”.
              </div>
            </div>
          )}

          {(add.isError || remove.isError) && (
            <div style={{ marginTop: "var(--s3)" }}>
              <ErrorNotice error={add.error ?? remove.error} />
            </div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2>Catalog</h2>
          {wantCatalog && catalog.isSuccess && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
            >
              {refresh.isPending ? "Rediscovering…" : "Refresh catalog"}
            </button>
          )}
        </div>

        {!wantCatalog ? (
          <div className="card">
            <p className="page-sub">
              Reading the catalog runs this extractor against the source to ask
              what it can produce. It needs working credentials, and may take a
              while.
            </p>
            <div className="actions-row" style={{ marginTop: "var(--s3)" }}>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setWantCatalog(true)}
              >
                Load catalog
              </button>
            </div>
          </div>
        ) : catalog.isLoading ? (
          <Loading rows={4} />
        ) : catalog.isError ? (
          <ErrorNotice error={catalog.error} />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Stream</th>
                  <th>Selection</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {catalog.data!.streams.map((stream) => (
                  <StreamRow
                    key={stream.name}
                    stream={stream}
                    pending={pending}
                    onPattern={(streams, properties, exclude) =>
                      add.mutate({ streams, properties, exclude })
                    }
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {refresh.isError && (
          <div style={{ marginTop: "var(--s3)" }}>
            <ErrorNotice error={refresh.error} />
          </div>
        )}
      </div>
    </>
  );
}
