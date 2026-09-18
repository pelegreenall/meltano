import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api, pluginTasks, type PluginCommand } from "../api";
import { ErrorNotice, Loading } from "./Status";

interface Props {
  pluginType: string;
  name: string;
  isInstalled: boolean;
}

/**
 * The commands a plugin declares, each runnable on its own.
 *
 * This is the transformation layer's way in. A mapper reshapes records as
 * they pass through a pipeline; dbt runs SQL over what has already landed,
 * and it is reached as a pipeline block spelled `plugin:command`. Extractors
 * and loaders declare no commands, so this renders nothing for them.
 *
 * Every command starts an ordinary supervised run, which means it shares the
 * log stream, history and cancellation that pipeline runs already have.
 */
export function PluginCommands({ pluginType, name, isInstalled }: Props) {
  const navigate = useNavigate();

  const commands = useQuery({
    queryKey: ["plugin-commands", pluginType, name],
    queryFn: () => pluginTasks.commands(pluginType, name),
  });

  const start = useMutation({
    mutationFn: (command: PluginCommand) =>
      api.startRun({ blocks: [command.block] }),
    onSuccess: (run) => navigate(`/runs/${run.run_id}`),
  });

  if (commands.isLoading) return <Loading rows={3} />;
  if (commands.isError) return <ErrorNotice error={commands.error} />;

  const entries = commands.data ?? [];
  if (entries.length === 0) return null;

  return (
    <div className="section">
      <div className="section-head">
        <h2>Commands</h2>
      </div>
      <p className="page-sub">
        Each runs as <code>meltano run {name}:&lt;command&gt;</code>, so it
        lands in the run history like any pipeline. A command that serves
        rather than finishes keeps running until you cancel it.
      </p>

      {!isInstalled && (
        <p className="actions-hint">
          Not installed yet — install it before running a command.
        </p>
      )}

      <ul className="command-list">
        {entries.map((command) => (
          <li key={command.name} className="command">
            <div className="command-text">
              <code>{command.name}</code>
              {command.description && <span>{command.description}</span>}
            </div>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => start.mutate(command)}
              disabled={!isInstalled || start.isPending}
              title={`Runs \`${command.block}\``}
            >
              {start.isPending && start.variables?.name === command.name
                ? "Starting…"
                : "Run"}
            </button>
          </li>
        ))}
      </ul>

      {start.isError && <ErrorNotice error={start.error} />}
    </div>
  );
}
