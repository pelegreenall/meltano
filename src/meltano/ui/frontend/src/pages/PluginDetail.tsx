import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { configApi, type SettingInfo } from "../api";
import { ErrorNotice, Loading } from "../components/Status";
import { SettingField } from "../components/SettingField";

export function PluginDetail() {
  const { pluginType = "", name = "" } = useParams();
  const [showAll, setShowAll] = useState(false);
  const queryClient = useQueryClient();

  const config = useQuery({
    queryKey: ["config", pluginType, name],
    queryFn: () => configApi.read(pluginType, name),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["config", pluginType, name] });

  const save = useMutation({
    mutationFn: ({ setting, value }: { setting: string; value: unknown }) =>
      configApi.set(pluginType, name, setting, value),
    onSuccess: invalidate,
  });

  const clear = useMutation({
    mutationFn: (setting: string) => configApi.unset(pluginType, name, setting),
    onSuccess: invalidate,
  });

  if (config.isLoading) return <Loading rows={6} />;
  if (config.isError) return <ErrorNotice error={config.error} />;

  const settings = config.data!.settings;
  const configured = settings.filter((s) => s.is_set || s.required);
  const visible = showAll ? settings : configured;
  const secrets = settings.filter((s) => s.sensitive).length;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{name}</h1>
          <p className="page-sub">
            <Link to="/plugins">Plugins</Link> · {config.data!.type} ·{" "}
            {settings.length} settings
            {secrets > 0 && `, ${secrets} of them secret`}
          </p>
        </div>
      </div>

      <div className="notice notice-info">
        <div>Secrets are written to this project's <code>.env</code> file.</div>
        <div className="notice-instruction">
          Meltano never writes them to <code>meltano.yml</code>, and this page
          cannot read them back — a configured secret shows only as “set”.
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2>{showAll ? "All settings" : "Configured and required"}</h2>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setShowAll((current) => !current)}
          >
            {showAll
              ? `Show configured only (${configured.length})`
              : `Show all (${settings.length})`}
          </button>
        </div>

        {visible.length === 0 ? (
          <div className="card">
            <p className="page-sub" style={{ margin: 0 }}>
              Nothing is configured yet, and this plugin declares no required
              settings. Choose “Show all” to see what it accepts.
            </p>
          </div>
        ) : (
          <div className="fields">
            {visible.map((setting) => (
              <SettingField
                key={setting.name}
                setting={setting}
                busy={save.isPending || clear.isPending}
                onSave={(value) => save.mutate({ setting: setting.name, value })}
                onClear={() => clear.mutate(setting.name)}
              />
            ))}
          </div>
        )}

        {(save.isError || clear.isError) && (
          <div style={{ marginTop: "16px" }}>
            <ErrorNotice error={save.error ?? clear.error} />
          </div>
        )}
      </div>
    </>
  );
}

export type { SettingInfo };
