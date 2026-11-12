import { useCallback, useEffect, useMemo, useState } from "react";

import * as api from "./api";
import ReviewPanel from "./components/ReviewPanel";
import StatusBadge from "./components/StatusBadge";
import TaskCard from "./components/TaskCard";
import TaskForm from "./components/TaskForm";
import TaskList from "./components/TaskList";
import Toast from "./components/Toast";

const ROLES = [
  { id: "client", label: "Client" },
  { id: "freelancer", label: "Freelancer" },
  { id: "review", label: "Review" },
];

const MAX_SUBMISSION = 20000;
const ALREADY_FUNDED = ["FUNDED", "SUBMITTED", "REVIEWED", "APPROVED", "PAID", "REJECTED"];

function rankForReview(task) {
  if (task.status === "REVIEWED") return 0;
  if (task.status === "APPROVED") return 1;
  return 2;
}

function friendlyCaptureError(message) {
  if (/ORDER_NOT_APPROVED|not approved|UNPROCESSABLE_ENTITY/i.test(message)) {
    return "Payment was not completed yet. Go back and try Pay with PayPal again.";
  }
  return message;
}

/** PayPal sends the buyer here after checkout; capture once, never twice. */
function ReturnPage({ onHome }) {
  const [phase, setPhase] = useState("working");
  const [message, setMessage] = useState("");
  const [task, setTask] = useState(null);

  useEffect(() => {
    const taskId = new URLSearchParams(window.location.search).get("task_id");
    let alive = true;

    async function confirmPayment() {
      if (!taskId) {
        setPhase("error");
        setMessage("This return link is missing its task id, so nothing was captured.");
        return;
      }
      try {
        const current = await api.getTask(taskId);
        if (!alive) return;

        // Refreshing this page must never capture an already-funded order again.
        if (ALREADY_FUNDED.includes(current.status)) {
          setTask(current);
          setPhase("success");
          setMessage("This payment was already confirmed - the task is funded.");
          return;
        }

        const funded = await api.captureTask(taskId);
        if (!alive) return;
        setTask(funded);
        setPhase("success");
        setMessage(`Payment confirmed: $${funded.amount} ${funded.currency} is now held in escrow.`);
      } catch (error) {
        if (!alive) return;
        setPhase("error");
        setMessage(friendlyCaptureError(error.message));
      }
    }

    confirmPayment();
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="centered">
      <div className="card return-card">
        <h1 className="brand-title">EscrowPilot</h1>

        {phase === "working" ? (
          <p className="working">
            <span className="spinner" aria-hidden="true" />
            Confirming your payment...
          </p>
        ) : null}

        {phase === "success" ? (
          <>
            <p className="notice notice-ok">{message}</p>
            {task ? (
              <p>
                Task <span className="mono">{task.id}</span> is now{" "}
                <StatusBadge status={task.status} />
              </p>
            ) : null}
          </>
        ) : null}

        {phase === "error" ? <p className="notice notice-error">{message}</p> : null}

        <button type="button" className="btn btn-primary" onClick={onHome}>
          Back to EscrowPilot
        </button>
      </div>
    </div>
  );
}

/** PayPal's cancel_url. Nothing was captured, so the task is still CREATED. */
function CancelPage({ onHome }) {
  return (
    <div className="centered">
      <div className="card return-card">
        <h1 className="brand-title">EscrowPilot</h1>
        <p className="notice notice-warn">Payment cancelled. The task is not funded yet.</p>
        <p className="muted">
          Reopen the task in the Client tab and choose Pay with PayPal when you are ready.
        </p>
        <button type="button" className="btn btn-primary" onClick={onHome}>
          Back to EscrowPilot
        </button>
      </div>
    </div>
  );
}

/** Submission box shown to the freelancer while a task is FUNDED. */
function SubmissionForm({ task, busy, onSubmit }) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const tooLong = text.length > MAX_SUBMISSION;
  const disabled = busy || tooLong || text.trim().length === 0;

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    try {
      await onSubmit(task.id, text);
      setText("");
    } catch (submitError) {
      setError(submitError.message);
    }
  };

  return (
    <form className="submission-form" onSubmit={submit}>
      <label htmlFor={`submission-${task.id}`}>Your submission</label>
      <textarea
        id={`submission-${task.id}`}
        rows={9}
        value={text}
        onChange={(event) => setText(event.target.value)}
        maxLength={MAX_SUBMISSION}
        placeholder="Paste the code, the text, or a link to what you delivered."
      />
      <div className="form-foot">
        <span className={tooLong ? "counter over" : "counter"}>
          {text.length} / {MAX_SUBMISSION}
        </span>
        <button type="submit" className="btn btn-primary" disabled={disabled}>
          {busy ? "AI is reviewing the submission..." : "Submit for AI review"}
        </button>
      </div>
      {error ? <p className="inline-error">{error}</p> : null}
    </form>
  );
}

