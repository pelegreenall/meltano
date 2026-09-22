import { useState } from "react";

import type { AggregateSpec, TableStep } from "../api";

/** Table-level step kinds, in the order someone reaches for them. */
const KINDS: { value: TableStep["kind"]; label: string }[] = [
  { value: "group_by", label: "Group by" },
  { value: "filter", label: "Keep rows where" },
  { value: "sort", label: "Sort by" },
  { value: "top_n", label: "Keep first" },
  { value: "distinct", label: "Remove duplicates" },
];

const AGGREGATES: { value: AggregateSpec["fn"]; label: string }[] = [
  { value: "sum", label: "Sum of" },
  { value: "count", label: "Count of rows" },
  { value: "count_distinct", label: "Distinct count of" },
  { value: "avg", label: "Average of" },
  { value: "min", label: "Smallest" },
  { value: "max", label: "Largest" },
];

const OPERATORS: { value: string; label: string; unary?: boolean }[] = [
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

/** `count` is the only aggregate that means anything without a column. */
const needsColumn = (fn: AggregateSpec["fn"]) => fn !== "count";

/** Describe a step the way it would read in a sentence. */
function describe(step: TableStep): string {
  switch (step.kind) {
    case "group_by": {
      const by = (step.by ?? []).join(", ") || "everything";
      const what = (step.aggregates ?? [])
        .map((aggregate) => {
          const label = AGGREGATES.find((entry) => entry.value === aggregate.fn);
          return aggregate.column
            ? `${label?.label ?? aggregate.fn} ${aggregate.column}`
            : (label?.label ?? aggregate.fn);
        })
        .join(", ");
      return `Group by ${by} — ${what}`;
    }
    case "filter": {
      const operator = OPERATORS.find((entry) => entry.value === step.operator);
      const unary = operator?.unary ?? false;
      return `Keep rows where ${step.column} ${operator?.label ?? ""}${
        unary ? "" : ` ${JSON.stringify(step.value)}`
      }`;
    }
    case "sort":
      return `Sort by ${step.column} ${step.desc ? "descending" : "ascending"}`;
    case "top_n":
      return `Keep the first ${step.n} rows`;
    case "distinct":
      return (step.columns ?? []).length > 0
        ? `Remove duplicates of ${(step.columns ?? []).join(", ")}`
        : "Remove duplicate rows";
    default:
      return step.kind;
  }
}

function StepBuilder({
  columns,
  onAdd,
}: {
  columns: string[];
  onAdd: (step: TableStep) => void;
}) {
  const [kind, setKind] = useState<TableStep["kind"]>("group_by");
  const [column, setColumn] = useState(columns[0] ?? "");
  const [by, setBy] = useState<string[]>([]);
  const [fn, setFn] = useState<AggregateSpec["fn"]>("count");
  const [aggColumn, setAggColumn] = useState(columns[0] ?? "");
  const [alias, setAlias] = useState("");
  const [operator, setOperator] = useState("eq");
  const [value, setValue] = useState("");
  const [desc, setDesc] = useState(true);
  const [n, setN] = useState(10);

  const unary = OPERATORS.find((entry) => entry.value === operator)?.unary ?? false;

  const build = (): TableStep | null => {
    switch (kind) {
      case "group_by": {
        if (needsColumn(fn) && !aggColumn) return null;
        const aggregate: AggregateSpec = {
          fn,
          ...(needsColumn(fn) ? { column: aggColumn } : {}),
          ...(alias.trim() ? { as: alias.trim() } : {}),
        };
        return { kind, by, aggregates: [aggregate] };
      }
      case "filter":
        if (!column) return null;
        return {
          kind,
          column,
          operator: operator as TableStep["operator"],
          // Numbers are compared as numbers; a filter typed as text against a
          // numeric column would otherwise never match.
          value: unary
            ? null
            : value !== "" && !Number.isNaN(Number(value))
              ? Number(value)
              : value,
        };
      case "sort":
        return column ? { kind, column, desc } : null;
      case "top_n":
        return { kind, n };
      case "distinct":
        return { kind, columns: by };
      default:
        return null;
    }
  };

  const toggle = (name: string) =>
    setBy((current) =>
      current.includes(name)
        ? current.filter((entry) => entry !== name)
        : [...current, name],
    );

  return (
    <div className="step-builder">
      <select
        value={kind}
        onChange={(event) => setKind(event.target.value as TableStep["kind"])}
        aria-label="Step kind"
      >
        {KINDS.map((entry) => (
          <option key={entry.value} value={entry.value}>
            {entry.label}
          </option>
        ))}
      </select>

      {(kind === "group_by" || kind === "distinct") && (
        <div className="column-picks">
          {columns.map((name) => (
            <label key={name} className="check">
              <input
                type="checkbox"
                checked={by.includes(name)}
                onChange={() => toggle(name)}
              />
              {name}
            </label>
          ))}
        </div>
      )}

      {kind === "group_by" && (
        <>
          <select
            value={fn}
            onChange={(event) =>
              setFn(event.target.value as AggregateSpec["fn"])
            }
            aria-label="Aggregate"
          >
            {AGGREGATES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
          {needsColumn(fn) && (
            <select
              value={aggColumn}
              onChange={(event) => setAggColumn(event.target.value)}
              aria-label="Aggregate column"
            >
              {columns.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          )}
          <input
            value={alias}
            onChange={(event) => setAlias(event.target.value)}
            placeholder="call it…"
            aria-label="Result name"
          />
        </>
      )}

      {(kind === "filter" || kind === "sort") && (
        <select
          value={column}
          onChange={(event) => setColumn(event.target.value)}
          aria-label="Column"
        >
          {columns.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      )}

      {kind === "filter" && (
        <>
          <select
            value={operator}
            onChange={(event) => setOperator(event.target.value)}
            aria-label="Comparison"
          >
            {OPERATORS.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
          {!unary && (
            <input
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="value"
              aria-label="Value"
            />
          )}
        </>
      )}

      {kind === "sort" && (
        <select
          value={desc ? "desc" : "asc"}
          onChange={(event) => setDesc(event.target.value === "desc")}
          aria-label="Direction"
        >
          <option value="desc">largest first</option>
          <option value="asc">smallest first</option>
        </select>
      )}

      {kind === "top_n" && (
        <input
          type="number"
          min={1}
          value={n}
          onChange={(event) => setN(Number(event.target.value) || 1)}
          aria-label="Row count"
        />
      )}

      <button
        type="button"
        className="btn btn-sm"
        onClick={() => {
          const step = build();
          if (step) onAdd(step);
        }}
      >
        Add step
      </button>
    </div>
  );
}

/**
 * Steps that act on the whole table rather than one record at a time.
 *
 * Kept apart from the row steps because they run somewhere else. A mapper
 * reshapes records on their way to the destination and can only ever see one;
 * grouping, sorting and deduplicating need the whole result, so these compile
 * to SQL that runs after the data has landed. Presenting them as one list
 * would suggest they happen together, and they do not.
 */
export function TableSteps({
  steps,
  columns,
  sql,
  streams,
  stream,
  onChange,
}: {
  steps: TableStep[];
  columns: string[];
  sql: string | null;
  streams: string[];
  stream: string | null;
  onChange: (next: TableStep[]) => void;
}) {
  // With no stream chosen the preview holds records from every stream the tap
  // announced, and grouping across two unrelated entities is not a question
  // with an answer - the columns of one are absent from the other, so they
  // collect in a null bucket. A run would never do this: a model reads one
  // table.
  const ambiguous = streams.length > 1 && !stream;

  return (
    <div className="section">
      <div className="section-head">
        <h2>Table steps</h2>
        {steps.length > 0 && (
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => onChange([])}
          >
            Clear
          </button>
        )}
      </div>

      <p className="page-sub">
        Applied after the steps above, over the whole result. A mapper only
        ever sees one record, so these run as SQL once the data has landed.
      </p>

      {ambiguous && (
        <p className="actions-hint">
          Choose a stream above first. These rows come from{" "}
          {streams.length} streams at once, and grouping across them would
          count records that have nothing to do with each other.
        </p>
      )}

      {steps.length === 0 ? (
        <p className="page-sub">
          No table steps — the rows above are one per extracted record.
        </p>
      ) : (
        <ol className="step-list">
          {steps.map((step, index) => (
            <li key={index} className="step">
              <span>{describe(step)}</span>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() =>
                  onChange(steps.filter((_, at) => at !== index))
                }
                aria-label={`Remove step ${index + 1}`}
              >
                Remove
              </button>
            </li>
          ))}
        </ol>
      )}

      {columns.length > 0 && !ambiguous && (
        <StepBuilder
          columns={columns}
          onAdd={(step) => onChange([...steps, step])}
        />
      )}

      {sql && (
        <details className="sql-preview">
          <summary>The query this becomes</summary>
          <pre>
            <code>{sql}</code>
          </pre>
        </details>
      )}
    </div>
  );
}
