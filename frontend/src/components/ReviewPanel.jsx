import { useEffect, useState } from "react";

import AuditTimeline from "./AuditTimeline";
import StatusBadge from "./StatusBadge";
import VerdictBar, { isFallbackVerdict } from "./VerdictBar";

/** Modal confirmation; closes on Escape, on the Cancel button, or on the backdrop. */
function ConfirmDialog({ title, children, confirmLabel, busy, onConfirm, onCancel }) {
  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h3 id="confirm-title">{title}</h3>
        <div className="modal-body">{children}</div>
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={onConfirm} disabled={busy}>
            {busy ? "Paying..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * The demo centerpiece: requirements next to the submission, the AI verdict,
 * and the only buttons that can move money.
 */
export default function ReviewPanel({ task, busy, onApprove, onReject, onRetry, onRefreshStatus }) {
  const [confirming, setConfirming] = useState(false);
  const [payout, setPayout] = useState(null);
  const [payoutError, setPayoutError] = useState("");

  const mine = (action) => busy === `${action}:${task.id}`;
  const anyBusy = Boolean(busy);
  const verdict = task.ai_verdict;

  const refreshPayout = async () => {
    setPayoutError("");
    try {
      setPayout(await onRefreshStatus(task.id));
    } catch (error) {
      setPayoutError(error.message);
    }
  };

  const confirmApprove = async () => {
    setConfirming(false);
    await onApprove(task.id);
  };

  return (
    <section className="review-panel" aria-label={`Review ${task.title}`}>
      <div className="review-head">
        <div>
          <h2>{task.title}</h2>
          <p className="muted">
            {task.client_name} pays {task.freelancer_email}
          </p>
        </div>
        <div className="task-card-right">
          <span className="amount">
            ${task.amount} <small>{task.currency}</small>
          </span>
          <StatusBadge status={task.status} />
        </div>
      </div>

      <div className="review-grid">
        <section className="block">
          <h4>Requirements</h4>
          <p className="pre">{task.requirements}</p>
        </section>
        <section className="block">
          <h4>Submission</h4>
          {task.submission ? (
            <pre className="submission">{task.submission}</pre>
          ) : (
            <p className="muted">Nothing submitted yet.</p>
          )}
        </section>
      </div>

      <section className="block">
        <h4>AI verdict</h4>
        {verdict ? <VerdictBar verdict={verdict} /> : <p className="muted">No verdict yet.</p>}
        {isFallbackVerdict(verdict) ? (
          <p className="notice notice-warn">
            Automated review was unavailable. Please review manually.
          </p>
        ) : null}
        <p className="hint">
          The AI opinion is advisory: no payout happens until a human approves below.
        </p>
      </section>

      {task.status === "REVIEWED" ? (
        <div className="review-actions">
          <button
            type="button"
            className="btn btn-approve btn-lg"
            disabled={anyBusy}
            onClick={() => setConfirming(true)}
          >
            {mine("approve") ? "Paying..." : "Approve and pay"}
          </button>
          <button
            type="button"
            className="btn btn-danger btn-lg"
            disabled={anyBusy}
            onClick={() => onReject(task.id)}
          >
            {mine("reject") ? "Rejecting..." : "Reject"}
          </button>
        </div>
      ) : null}

      {task.status === "APPROVED" ? (
        <div className="review-actions">
          <button
            type="button"
            className="btn btn-primary"
            disabled={anyBusy}
            onClick={() => onRetry(task.id)}
          >
            {mine("retry") ? "Retrying payout..." : "Retry payout"}
          </button>
          <p className="hint">
            The payout failed after approval. Retrying is safe - PayPal de-duplicates the batch.
          </p>
        </div>
      ) : null}

      {task.status === "PAID" ? (
        <section className="block payout">
          <h4>Payout</h4>
          <dl className="kv">
            <dt>Batch</dt>
            <dd className="mono">{task.payout_batch_id || "-"}</dd>
            <dt>Recorded status</dt>
            <dd>
              <strong>{task.payout_status || "PENDING"}</strong>
            </dd>
          </dl>
          <button type="button" className="btn" onClick={refreshPayout} disabled={anyBusy}>
            Refresh status
          </button>
          {payout ? (
            <p className="muted">
              PayPal batch: {payout.batch_status || "unknown"} · item: {payout.item_status || "unknown"}
            </p>
          ) : null}
          {payoutError ? <p className="inline-error">{payoutError}</p> : null}
        </section>
      ) : null}

      <section className="block">
        <h4>Audit log</h4>
        <AuditTimeline entries={task.audit_log} />
      </section>

      {confirming ? (
        <ConfirmDialog
          title="Release the payment?"
          confirmLabel="Approve and pay"
          busy={mine("approve")}
          onConfirm={confirmApprove}
          onCancel={() => setConfirming(false)}
        >
          <p>
            You are about to pay{" "}
            <strong>
              ${task.amount} {task.currency}
            </strong>{" "}
            to <strong>{task.freelancer_email}</strong>. This cannot be undone.
          </p>
        </ConfirmDialog>
      ) : null}
    </section>
  );
}
