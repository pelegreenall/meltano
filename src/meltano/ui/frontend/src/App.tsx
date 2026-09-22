import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "./api";
import { Setup } from "./pages/Setup";
import { Loading } from "./components/Status";
import { Overview } from "./pages/Overview";
import { Jobs } from "./pages/Jobs";
import { Runs } from "./pages/Runs";
import { Schedules } from "./pages/Schedules";
import { State } from "./pages/State";
import { RunDetail } from "./pages/RunDetail";
import { Hub } from "./pages/Hub";
import { Plugins } from "./pages/Plugins";
import { PluginDetail } from "./pages/PluginDetail";

function Sidebar() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: api.plugins });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.jobs });
  const schedules = useQuery({
    queryKey: ["schedules"],
    queryFn: api.schedules,
  });

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
        <NavLink className="nav-link" to="/jobs">
          Jobs
          {jobs.data && jobs.data.length > 0 && (
            <span className="nav-count">{jobs.data.length}</span>
          )}
        </NavLink>
        <NavLink className="nav-link" to="/schedules">
          Schedules
          {schedules.data && schedules.data.length > 0 && (
            <span className="nav-count">{schedules.data.length}</span>
          )}
        </NavLink>
        <NavLink className="nav-link" to="/state">
          State
        </NavLink>
        <NavLink className="nav-link" to="/plugins">
          Plugins
          {plugins.data && plugins.data.length > 0 && (
            <span className="nav-count">{plugins.data.length}</span>
          )}
        </NavLink>
        <NavLink className="nav-link" to="/hub">
          Add from Hub
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
  // Which face to show is a property of the server, not of the URL: a
  // projectless server answers `/setup` and serves nothing else, while a
  // serving one 404s it. Asking is cheaper and more honest than a build flag.
  const setup = useQuery({
    queryKey: ["setup"],
    queryFn: api.setupState,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });

  if (setup.isLoading) {
    return (
      <div className="setup">
        <Loading rows={2} />
      </div>
    );
  }

  if (setup.isSuccess) return <Setup state={setup.data} />;

  // Anything else means a project is being served: a 404 because `/setup` does
  // not exist there, and any other failure - an expired token, say - is left
  // to the pages, which report it where it makes sense.
  return (
    <div className="shell">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:runId" element={<RunDetail />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/schedules" element={<Schedules />} />
          <Route path="/state" element={<State />} />
          <Route path="/plugins" element={<Plugins />} />
          <Route path="/hub" element={<Hub />} />
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
