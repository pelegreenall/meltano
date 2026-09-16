import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "./api";
import { Overview } from "./pages/Overview";
import { Runs } from "./pages/Runs";
import { RunDetail } from "./pages/RunDetail";
import { Plugins } from "./pages/Plugins";
import { PluginDetail } from "./pages/PluginDetail";

function Sidebar() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: api.plugins });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          M
        </span>
        Meltano
      </div>

      <nav className="nav" aria-label="Sections">
        <NavLink className="nav-link" to="/overview">
          Overview
        </NavLink>
        <NavLink className="nav-link" to="/runs">
          Runs
          {runs.data && runs.data.length > 0 && (
            <span className="nav-count">{runs.data.length}</span>
          )}
        </NavLink>
        <NavLink className="nav-link" to="/plugins">
          Plugins
          {plugins.data && plugins.data.length > 0 && (
            <span className="nav-count">{plugins.data.length}</span>
          )}
        </NavLink>
      </nav>

      <div className="sidebar-foot">
        {meta.data && (
          <>
            <span>Meltano {meta.data.meltano_version}</span>
            {meta.data.environment && <span>Environment: {meta.data.environment}</span>}
            {meta.data.readonly && <span>Read-only mode</span>}
            <a href="/docs" target="_blank" rel="noreferrer">
              API docs
            </a>
          </>
        )}
      </div>
    </aside>
  );
}

export function App() {
  return (
    <div className="shell">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:runId" element={<RunDetail />} />
          <Route path="/plugins" element={<Plugins />} />
          <Route
            path="/plugins/:pluginType/:name"
            element={<PluginDetail />}
          />
          <Route
            path="*"
            element={
              <div className="empty">
                <div className="empty-title">Page not found</div>
                <div>
                  <NavLink to="/overview">Back to overview</NavLink>
                </div>
              </div>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
