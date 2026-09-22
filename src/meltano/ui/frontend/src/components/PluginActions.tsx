import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { pluginTasks, type PluginTaskAccepted } from "../api";
import { ErrorNotice } from "./Status";

interface Props {
  pluginType: string;
  name: string;
  isInstalled: boolean;
  disabled: boolean;
  onStarted: () => void;
}

/** Install and connection-test actions for one plugin.
 *
 * Both run as supervised background tasks, so this hands off to the run view
 * rather than blocking. Testing a loader writes a real table to the
 * destination, so that case is confirmed before the task starts - the API
 * returns the warning, but it would arrive too late to be useful, so the
 * confirmation happens here first. */
export function PluginActions({
  pluginType,
  name,
  isInstalled,
  disabled,
  onStarted,
}: Props) {
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const isLoader = pluginType === "loaders";

  const follow = (task: PluginTaskAccepted) => {
    onStarted();
    navigate(`/runs/${task.run_id}`);
  };

  const install = useMutation({
    mutationFn: () => pluginTasks.install(pluginType, name),
    onSuccess: follow,
  });

  const test = useMutation({
    mutationFn: () => pluginTasks.test(pluginType, name),
    onSuccess: follow,
  });

  const busy = install.isPending || test.isPending || disabled;

  return (
    <div className="actions">
      <div className="actions-row">
        <button
          type="button"
          className="btn"
          onClick={() => install.mutate()}
          disabled={busy}
        >
          {install.isPending
            ? "Installing…"
            : isInstalled
              ? "Reinstall"
              : "Install"}
        </button>

        <button
          type="button"
          className="btn btn-primary"
          onClick={() => (isLoader ? setConfirming(true) : test.mutate())}
          disabled={busy || !isInstalled}
          title={
            isInstalled
              ? undefined
              : "Install the plugin before testing its configuration"
          }
        >
          {test.isPending ? "Testing…" : "Test connection"}
        </button>

        {!isInstalled && (
          <span className="actions-hint">
            Not installed yet — install it before testing or running.
          </span>
        )}
      </div>

      {confirming && (
        <div className="confirm" role="alertdialog" aria-label="Confirm loader test">
          <div>
            <strong>This writes to the destination.</strong> Testing a loader
            sends a <code>meltano_test_stream</code> table to wherever{" "}
            {name} is configured to load.
          </div>
          <div className="actions-row">
            <button
              type="button"
              className="btn btn-danger btn-sm"
              onClick={() => {
                setConfirming(false);
                test.mutate();
              }}
            >
              Write the test table
            </button>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setConfirming(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {(install.isError || test.isError) && (
        <ErrorNotice error={install.error ?? test.error} />
      )}
    </div>
  );
}
