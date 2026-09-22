import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  pluginTasks,
  type PreviewResponse,
  type TableStep,
  type TransformStep,
} from "../api";
import { TableSteps } from "./TableSteps";
import { ErrorNotice, Loading } from "./Status";

/** Step kinds, in the order someone reaches for them. */
const KINDS: { value: TransformStep["kind"]; label: string }[] = [
  { value: "drop", label: "Remove column" },
  { value: "rename", label: "Rename column" },
  { value: "cast", label: "Change type" },
  { value: "filter", label: "Keep rows where" },
];

const CASTS: { value: NonNullable<TransformStep["type"]>; label: string }[] = [
  { value: "string", label: "Text" },
  { value: "integer", label: "Whole number" },
  { value: "number", label: "Decimal" },
  { value: "boolean", label: "True/false" },
];

const OPERATORS: {
  value: NonNullable<TransformStep["operator"]>;
  label: string;
  unary?: boolean;
}[] = [
  { value: "eq", label: "equals" },
  { value: "ne", label: "does not equal" },
  { value: "gt", label: "is greater than" },
  { value: "gte", label: "is at least" },
  { value: "lt", label: "is less than" },
  { value: "lte", label: "is at most" },
  { value: "contains", label: "contains" },
  { value: "is_null", label: "is empty", unary: true },
  { value: "not_null", label: "is not empty", unary: true },
];

/** Describe a step the way it would read in a sentence. */
function describe(step: TransformStep): string {
  switch (step.kind) {
    case "drop":
      return `Remove ${step.column}`;
    case "rename":
      return `Rename ${step.column} → ${step.to}`;
    case "cast": {
      const cast = CASTS.find((entry) => entry.value === step.type);
      return `${step.column} as ${cast?.label ?? step.type}`;
    }
    case "filter": {
      const operator = OPERATORS.find((entry) => entry.value === step.operator);
      const unary = operator?.unary ?? false;
      return `Keep rows where ${step.column} ${operator?.label ?? ""}${
        unary ? "" : ` ${JSON.stringify(step.value)}`
      }`;
    }
    default:
      return step.kind;
  }
}

