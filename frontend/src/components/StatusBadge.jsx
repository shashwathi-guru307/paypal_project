const LABELS = {
  CREATED: "Created",
  FUNDED: "Funded",
  SUBMITTED: "Submitted",
  REVIEWED: "Reviewed",
  APPROVED: "Approved",
  PAID: "Paid",
  REJECTED: "Rejected",
};

/** Colour-coded status pill; unknown statuses still render readably. */
export default function StatusBadge({ status }) {
  const key = String(status || "").toUpperCase();
  return (
    <span className={`badge badge-${key.toLowerCase() || "unknown"}`}>
      {LABELS[key] || key || "Unknown"}
    </span>
  );
}
