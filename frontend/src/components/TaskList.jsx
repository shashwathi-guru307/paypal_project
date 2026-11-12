import StatusBadge from "./StatusBadge";

/**
 * Rows of tasks; the open row renders `renderDetail(task)` underneath.
 * Selection is controlled so the parent keeps one task open at a time.
 */
export default function TaskList({ tasks, selectedId, onSelect, renderDetail, emptyMessage }) {
  if (!Array.isArray(tasks) || tasks.length === 0) {
    return <p className="empty">{emptyMessage || "No tasks yet."}</p>;
  }

  return (
    <ul className="task-rows">
      {tasks.map((task) => {
        const open = task.id === selectedId;
        return (
          <li key={task.id} className={open ? "task-row open" : "task-row"}>
            <button
              type="button"
              className="task-row-head"
              aria-expanded={open}
              onClick={() => onSelect(open ? null : task.id)}
            >
              <StatusBadge status={task.status} />
              <span className="task-row-title">{task.title}</span>
              <span className="task-row-amount">${task.amount}</span>
              <span className="task-row-caret" aria-hidden="true">
                {open ? "▾" : "▸"}
              </span>
            </button>
            {open ? <div className="task-row-body">{renderDetail(task)}</div> : null}
          </li>
        );
      })}
    </ul>
  );
}
