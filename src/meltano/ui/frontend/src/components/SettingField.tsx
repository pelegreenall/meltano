import { useEffect, useId, useState } from "react";

import type { SettingInfo } from "../api";

interface Props {
  setting: SettingInfo;
  busy: boolean;
  onSave: (value: unknown) => void;
  onClear: () => void;
}

/** One configuration field, rendered from the setting's declared kind.
 *
 * A sensitive setting is never populated from the server - the API returns a
 * redaction marker, not the value - so its input always starts empty and shows
 * whether something is already stored. */
export function SettingField({ setting, busy, onSave, onClear }: Props) {
  const fieldId = useId();
  const [draft, setDraft] = useState(() => initialDraft(setting));
  const [dirty, setDirty] = useState(false);

  // Re-sync when a save or clear changes the stored value.
  useEffect(() => {
    setDraft(initialDraft(setting));
    setDirty(false);
  }, [setting.value, setting.is_set]);

  const update = (next: string) => {
    setDraft(next);
    setDirty(true);
  };

  const commit = () => {
    onSave(coerce(setting, draft));
    setDirty(false);
  };

  return (
    <div className="field-row">
      <div className="field-head">
        <label className="field-name" htmlFor={fieldId}>
          {setting.label || setting.name}
          {setting.required && <span className="field-required"> required</span>}
        </label>
        <div className="field-flags">
          {setting.sensitive && <span className="tag">secret</span>}
          {setting.is_set ? (
            <span className="badge badge-ok">set · {setting.source}</span>
          ) : (
            <span className="badge badge-idle">not set</span>
          )}
        </div>
      </div>

      {setting.description && (
        <p className="field-help">{setting.description}</p>
      )}

      <div className="field-control">
        {setting.kind === "boolean" ? (
          <select
            id={fieldId}
            value={draft}
            onChange={(event) => update(event.target.value)}
            disabled={busy}
          >
            <option value="">(not set)</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        ) : setting.options.length > 0 ? (
          <select
            id={fieldId}
            value={draft}
            onChange={(event) => update(event.target.value)}
            disabled={busy}
          >
            <option value="">(not set)</option>
            {setting.options.map((option) => (
              <option key={String(option.value)} value={String(option.value)}>
                {option.label ?? String(option.value)}
              </option>
            ))}
          </select>
        ) : isMultiline(setting) ? (
          <textarea
            id={fieldId}
            rows={4}
            value={draft}
            spellCheck={false}
            onChange={(event) => update(event.target.value)}
            disabled={busy}
          />
        ) : (
          <input
            id={fieldId}
            type={setting.sensitive ? "password" : "text"}
            value={draft}
            autoComplete={setting.sensitive ? "new-password" : "off"}
            spellCheck={false}
            onChange={(event) => update(event.target.value)}
            disabled={busy}
          />
        )}

        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={commit}
          disabled={busy || !dirty || draft === ""}
        >
          Save
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={onClear}
          disabled={busy || !setting.is_set}
        >
          Clear
        </button>
      </div>

      {setting.env && <div className="field-env">{setting.env}</div>}
    </div>
  );
}

function isMultiline(setting: SettingInfo): boolean {
  return setting.kind === "array" || setting.kind === "object";
}

/** Seed the input. Sensitive settings always start empty: the server sends a
 * redaction marker rather than the value, and echoing that back would let a
 * user overwrite a real secret with the literal "(redacted)". */
function initialDraft(setting: SettingInfo): string {
  if (setting.sensitive) return "";
  if (setting.value === null || setting.value === undefined) return "";
  if (typeof setting.value === "object") {
    return JSON.stringify(setting.value, null, 2);
  }
  return String(setting.value);
}

/** Convert the input's text back into the type the setting expects. */
function coerce(setting: SettingInfo, draft: string): unknown {
  if (setting.kind === "boolean") return draft === "true";
  if (setting.kind === "integer") {
    const parsed = Number(draft);
    return Number.isFinite(parsed) ? parsed : draft;
  }
  if (isMultiline(setting)) {
    try {
      return JSON.parse(draft);
    } catch {
      // Let the API reject it and surface a real error rather than guessing.
      return draft;
    }
  }
  return draft;
}
