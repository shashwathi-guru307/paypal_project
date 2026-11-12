import { useState } from "react";

const EMPTY_FORM = {
  title: "",
  requirements: "",
  amount: "1.00",
  client_name: "",
  freelancer_email: "",
};

const MAX_AMOUNT = 100;

function validate(form) {
  const errors = {};

  const title = form.title.trim();
  if (!title) errors.title = "A title is required.";
  else if (title.length > 120) errors.title = "Keep the title under 120 characters.";

  const requirements = form.requirements.trim();
  if (requirements.length < 10) errors.requirements = "Describe the work in at least 10 characters.";
  else if (requirements.length > 2000) errors.requirements = "Keep the requirements under 2000 characters.";

  const amount = form.amount.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(amount)) errors.amount = "Use a number like 1.00 (at most two decimals).";
  else if (Number(amount) <= 0) errors.amount = "The amount must be greater than zero.";
  else if (Number(amount) > MAX_AMOUNT) errors.amount = `The amount must not exceed ${MAX_AMOUNT.toFixed(2)} USD.`;

  const clientName = form.client_name.trim();
  if (!clientName) errors.client_name = "Your name is required.";
  else if (clientName.length > 80) errors.client_name = "Keep your name under 80 characters.";

  const email = form.freelancer_email.trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    errors.freelancer_email = "Enter the freelancer's sandbox PayPal email.";
  }

  return errors;
}

/** Same rules as backend models.TaskCreate, checked before spending an API call. */
export default function TaskForm({ onCreate }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  const update = (field) => (event) => {
    const { value } = event.target;
    setForm((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
  };

  const submit = async (event) => {
    event.preventDefault();
    const found = validate(form);
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    setSubmitting(true);
    setFormError("");
    try {
      await onCreate({
        title: form.title.trim(),
        requirements: form.requirements.trim(),
        amount: form.amount.trim(),
        currency: "USD",
        client_name: form.client_name.trim(),
        freelancer_email: form.freelancer_email.trim(),
      });
      setForm(EMPTY_FORM);
    } catch (error) {
      setFormError(error.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="task-form" onSubmit={submit} noValidate>
      <h2>New task</h2>
      <p className="muted">
        Creating a task opens a PayPal order. The buyer pays into that order; the money stays with the
        client until a human releases it - the AI only advises.
      </p>

      <div className="field">
        <label htmlFor="task-title">Title</label>
        <input
          id="task-title"
          value={form.title}
          onChange={update("title")}
          maxLength={120}
          placeholder="Palindrome checker"
          aria-invalid={Boolean(errors.title)}
        />
        {errors.title ? <p className="form-error">{errors.title}</p> : null}
      </div>

      <div className="field">
        <label htmlFor="task-requirements">Requirements</label>
        <textarea
          id="task-requirements"
          value={form.requirements}
          onChange={update("requirements")}
          rows={4}
          maxLength={2000}
          placeholder="Write a Python function is_palindrome(s) that ignores case and punctuation, with at least three test cases."
          aria-invalid={Boolean(errors.requirements)}
        />
        <div className="form-foot">
          <span className="hint">At least 10 characters.</span>
          <span className="counter">{form.requirements.length} / 2000</span>
        </div>
        {errors.requirements ? <p className="form-error">{errors.requirements}</p> : null}
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="task-amount">Amount (USD)</label>
          <input
            id="task-amount"
            value={form.amount}
            onChange={update("amount")}
            inputMode="decimal"
            aria-invalid={Boolean(errors.amount)}
          />
          <span className="hint">Max {MAX_AMOUNT.toFixed(2)} per task.</span>
          {errors.amount ? <p className="form-error">{errors.amount}</p> : null}
        </div>

        <div className="field">
          <label htmlFor="task-client">Your name</label>
          <input
            id="task-client"
            value={form.client_name}
            onChange={update("client_name")}
            maxLength={80}
            placeholder="Ada Client"
            aria-invalid={Boolean(errors.client_name)}
          />
          {errors.client_name ? <p className="form-error">{errors.client_name}</p> : null}
        </div>
      </div>

      <div className="field">
        <label htmlFor="task-email">Freelancer sandbox email</label>
        <input
          id="task-email"
          value={form.freelancer_email}
          onChange={update("freelancer_email")}
          placeholder="sb-freelancer@business.example.com"
          aria-invalid={Boolean(errors.freelancer_email)}
        />
        <span className="hint">Must be a PayPal sandbox account - the payout goes here.</span>
        {errors.freelancer_email ? <p className="form-error">{errors.freelancer_email}</p> : null}
      </div>

      {formError ? <p className="inline-error">{formError}</p> : null}

      <button type="submit" className="btn btn-primary" disabled={submitting}>
        {submitting ? "Creating task..." : "Create task"}
      </button>
    </form>
  );
}
