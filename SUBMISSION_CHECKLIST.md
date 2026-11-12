# Submission checklist

## Before you touch git

- [ ] A real end-to-end sandbox run is done **on the code you are about to push**: create a
      $1.00 task, pay as the sandbox buyer, `/return` captures it, freelancer submits, verdict
      shown, human approves, task `PAID`, payout status refreshed
- [ ] `cd backend; ..\.venv\Scripts\python.exe -m pytest tests -q` ends with `117 passed`
- [ ] `cd frontend; npm run build` succeeds
- [ ] Backend and frontend run from a clean `.env` copy of `.env.example` (no leftover local edits
      that the code depends on)

## Secret scan before pushing

Run these in the repo root. All three must print **nothing**.

```powershell
# 1. No env files or Firebase service-account keys are tracked
git ls-files | Select-String -Pattern "\.env$|serviceAccount|firebase.*adminsdk"
```

```powershell
# 2. No API keys, tokens or client secrets in tracked files
git grep -n -I -E "AIza[0-9A-Za-z_-]{20,}|sk-ant-|client_secret"
```

```powershell
# 3. No PayPal client secret or bearer token literals
git grep -n -I -E "PAYPAL_CLIENT_SECRET=.+|Bearer [A-Za-z0-9._-]{20,}|access_token\s*=\s*['\"]"
```

What should be true instead: `.env` is listed in `.gitignore`, `.env.example` has empty values,
and `secrets/` is ignored. If any command prints a real value, **do not push** — rotate the
credential first, then fix the file history.

Also confirm the frontend's local env is untracked:

```powershell
git check-ignore -v frontend/.env backend/.uvicorn.log backend/.dev_task_id
```

## First push (PowerShell)

```powershell
cd C:\paypal

git init
git add .
git status --short          # sanity check: no .env, no node_modules, no .venv, no dist
git commit -m "EscrowPilot: AI-reviewed micro-task escrow on PayPal"
git branch -M main
```

Create an **empty** public repo on GitHub (no README, no .gitignore), then:

```powershell
git remote add origin https://github.com/<your-user>/<repo>.git
git push -u origin main
```

With the GitHub CLI installed, the same thing in one step:

```powershell
gh repo create escrowpilot --public --source . --remote origin --push
```

## Repository

- [ ] Repo is **public** and the URL opens in a private/incognito window
- [ ] Default branch is `main` and the working tree is clean
- [ ] `LICENSE` is present (MIT) and the README links to it
- [ ] `README.md` renders on GitHub: both Mermaid diagrams draw, no broken relative links
- [ ] `DEMO.md`, `DEVPOST.md`, `SUBMISSION_CHECKLIST.md`, `backend/API_WALKTHROUGH.md` are visible
- [ ] Placeholders are filled or deliberately left: `[ADD VIDEO LINK]`, `[ADD DEVPOST LINK]`,
      `[ADD SCREENSHOT: …]`

## Video

- [ ] Under 3:00 (hard limit), audio audible, no dead air at the start
- [ ] Slides total under 45 seconds
- [ ] Shows: the problem, create → pay → capture, freelancer submit, AI verdict, the Approve
      confirm dialog, `PAID`, payout status
- [ ] Shows the guardrails recap before the end
- [ ] Uploaded to YouTube or Vimeo, **public or unlisted**, and the link plays in a logged-out
      browser
- [ ] The same link is in the Devpost form and in `README.md`

## Devpost form

- [ ] Title, tagline, and the sections from `DEVPOST.md` pasted in (Inspiration → What's next)
- [ ] "Built with" list filled from `DEVPOST.md`
- [ ] Repo link and video link added
- [ ] 3–5 screenshots uploaded (Client tab, review verdict, confirm dialog, PAID task, audit log)
- [ ] Thumbnail image chosen
- [ ] Submitted **before** the deadline, with the confirmation page saved as a screenshot

## Final 10 minutes

- [ ] Open the repo link in incognito; open the video link in incognito
- [ ] Confirm the Devpost preview shows the video embedded, not just a URL
- [ ] Leave the backend and frontend running locally in case a judge asks for a live walkthrough —
      and remember the in-memory store loses tasks on restart
