import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, type PluginInfo } from "../api";
import { Empty, ErrorNotice, Loading } from "../components/Status";

const TYPE_LABELS: Record<string, string> = {
  extractors: "Extractors",
  loaders: "Loaders",
  transformers: "Transformers",
  utilities: "Utilities",
  mappers: "Mappers",
  files: "File bundles",
  orchestrators: "Orchestrators",
};

export function Plugins() {
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: api.plugins });

  if (plugins.isError) return <ErrorNotice error={plugins.error} />;

  const grouped = new Map<string, PluginInfo[]>();
  for (const plugin of plugins.data ?? []) {
    const bucket = grouped.get(plugin.type) ?? [];
    bucket.push(plugin);
    grouped.set(plugin.type, bucket);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Plugins</h1>
          <p className="page-sub">
            Everything declared in this project's <code>meltano.yml</code>.
            Select one to enter its credentials and settings.
          </p>
        </div>
      </div>

      {plugins.isLoading ? (
        <Loading rows={4} />
      ) : grouped.size === 0 ? (
        <div className="table-wrap">
          <Empty
            title="No plugins installed"
            hint="Add one with `meltano add tap-github`."
          />
        </div>
      ) : (
        [...grouped.entries()].map(([type, items]) => (
          <div className="section" key={type}>
            <div className="section-head">
              <h2>{TYPE_LABELS[type] ?? type}</h2>
              <span className="nav-count">{items.length}</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Variant</th>
                    <th>Installed</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((plugin) => (
                    <tr key={plugin.name}>
                      <td>
                        <Link
                          className="row-link"
                          to={`/plugins/${plugin.type}/${plugin.name}`}
                        >
                          {plugin.name}
                        </Link>
                        {plugin.label && plugin.label !== plugin.name && (
                          <div className="cell-mono">{plugin.label}</div>
                        )}
                      </td>
                      <td>
                        {plugin.variant ? (
                          <span className="tag">{plugin.variant}</span>
                        ) : (
                          <span className="cell-mono">—</span>
                        )}
                      </td>
                      <td>
                        <span
                          className={`badge ${plugin.is_installed ? "badge-ok" : "badge-warn"}`}
                        >
                          {plugin.is_installed ? "Yes" : "Not installed"}
                        </span>
                      </td>
                      <td className="cell-num">
                        {plugin.docs && (
                          <a href={plugin.docs} target="_blank" rel="noreferrer">
                            Docs
                          </a>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))
      )}
    </>
  );
}
