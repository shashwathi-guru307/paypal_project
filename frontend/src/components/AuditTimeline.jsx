function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Every state change the server recorded, oldest first. */
export default function AuditTimeline({ entries }) {
  if (!Array.isArray(entries) || entries.length === 0) {
    return <p className="muted">No audit entries yet.</p>;
  }

  return (
    <ol className="timeline">
      {entries.map((entry, index) => (
        <li key={`${entry.ts || "t"}-${index}`}>
          <span className="timeline-dot" aria-hidden="true" />
          <div className="timeline-body">
            <div className="timeline-head">
              <span className="timeline-event">{entry.event}</span>
              <time className="timeline-ts" dateTime={entry.ts || undefined}>
                {formatTime(entry.ts)}
              </time>
            </div>
            {entry.detail ? <p className="timeline-detail">{entry.detail}</p> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
