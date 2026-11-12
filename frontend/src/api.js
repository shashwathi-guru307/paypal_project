/**
 * Thin fetch wrapper around the EscrowPilot API.
 *
 * Every failure becomes an Error whose message is safe to show on screen: the
 * backend's own `message` (plus PayPal's debug_id) or its `detail`, never a raw
 * response body. Nothing here logs.
 */

const BASE_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");

function describeError(status, payload) {
  if (payload && typeof payload.message === "string" && payload.message) {
    return payload.debug_id ? `${payload.message} (debug_id: ${payload.debug_id})` : payload.message;
  }
  if (payload && typeof payload.detail === "string" && payload.detail) {
    return payload.detail;
  }
  if (payload && Array.isArray(payload.detail)) {
    const fields = payload.detail
      .map((item) => {
        const path = Array.isArray(item.loc) ? item.loc.slice(1).join(".") : "";
        return `${path || "request"}: ${item.msg || "invalid value"}`;
      })
      .join("; ");
    if (fields) return fields;
  }
  return `Request failed (HTTP ${status}).`;
}

async function request(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error(`Cannot reach the API at ${BASE_URL}. Is the backend running?`);
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    throw new Error(describeError(response.status, payload));
  }
  return payload;
}

const taskPath = (taskId, suffix = "") => `/tasks/${encodeURIComponent(taskId)}${suffix}`;

export function createTask(task) {
  return request("/tasks", { method: "POST", body: task });
}

export function listTasks() {
  return request("/tasks");
}

export function getTask(taskId) {
  return request(taskPath(taskId));
}

export function captureTask(taskId) {
  return request(taskPath(taskId, "/capture"), { method: "POST" });
}

export function submitWork(taskId, submission) {
  return request(taskPath(taskId, "/submit"), { method: "POST", body: { submission } });
}

export function approveTask(taskId) {
  return request(taskPath(taskId, "/approve"), { method: "POST" });
}

export function rejectTask(taskId) {
  return request(taskPath(taskId, "/reject"), { method: "POST" });
}

export function retryPayout(taskId) {
  return request(taskPath(taskId, "/retry-payout"), { method: "POST" });
}

export function getPayoutStatus(taskId) {
  return request(taskPath(taskId, "/payout-status"));
}

export { BASE_URL };
