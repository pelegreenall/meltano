import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api, type HubPlugin } from "../api";
import { Empty, ErrorNotice, Loading } from "../components/Status";

/** The types worth browsing first; `mappings` are project-defined, not listed. */
const TYPES: { value: string; label: string }[] = [
  { value: "extractors", label: "Extractors" },
  { value: "loaders", label: "Loaders" },
  { value: "transformers", label: "Transformers" },
  { value: "utilities", label: "Utilities" },
  { value: "mappers", label: "Mappers" },
  { value: "orchestrators", label: "Orchestrators" },
];

function HubRow({ plugin }: { plugin: HubPlugin }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [variant, setVariant] = useState(plugin.default_variant);

  const add = useMutation({
    mutationFn: (install: boolean) =>
      api.addPlugin({
        plugin_type: plugin.plugin_type,
        name: plugin.name,
        variant,
        install,
      }),
    onSuccess: (added) => {
      queryClient.invalidateQueries({ queryKey: ["plugins"] });
      queryClient.invalidateQueries({ queryKey: ["hub"] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      // Installing builds a venv and downloads packages, so the useful next
      // screen is the one streaming that.
      if (added.run_id) navigate(`/runs/${added.run_id}`);
      else navigate(`/plugins/${added.type}/${added.name}`);
    },
  });

  return (
    <>
      <tr>
        <td>
          <span className="row-link">{plugin.name}</span>
        </td>
        <td>
          {plugin.variants.length > 1 ? (
            <select
              value={variant}
              onChange={(event) => setVariant(event.target.value)}
              disabled={plugin.is_added || add.isPending}
            >
              {plugin.variants.map((entry) => (
                <option key={entry.name} value={entry.name}>
                  {entry.name}
                  {entry.is_default ? " (default)" : ""}
                </option>
              ))}
            </select>
          ) : (
            <span className="cell-mono">{plugin.default_variant}</span>
          )}
        </td>
        <td className="cell-num">
          {plugin.is_added ? (
            <span className="badge badge-ok">In project</span>
          ) : (
            <div className="actions-row">
              <button
                type="button"
                className="btn btn-sm btn-primary"
                onClick={() => add.mutate(true)}
                disabled={add.isPending}
              >
                {add.isPending ? "Adding…" : "Add and install"}
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => add.mutate(false)}
                disabled={add.isPending}
                title="Declare it in meltano.yml without installing yet"
              >
                Add only
              </button>
            </div>
          )}
        </td>
      </tr>
      {add.isError && (
        <tr>
          <td colSpan={3}>
            <ErrorNotice error={add.error} />
          </td>
        </tr>
      )}
    </>
  );
}

export function Hub() {
  const [params, setParams] = useSearchParams();
  const type = params.get("type") ?? "extractors";
  const q = params.get("q") ?? "";

  const hub = useQuery({
    queryKey: ["hub", type, q],
    queryFn: () => api.hub(type, q || undefined),
    // Hub's index changes rarely and the request leaves the machine, so this
    // is not something to refetch on every focus.
    staleTime: 5 * 60_000,
    retry: false,
  });

  const update = (next: { type?: string; q?: string }) => {
    const merged = { type, q, ...next };
    const search: Record<string, string> = { type: merged.type };
    if (merged.q) search.q = merged.q;
    setParams(search, { replace: true });
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Add from Hub</h1>
          <p className="page-sub">
            Meltano Hub's catalogue of connectors. Adding one writes it to{" "}
            <code>meltano.yml</code>, the same as <code>meltano add</code>.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="composer">
          <label className="field">
            <span className="field-label">Type</span>
            <select
              value={type}
              onChange={(event) => update({ type: event.target.value })}
            >
              {TYPES.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </select>
          </label>

          <label className="field" style={{ flex: 1, minWidth: "220px" }}>
            <span className="field-label">Search</span>
            <input
              type="text"
              value={q}
              placeholder="github"
              onChange={(event) => update({ q: event.target.value })}
            />
          </label>
        </div>
      </div>

      {hub.isError ? (
        <ErrorNotice error={hub.error} />
      ) : hub.isLoading ? (
        <Loading rows={5} />
      ) : (hub.data?.length ?? 0) === 0 ? (
        <div className="table-wrap">
          <Empty
            title={q ? "Nothing matches" : "Hub listed nothing"}
            hint={
              q
                ? "Try a shorter search term."
                : "Hub returned no plugins of this type."
            }
          />
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Variant</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {hub.data!.map((plugin) => (
                <HubRow key={plugin.name} plugin={plugin} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