export default function App() {
  const [route, setRoute] = useState(() => window.location.pathname);

  const goHome = useCallback(() => {
    window.history.replaceState({}, "", "/");
    setRoute("/");
  }, []);

  if (route === "/return") return <ReturnPage onHome={goHome} />;
  if (route === "/cancel") return <CancelPage onHome={goHome} />;
  return <MainApp />;
}

function MainApp() {
  const [role, setRole] = useState("client");
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [openIds, setOpenIds] = useState({ client: null, review: null });
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState(null);

  const notify = useCallback((message, type = "info") => setToast({ message, type, at: Date.now() }), []);
  const dismissToast = useCallback(() => setToast(null), []);

  const refresh = useCallback(async ({ quiet = false } = {}) => {
    try {
      const data = await api.listTasks();
      setTasks(Array.isArray(data) ? data : []);
      setListError("");
    } catch (error) {
      if (!quiet) setListError(error.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const needsPolling = tasks.some((task) => task.status === "SUBMITTED" || task.status === "APPROVED");
  useEffect(() => {
    if (!needsPolling) return undefined;
    const timer = setInterval(() => refresh({ quiet: true }), 5000);
    return () => clearInterval(timer);
  }, [needsPolling, refresh]);

  const run = useCallback(
    async (key, action, successMessage) => {
      setBusy(key);
      try {
        const result = await action();
        if (successMessage) notify(successMessage, "success");
        await refresh({ quiet: true });
        return result;
      } catch (error) {
        notify(error.message, "error");
        throw error;
      } finally {
        setBusy(null);
      }
    },
    [notify, refresh],
  );

  const handleCreate = useCallback(
    async (payload) => {
      const task = await run(
        "create",
        () => api.createTask(payload),
        "Task created. Pay with PayPal to fund it.",
      );
      setOpenIds((current) => ({ ...current, client: task.id }));
      return task;
    },
    [run],
  );

  const handleCapture = (taskId) =>
    run(`capture:${taskId}`, () => api.captureTask(taskId), "Payment confirmed - the task is funded.");

  const handleSubmit = (taskId, submission) =>
    run(
      `submit:${taskId}`,
      () => api.submitWork(taskId, submission),
      "Work submitted - the AI verdict is ready.",
    );

  const handleApprove = (taskId) =>
    run(`approve:${taskId}`, () => api.approveTask(taskId), "Payout sent - the task is paid.");

  const handleReject = (taskId) =>
    run(`reject:${taskId}`, () => api.rejectTask(taskId), "Task rejected. No payout was made.");

  const handleRetry = (taskId) =>
    run(`retry:${taskId}`, () => api.retryPayout(taskId), "Payout retried.");

  const handlePayoutStatus = async (taskId) => {
    try {
      return await api.getPayoutStatus(taskId);
    } catch (error) {
      notify(error.message, "error");
      throw error;
    }
  };

  const pay = (task) => {
    if (!task.approve_url) {
      notify("This task has no PayPal approval link. Create a new task.", "error");
      return;
    }
    window.location.href = task.approve_url;
  };

  const select = (tab, taskId) => setOpenIds((current) => ({ ...current, [tab]: taskId }));

  const freelancerTasks = useMemo(
    () => tasks.filter((task) => ALREADY_FUNDED.includes(task.status)),
    [tasks],
  );

  const reviewTasks = useMemo(
    () => [...tasks].sort((a, b) => rankForReview(a) - rankForReview(b)),
    [tasks],
  );

  const reviewId =
    openIds.review && reviewTasks.some((task) => task.id === openIds.review)
      ? openIds.review
      : reviewTasks[0]?.id || null;
  const reviewTask = reviewTasks.find((task) => task.id === reviewId) || null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            EP
          </span>
          <div>
            <h1 className="brand-title">EscrowPilot</h1>
            <p className="brand-sub">AI-reviewed micro-task escrow on PayPal</p>
          </div>
        </div>

        <div className="topbar-right">
          <span className="sandbox">SANDBOX MODE</span>
          <div className="role-tabs" role="tablist" aria-label="Choose a role">
            {ROLES.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={role === item.id}
                className={role === item.id ? "role-tab active" : "role-tab"}
                onClick={() => setRole(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="banner">AI proposes → Human approves → PayPal confirms</div>

      <main className="layout">
        {loading ? <p className="muted">Loading tasks...</p> : null}
        {listError ? <p className="notice notice-error">{listError}</p> : null}

        {role === "client" ? (
          <div className="split">
            <section className="panel form-panel">
              <TaskForm onCreate={handleCreate} />
            </section>

            <section className="panel">
              <div className="panel-head">
                <h2>Your tasks</h2>
                <p className="muted">Select a task to see its escrow details and audit trail.</p>
              </div>

              <TaskList
                tasks={tasks}
                selectedId={openIds.client}
                onSelect={(taskId) => select("client", taskId)}
                emptyMessage="No tasks yet. Create one on the left."
                renderDetail={(task) => (
                  <TaskCard
                    task={task}
                    actions={
                      task.status === "CREATED" ? (
                        <>
                          <button
                            type="button"
                            className="btn btn-paypal"
                            disabled={Boolean(busy)}
                            onClick={() => pay(task)}
                          >
                            Pay ${task.amount} with PayPal
                          </button>
                          <span className="hint">
                            Opens the PayPal sandbox checkout in this tab. The payment is confirmed when
                            PayPal sends you back.
                          </span>
                        </>
                      ) : null
                    }
                  >
                    {task.status === "CREATED" ? (
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={Boolean(busy)}
                        onClick={() => handleCapture(task.id)}
                      >
                        {busy === `capture:${task.id}`
                          ? "Confirming payment..."
                          : "I have paid - confirm payment"}
                      </button>
                    ) : null}
                  </TaskCard>
                )}
              />
            </section>
          </div>
        ) : null}

        {role === "freelancer" ? (
          <section className="panel">
            <div className="panel-head">
              <h2>Work to deliver</h2>
              <p className="muted">
                Funded tasks you can submit. The AI reviews the submission and prepares a verdict for the
                client - it never pays on its own.
              </p>
            </div>

            {!loading && freelancerTasks.length === 0 ? (
              <p className="empty">Nothing funded yet. A task appears here once a client pays.</p>
            ) : null}

            {freelancerTasks.map((task) => (
              <article key={task.id} className="panel-card">
                <TaskCard task={task}>
                  {task.status === "FUNDED" ? (
                    <SubmissionForm
                      task={task}
                      busy={busy === `submit:${task.id}`}
                      onSubmit={handleSubmit}
                    />
                  ) : null}
                  {task.status === "SUBMITTED" ? (
                    <p className="notice notice-warn">
                      <span className="spinner" aria-hidden="true" /> AI is reviewing the submission...
                    </p>
                  ) : null}
                </TaskCard>
              </article>
            ))}
          </section>
        ) : null}

        {role === "review" ? (
          <section className="panel">
            <div className="panel-head">
              <h2>Review queue</h2>
              <p className="muted">
                The AI has an opinion. Only a human approval releases the money.
              </p>
            </div>

            {!loading && reviewTasks.length === 0 ? (
              <p className="empty">No tasks yet. Create one in the Client tab.</p>
            ) : null}

            {reviewTasks.length > 0 ? (
              <ul className="queue">
                {reviewTasks.map((task) => (
                  <li key={task.id}>
                    <button
                      type="button"
                      className={task.id === reviewId ? "queue-item active" : "queue-item"}
                      aria-current={task.id === reviewId}
                      onClick={() => select("review", task.id)}
                    >
                      <StatusBadge status={task.status} />
                      <span className="queue-title">{task.title}</span>
                      <span className="queue-meta">
                        ${task.amount} · {task.freelancer_email}
                      </span>
                      {task.ai_verdict ? (
                        <span className="queue-score">{task.ai_verdict.score}/100</span>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}

            {reviewTask ? (
              <ReviewPanel
                task={reviewTask}
                busy={busy}
                onApprove={handleApprove}
                onReject={handleReject}
                onRetry={handleRetry}
                onRefreshStatus={handlePayoutStatus}
              />
            ) : null}
          </section>
        ) : null}
      </main>

      <Toast toast={toast} onClose={dismissToast} />
    </div>
  );
}