/** A cell's value, rendered so the table stays readable. */
function cell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function StepBuilder({
  columns,
  onAdd,
}: {
  columns: string[];
  onAdd: (step: TransformStep) => void;
}) {
  const [kind, setKind] = useState<TransformStep["kind"]>("drop");
  const [column, setColumn] = useState(columns[0] ?? "");
  const [to, setTo] = useState("");
  const [type, setType] = useState<NonNullable<TransformStep["type"]>>("string");
  const [operator, setOperator] =
    useState<NonNullable<TransformStep["operator"]>>("eq");
  const [value, setValue] = useState("");

  const unary = OPERATORS.find((entry) => entry.value === operator)?.unary ?? false;
  const chosen = column || columns[0] || "";
  const ready =
    chosen !== "" && (kind !== "rename" || to.trim() !== "");

  const submit = () => {
    const step: TransformStep = { kind, column: chosen };
    if (kind === "rename") step.to = to.trim();
    if (kind === "cast") step.type = type;
    if (kind === "filter") {
      step.operator = operator;
      if (!unary) {
        // Numeric-looking input is sent as a number so comparisons order
        // properly rather than comparing as text.
        const trimmed = value.trim();
        const asNumber = Number(trimmed);
        step.value =
          trimmed !== "" && !Number.isNaN(asNumber) ? asNumber : trimmed;
      }
    }
    onAdd(step);
    setTo("");
    setValue("");
  };

  return (
    <div className="composer">
      <label className="field">
        <span className="field-label">Step</span>
        <select
          value={kind}
          onChange={(event) =>
            setKind(event.target.value as TransformStep["kind"])
          }
        >
          {KINDS.map((entry) => (
            <option key={entry.value} value={entry.value}>
              {entry.label}
            </option>
          ))}
        </select>
      </label>

      <label className="field">
        <span className="field-label">Column</span>
        <select value={chosen} onChange={(event) => setColumn(event.target.value)}>
          {columns.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>

      {kind === "rename" && (
        <label className="field">
          <span className="field-label">New name</span>
          <input
            type="text"
            value={to}
            placeholder="customer_id"
            onChange={(event) => setTo(event.target.value)}
          />
        </label>
      )}

      {kind === "cast" && (
        <label className="field">
          <span className="field-label">Type</span>
          <select
            value={type}
            onChange={(event) =>
              setType(event.target.value as NonNullable<TransformStep["type"]>)
            }
          >
            {CASTS.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
      )}

      {kind === "filter" && (
        <>
          <label className="field">
            <span className="field-label">Test</span>
            <select
              value={operator}
              onChange={(event) =>
                setOperator(
                  event.target.value as NonNullable<TransformStep["operator"]>,
                )
              }
            >
              {OPERATORS.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </select>
          </label>
          {!unary && (
            <label className="field">
              <span className="field-label">Value</span>
              <input
                type="text"
                value={value}
                onChange={(event) => setValue(event.target.value)}
              />
            </label>
          )}
        </>
      )}

      <button
        type="button"
        className="btn"
        onClick={submit}
        disabled={!ready}
      >
        Add step
      </button>
    </div>
  );
}

/**
 * Look at an extractor's records and shape them, Power Query style.
 *
 * Steps are structured rather than typed expressions: the server compiles them
 * into Meltano stream maps and applies the same list to these rows, so what is
 * shown here is what a run would produce.
 *
 * Only column-level edits and row filters, because a stream map sees one record
 * at a time. Joins, grouping and pivots need the whole table and belong in dbt,
 * after the data has landed.
 */
export function DataShaper({
  pluginType,
  name,
}: {
  pluginType: string;
  name: string;
}) {
  const [stream, setStream] = useState<string | null>(null);
  const [steps, setSteps] = useState<TransformStep[]>([]);
  const [tableSteps, setTableSteps] = useState<TableStep[]>([]);
  const [result, setResult] = useState<PreviewResponse | null>(null);
  const [limit, setLimit] = useState(20);
  const [mappingName, setMappingName] = useState("");
  const queryClient = useQueryClient();

  // Saving writes config; a run only honours it once a mapper is present
  // and installed, so the two states are surfaced separately.
  const mapper = useQuery({
    queryKey: ["mapper-status"],
    queryFn: api.mapperStatus,
  });

  // Both step lists go in one request: the server applies the row steps and
  // then the table steps, which is the order a pipeline applies them in too.
  const run = useMutation({
    mutationFn: (next: { steps: TransformStep[]; table: TableStep[] }) =>
      api.preview(pluginType, name, {
        stream,
        limit,
        steps: next.steps,
        table_steps: next.table,
      }),
    onSuccess: setResult,
  });

  const save = useMutation({
    mutationFn: (target: string) =>
      api.saveMapping({
        name: mappingName.trim(),
        stream: target,
        steps,
        overwrite: true,
      }),
  });

  // Two different shortfalls with one control: a mapper that is missing needs
  // adding, one that is declared but has no virtualenv only needs installing.
  // Both end in a supervised task, so only its run is carried forward.
  const installMapper = useMutation<{ run_id: string | null }>({
    mutationFn: async () => {
      if (mapper.data?.name) {
        const task = await pluginTasks.install("mappers", mapper.data.name);
        return { run_id: task.run_id };
      }
      const added = await api.addPlugin({
        plugin_type: "mappers",
        name: mapper.data?.suggested ?? "meltano-map-transformer",
      });
      return { run_id: added.run_id };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mapper-status"] });
      queryClient.invalidateQueries({ queryKey: ["plugins"] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  // Every edit re-runs the preview, which is what makes this a loop rather
  // than a form. The extractor is re-invoked each time; that is the cost of
  // showing the truth rather than a guess.
  const update = (next: TransformStep[]) => {
    setSteps(next);
    if (result) run.mutate({ steps: next, table: tableSteps });
  };

  const updateTable = (next: TableStep[]) => {
    setTableSteps(next);
    if (result) run.mutate({ steps, table: next });
  };

  const streams = Object.keys(result?.schemas ?? {});
  const columns = result?.columns ?? [];

  // A mapping's stream maps are keyed by stream, so saving needs exactly one.
  // With a single-stream tap that is unambiguous; otherwise one must be chosen.
  const target = stream ?? (streams.length === 1 ? streams[0] : null);

  return (
    <div className="section">
      <div className="section-head">
        <h2>Data</h2>
        {result && (
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => run.mutate({ steps, table: tableSteps })}
            disabled={run.isPending}
          >
            {run.isPending ? "Reading…" : "Refresh"}
          </button>
        )}
      </div>

      {!result && !run.isPending && (
        <div className="card">
          <p className="page-sub">
            Reads a few records by running this extractor on its own — no
            loader, no state, nothing written. Then shape them into the columns
            you want.
          </p>
          <div className="composer" style={{ marginTop: "var(--s3)" }}>
            <label className="field">
              <span className="field-label">Rows</span>
              <input
                type="number"
                min={1}
                max={500}
                value={limit}
                onChange={(event) => setLimit(Number(event.target.value) || 20)}
              />
            </label>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => run.mutate({ steps, table: tableSteps })}
            >
              Load data
            </button>
          </div>
        </div>
      )}

      {run.isPending && !result && <Loading rows={4} />}
      {run.isError && <ErrorNotice error={run.error} />}

      {result && (
        <>
          <div className="card">
            <div className="composer">
              {streams.length > 1 && (
                <label className="field">
                  <span className="field-label">Stream</span>
                  <select
                    value={stream ?? ""}
                    onChange={(event) => {
                      setStream(event.target.value || null);
                      setSteps([]);
                    }}
                  >
                    <option value="">All streams</option>
                    {streams.map((entry) => (
                      <option key={entry} value={entry}>
                        {entry}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <span className="field-help" style={{ alignSelf: "flex-end" }}>
                Showing {result.row_count} of {result.read_count} record
                {result.read_count === 1 ? "" : "s"} read
                {result.truncated && " (stopped at the limit)"}
                {result.timed_out && " — the extractor timed out"}
              </span>
            </div>
          </div>

          <div className="section">
            <div className="section-head">
              <h2>Applied steps</h2>
              {steps.length > 0 && (
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => update([])}
                >
                  Clear all
                </button>
              )}
            </div>

            <div className="card">
              {steps.length === 0 ? (
                <p className="page-sub" style={{ margin: 0 }}>
                  No steps yet — the rows below are as the extractor emitted
                  them.
                </p>
              ) : (
                <ol className="step-list">
                  {steps.map((step, index) => (
                    <li key={index}>
                      <span>{describe(step)}</span>
                      <button
                        type="button"
                        className="pattern-remove"
                        aria-label={`Remove step ${index + 1}`}
                        onClick={() =>
                          update(steps.filter((_, at) => at !== index))
                        }
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ol>
              )}

              <div style={{ marginTop: "var(--s4)" }}>
                <StepBuilder
                  columns={columns}
                  onAdd={(step) => update([...steps, step])}
                />
              </div>
            </div>
          </div>

          <TableSteps
            steps={tableSteps}
            columns={columns}
            sql={result.sql}
            streams={streams}
            stream={target}
            onChange={updateTable}
          />

          <div className="table-wrap">
            {result.rows.length === 0 ? (
              <div className="empty">
                <div className="empty-title">No rows</div>
                <div>
                  {steps.length > 0
                    ? "Every record was filtered out by the steps above."
                    : "This extractor produced no records."}
                </div>
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    {columns.map((column) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row, index) => (
                    <tr key={index}>
                      {columns.map((column) => (
                        <td key={column} className="cell-mono">
                          {cell(row[column])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {steps.length > 0 && (
            <div className="card" style={{ marginTop: "var(--s4)" }}>
              <div className="section-head">
                <h2>Save as a mapping</h2>
              </div>
              <p className="page-sub">
                Stores these steps under a mapper plugin so a pipeline can use
                them: <code>meltano run {name} &lt;mapping&gt; &lt;loader&gt;</code>.
              </p>
              <div className="composer">
                <label className="field">
                  <span className="field-label">Mapping name</span>
                  <input
                    type="text"
                    value={mappingName}
                    placeholder="tidy-customers"
                    onChange={(event) => setMappingName(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => target && save.mutate(target)}
                  disabled={
                    save.isPending || mappingName.trim() === "" || !target
                  }
                  title={
                    target
                      ? undefined
                      : "Choose a single stream first — a mapping is keyed by stream"
                  }
                >
                  {save.isPending ? "Saving…" : "Save mapping"}
                </button>
              </div>

              {mapper.data && !mapper.data.is_installed && (
                <div className="notice" role="status">
                  <div>
                    {mapper.data.name
                      ? `${mapper.data.name} is declared but not installed.`
                      : "This project has no mapper plugin."}{" "}
                    A run will not apply a mapping until it is installed.
                  </div>
                  <div className="notice-instruction">
                    <button
                      type="button"
                      className="btn btn-sm btn-primary"
                      onClick={() => installMapper.mutate()}
                      disabled={installMapper.isPending}
                      style={{ marginTop: "var(--s2)" }}
                    >
                      {installMapper.isPending
                        ? "Installing…"
                        : mapper.data.name
                          ? `Install ${mapper.data.name}`
                          : `Add and install ${mapper.data.suggested}`}
                    </button>
                  </div>
                </div>
              )}

              {installMapper.isError && (
                <ErrorNotice error={installMapper.error} />
              )}

              {save.isSuccess && (
                <div className="notice notice-info" role="status">
                  <div>
                    Saved <code>{save.data.name}</code> to{" "}
                    <code>{save.data.mapper}</code>.
                  </div>
                  <div className="notice-instruction">
                    Use it with{" "}
                    <code>
                      meltano run {name} {save.data.name} &lt;loader&gt;
                    </code>
                    . The mapper plugin has to be installed for a run to apply
                    it.
                  </div>
                </div>
              )}
              {save.isError && <ErrorNotice error={save.error} />}
            </div>
          )}

          {steps.length > 0 && (
            <details className="card" style={{ marginTop: "var(--s4)" }}>
              <summary>Stream map these steps compile to</summary>
              <pre className="state-payload" style={{ marginTop: "var(--s3)" }}>
                {JSON.stringify(result.stream_map, null, 2)}
              </pre>
              <p className="field-help">
                This is Meltano's own format. A mapper plugin applies it during
                a run — joins, grouping and pivots aren't expressible here,
                because a stream map sees one record at a time.
              </p>
            </details>
          )}
        </>
      )}
    </div>
  );
}
