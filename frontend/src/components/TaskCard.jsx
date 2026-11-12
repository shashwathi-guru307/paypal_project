import AuditTimeline from "./AuditTimeline";
import StatusBadge from "./StatusBadge";
import VerdictBar from "./VerdictBar";

/** Compact key/value list of the PayPal references behind a task. */
function PaymentReferences({ task }) {
  return (
    <details className="meta">
      <summary>Payment references</summary>
      <dl>
        <dt>Task id</dt>
        <dd className="mono">{task.id}</dd>
        <dt>PayPal order</dt>
        <dd className="mono">{task.paypal_order_id || "-"}</dd>
        <dt>Capture</dt>
        <dd className="mono">{task.capture_id || "-"}</dd>
        <dt>Payout batch</dt>
        <dd className="mono">{task.payout_batch_id || "-"}</dd>
        <dt>Created</dt>
        <dd>{task.created_at}</dd>
      </dl>
    </details>
  );
}

/**
 * Full detail for one task. `children` renders between the verdict and the
 * actions; `actions` renders after them, above the audit log.
 */
export default function TaskCard({ task, actions = null, children = null }) {
  return (
    <div className="task-card">
      <div className="task-card-head">
        <div>
          <h3>{task.title}</h3>
          <p className="muted">
            {task.client_name} → {task.freelancer_email}
          </p>
        </div>
        <div className="task-card-right">
          <span className="amount">
            ${task.amount} <small>{task.currency}</small>
          </span>
          <StatusBadge status={task.status} />
        </div>
      </div>

      <section className="block">
        <h4>Requirements</h4>
        <p className="pre">{task.requirements}</p>
      </section>

      {task.submission ? (
        <section className="block">
          <h4>Submission</h4>
          <pre className="submission">{task.submission}</pre>
        </section>
      ) : null}

      {task.ai_verdict ? (
        <section className="block">
          <h4>AI verdict</h4>
          <VerdictBar verdict={task.ai_verdict} />
        </section>
      ) : null}

      {children}

      {actions ? <div className="task-actions">{actions}</div> : null}

      <section className="block">
        <h4>Audit log</h4>
        <AuditTimeline entries={task.audit_log} />
      </section>

      <PaymentReferences task={task} />
    </div>
  );
}
