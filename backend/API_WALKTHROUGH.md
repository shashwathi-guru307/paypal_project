# EscrowPilot - API walkthrough (PowerShell, PayPal sandbox)

A full manual run of the Phase 4 API with a **$1.00** task: create -> pay as the
sandbox buyer -> capture -> submit -> review -> approve -> payout.

Every command below is PowerShell (Windows). The store is in-memory unless
Firebase credentials are present, so **restarting the server erases tasks** - keep
the server running for the whole walkthrough.

All commands assume five variables set once (see step 0).

---

## 0. Configure and start

`.env` in the repo root must have (see `.env.example`):

- `PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com`
- `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` (sandbox app)
- `FRONTEND_URL=http://localhost:5173`
- `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`

```powershell
cd C:\paypal
.\.venv\Scripts\Activate.ps1

cd backend
uvicorn main:app --reload --port 8000
```

Expected startup output:

```
[db] No Firebase credentials at FIREBASE_CREDENTIALS_PATH - using the in-memory store (data is lost on restart).
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

Leave that window running. **Open a second PowerShell window** for the calls
below, and start it with the same two variables:

```powershell
$api = "http://localhost:8000"
```

FastAPI's clickable docs are at <http://localhost:8000/docs> - every endpoint
below can be tried there instead of typing commands.

Health check:

```powershell
Invoke-RestMethod -Uri "$api/health"
```

```json
{ "status": "ok" }
```

---

## 1. Create the task ($1.00)

```powershell
$body = @{
    title             = "Palindrome checker"
    requirements      = "Write a Python function is_palindrome(s) that ignores case and non-alphanumeric characters, with at least 3 example test cases."
    amount            = "1.00"
    client_name       = "Ada Client"
    freelancer_email  = "sb-buyer@example.com"
} | ConvertTo-Json

$task = Invoke-RestMethod -Method Post -Uri "$api/tasks" -ContentType "application/json" -Body $body
$task | ConvertTo-Json -Depth 6
$task.id
```

Expected (`201`):

```json
{
  "id": "63adeb2ccc084029a4e91320487028c5",
  "status": "CREATED",
  "amount": "1.00",
  "currency": "USD",
  "paypal_order_id": "5DG797587A020612W",
  "approve_url": "https://www.sandbox.paypal.com/checkoutnow?token=5DG797587A020612W",
  "capture_id": null,
  "submission": null,
  "ai_verdict": null,
  "payout_batch_id": null,
  "payout_status": null,
  "audit_log": [
    { "ts": "...", "event": "TASK_CREATED", "detail": "Task created for 1.00 USD" }
  ]
}
```

The task id is the only thing you need to carry forward. `status`, ids and
verdicts are never accepted from the client.

---

## 2. Pay as the sandbox buyer

```powershell
Start-Process $task.approve_url
```

In the browser:

1. Log in with a **sandbox Personal (buyer)** account - not the business account
   that owns the API app (PayPal blocks self-payment).
2. Confirm the amount shows **$1.00 USD**, then click **Pay**.

You land on `http://localhost:5173/return?task_id=...`. **The page failing to load
is expected** - the frontend is not built yet. What matters is that PayPal
redirects you there, which means the payment was approved.

The task is still `CREATED` at this point: nothing is funded until the capture is
verified server-side.

---

## 3. Capture and verify the funding

```powershell
$funded = Invoke-RestMethod -Method Post -Uri "$api/tasks/$($task.id)/capture"
$funded.status
$funded.capture_id
$funded.audit_log[-1]
```

Expected (`200`):

```json
{
  "status": "FUNDED",
  "capture_id": "4AB12345CD678901E",
  "audit_log": [
    { "event": "TASK_CREATED", "detail": "Task created for 1.00 USD" },
    { "event": "FUNDED", "detail": "PayPal capture 4AB12345CD678901E verified for 1.00 USD (status COMPLETED)" }
  ]
}
```

If you skipped step 2, PayPal itself refuses the capture and the API returns `502`
(observed against the real sandbox):

```json
{
  "message": "UNPROCESSABLE_ENTITY: The requested action could not be performed, semantically incorrect, or failed business validation. [ORDER_NOT_APPROVED]",
  "debug_id": "ca58b9f465b6d"
}
```

The task stays `CREATED`; pay the order and repeat the call.

A captured amount or currency that differs from the task returns `409` and also
leaves the task `CREATED`; the reason is in the `CAPTURE_REJECTED` audit entry.
Amounts are compared as decimals, so PayPal's `1.0` and the task's `1.00` count as
the same money.

---

## 4. Submit the work

```powershell
$submission = @'
def is_palindrome(s: str) -> bool:
    """True if s reads the same backwards, ignoring case and punctuation."""
    import re
    cleaned = re.sub(r"[^a-z0-9]", "", s.lower())
    return cleaned == cleaned[::-1]


if __name__ == "__main__":
    assert is_palindrome("A man, a plan, a canal: Panama") is True
    assert is_palindrome("race a car") is False
    assert is_palindrome("") is True
    print("3/3 tests passed")
'@

$reviewed = Invoke-RestMethod -Method Post -Uri "$api/tasks/$($task.id)/submit" `
    -ContentType "application/json" `
    -Body (@{ submission = $submission } | ConvertTo-Json)
$reviewed.status
```

