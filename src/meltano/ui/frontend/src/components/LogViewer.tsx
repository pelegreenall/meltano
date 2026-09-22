import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { runEventsUrl, toLogRecord, type LogRecord, type RunStatus } from "../api";

interface Props {
  runId: string;
  /** Called when the stream reports the run reached a terminal state. */
  onFinished: (status: RunStatus) => void;
}

/** Streams a run's output over SSE.
 *
 * `EventSource` cannot set an Authorization header, which is one reason the
 * server issues a session cookie: the stream is same-origin, so the browser
 * attaches it automatically. */
export function LogViewer({ runId, onFinished }: Props) {
  const [records, setRecords] = useState<LogRecord[]>([]);
  const [connected, setConnected] = useState(false);
  const [follow, setFollow] = useState(true);
  const bodyRef = useRef<HTMLDivElement>(null);

  // `onFinished` is called from inside the effect but must not re-subscribe
  // the stream when the parent re-renders.
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  useEffect(() => {
    setRecords([]);
    const source = new EventSource(runEventsUrl(runId));

    source.addEventListener("open", () => setConnected(true));

    source.addEventListener("log", (event) => {
      const id = Number((event as MessageEvent).lastEventId) || 0;
      setRecords((current) => [
        ...current,
        toLogRecord(id, JSON.parse((event as MessageEvent).data)),
      ]);
    });

    source.addEventListener("end", (event) => {
      const payload = JSON.parse((event as MessageEvent).data);
      setConnected(false);
      finishedRef.current(payload.status as RunStatus);
      // The run is over, so stop the browser's automatic reconnect.
      source.close();
    });

    source.addEventListener("error", () => setConnected(false));

    return () => source.close();
  }, [runId]);

  useLayoutEffect(() => {
    if (follow && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [records, follow]);

  return (
    <div className="log">
      <div className="log-bar">
        <span className="badge badge-idle">
          {connected ? `Streaming · ${records.length} lines` : `${records.length} lines`}
        </span>
        <label className="check">
          <input
            type="checkbox"
            checked={follow}
            onChange={(event) => setFollow(event.target.checked)}
          />
          Follow output
        </label>
      </div>

      <div className="log-body" ref={bodyRef} role="log" aria-live="polite">
        {records.length === 0 ? (
          <div className="empty" style={{ padding: "32px 16px" }}>
            {connected ? "Waiting for output…" : "This run produced no output."}
          </div>
        ) : (
          records.map((record) => <LogLine key={record.id} record={record} />)
        )}
      </div>
    </div>
  );
}

function LogLine({ record }: { record: LogRecord }) {
  return (
    <div className="log-line">
      <span className="log-time">{formatTime(record.timestamp)}</span>
      <span className={`log-level lv-${record.level}`}>{record.level}</span>
      <span className="log-msg">
        {record.stream && <span className="log-stream">[{record.stream}] </span>}
        {record.metric ? (
          <span className="log-metric">
            {record.metric.stream ?? "stream"} · {record.metric.metric}{" "}
            {record.metric.value.toLocaleString()}
          </span>
        ) : (
          record.message
        )}
      </span>
    </div>
  );
}

function formatTime(timestamp: string | null): string {
  if (!timestamp) return "";
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleTimeString(undefined, { hour12: false });
}