Expected (`200`) after a few seconds - the call waits for the real model:

```json
{
  "status": "REVIEWED",
  "ai_verdict": {
    "pass": true,
    "score": 95,
    "reason": "Implements case- and punctuation-insensitive palindrome checking with three example tests.",
    "missing": []
  }
}
```

The review never raises. If the provider is unreachable you get
`{"pass": false, "score": 0, "reason": "Automated review failed. Manual review needed."}`
and the task still reaches `REVIEWED`, so a human can decide.

## 5. Read the verdict

```powershell
$reviewed.ai_verdict | Format-List
```

`pass` and `score` are advisory. **Nothing has been paid and nothing will be paid
automatically** - step 6 is the only way money moves. Approving the same task a
second time is rejected with `409` (`cannot approve a reviewed task while the
task is PAID (expected REVIEWED)`), so a double click cannot pay twice.

---

## 6. Approve - the only path to a payout

```powershell
$paid = Invoke-RestMethod -Method Post -Uri "$api/tasks/$($task.id)/approve"
$paid.status
$paid.payout_batch_id
$paid.payout_status
```

Expected (`200`):

```json
{
  "status": "PAID",
  "payout_batch_id": "8QK12345ABCDEFG",
  "payout_status": "PENDING",
  "audit_log": [
    { "event": "TASK_CREATED",  "detail": "Task created for 1.00 USD" },
    { "event": "FUNDED",        "detail": "PayPal capture ... verified for 1.00 USD (status COMPLETED)" },
    { "event": "SUBMISSION_RECEIVED", "detail": "421 characters submitted" },
    { "event": "REVIEW_COMPLETED",    "detail": "AI score 95/100, passed: ..." },
    { "event": "APPROVED",      "detail": "Approved by a human reviewer (AI score 95); payout requested" },
    { "event": "PAID",          "detail": "PayPal payout batch 8QK12345ABCDEFG (PENDING)" }
  ]
}
```

If PayPal rejects the payout you get `502` and the task **stays `APPROVED`**:

```json
{ "message": "INSUFFICIENT_FUNDS: ...", "debug_id": "6f1a2b3c4d5e6" }
```

Recover with the retry endpoint (the deterministic `sender_batch_id`
`escrowpilot_<task_id>` makes a double payment impossible):

```powershell
$paid = Invoke-RestMethod -Method Post -Uri "$api/tasks/$($task.id)/retry-payout"
$paid.status
```

If PayPal answers "batch already exists", the API returns `409` with a message
telling you to check the payout status instead of retrying.

---

## 7. Payout status

```powershell
Invoke-RestMethod -Uri "$api/tasks/$($task.id)/payout-status" | ConvertTo-Json
```

Expected (`200`):

```json
{
  "task_id": "63adeb2ccc084029a4e91320487028c5",
  "status": "PAID",
  "payout_batch_id": "8QK12345ABCDEFG",
  "batch_status": "SUCCESS",
  "item_status": "SUCCESS"
}
```

`batch_status` moves `PENDING` -> `SUCCESS` once PayPal finishes the batch.
Polling it never changes the task status.

---

## 8. Reject instead of paying (optional second task)

```powershell
$t2 = Invoke-RestMethod -Method Post -Uri "$api/tasks" -ContentType "application/json" -Body $body
Invoke-RestMethod -Method Post -Uri "$api/tasks/$($t2.id)/capture" | Out-Null   # after paying step 2 for this order
Invoke-RestMethod -Method Post -Uri "$api/tasks/$($t2.id)/submit" `
    -ContentType "application/json" -Body (@{ submission = "nope" } | ConvertTo-Json) | Out-Null
$rejected = Invoke-RestMethod -Method Post -Uri "$api/tasks/$($t2.id)/reject"
$rejected.status          # REJECTED
$rejected.audit_log[-1]   # detail: Rejected by a human reviewer. Refund not automated in demo.
```

---

## Expected error shapes

| Case | Status | Body |
| --- | --- | --- |
| Unknown task id | `404` | `{"detail": "task <id> not found"}` |
| Wrong state (e.g. approve before review) | `409` | `{"detail": "cannot approve a reviewed task while the task is FUNDED (expected REVIEWED)"}` |
| Capture mismatch / not completed | `409` | `{"detail": "capture not accepted: ..."}` |
| PayPal call failed | `502` | `{"message": "...", "debug_id": "..."}` |
| PayPal not configured | `502` | `{"message": "PayPal is not configured on this server; see backend logs.", "debug_id": null}` |
| Invalid body (float amount, short requirements, 20k+ submission) | `422` | FastAPI's field errors |

## Troubleshooting

- **`422` on create with `amount`** - `amount` must be a JSON **string** such as
  `"1.00"`, not a number, and must not exceed `MAX_TASK_AMOUNT` (default 100.00).
- **`502` ORDER_NOT_APPROVED at capture** - the buyer has not paid yet; the
  order is still awaiting approval at PayPal.
- **`409` at capture** - the payment completed but the amount or currency differs
  from the task. The `CAPTURE_REJECTED` audit entry says which.
- **`502` with `debug_id`** - quote that id to PayPal support; it is the only
  identifier the API ever exposes.
- **Tasks vanished** - the in-memory store does not survive a server restart.
