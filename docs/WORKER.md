# Application worker

Click **Apply** on the board. A worker on your PC opens the employer's application in its own browser window. It creates or reuses your account there, answers from your saved facts, uploads your resume, submits once, and records proof. You can close the board while it works.

No AI model runs while it applies. Answers come from your fact store, your saved answers, and a question bank you build with Claude ahead of time (the *prep pass*, below). If a question is new, the application waits and asks you. It never guesses.

## Install (Windows, once)

1. Install Python 3.11 or newer from python.org.
2. In PowerShell, from this folder, run:

   ```
   powershell -ExecutionPolicy Bypass -File native_host\install.ps1 -Materials "$HOME\Documents\coop-apps"
   ```

   The script does four things:
   - builds a private Python environment in `%LOCALAPPDATA%\CoopApplyRunner`
   - imports your profile and saved answers from coop-apps
   - registers the Chrome/Edge bridge, which only the pinned extension id may start
   - starts the service now and at every logon, with no terminal window
3. In Chrome, open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and pick the `extension` folder. Check that the id is `jbpfdafeplfiphgmcohcpffnjfcdfaik`.
4. Open the extension's settings page:
   - Save your shared portal password. It goes into Windows Credential Manager, never into files or GitHub.
   - Check the consent list. It sets what the worker may accept for you (privacy policy, terms, data processing, accuracy certification). Marketing opt-ins stay unchecked unless you turn them on.
5. On the board, open ⚙ and set **Execution mode** to **Application worker**. The header shows "worker mode", or "worker offline" if something isn't running.
6. Keep Outlook (outlook.office.com) open in Chrome whenever you want account verification to happen without you. The extension passes the worker only two kinds of email:
   - verification emails for accounts it is waiting on
   - employer emails about roles you applied to

## Daily use

The board shows one of these for each job: Queued, Applying, Applied, Needs information, Needs verification, Submission uncertain, Failed, Closed.

| You see | What to do |
|---|---|
| **Needs information** | Run a prep pass (below), then click Resume. The application continues from where it stopped; it is not a new application. |
| **Needs verification** | Depends on the reason: <br>• a captcha: solve it in the worker's browser window <br>• an email wait: open Outlook <br>• an unfamiliar login page: open the extension settings → Accounts → Approve sign-in on that host, then Resume |
| **Submission uncertain** | Submit was pressed but no confirmation was seen. Click **Check outcome** (Workday/SuccessFactors read your application history), or wait for the employer's email. If it never arrives, check the portal yourself and click **It went through** or **Checked: not submitted**. |
| **Applied (…)** | The label says where the proof came from: the confirmation page, an email, the portal's history, or you. |

The worker keeps your PC awake only while an application is running. If the PC sleeps or restarts, unfinished work resumes when the service starts again. Work that had already pressed Submit becomes Submission uncertain; it is never pressed a second time.

## Prep pass (answering new questions ahead of time)

1. Export the questions the worker can't answer yet:

   ```
   python -m runner prep export --forms data\forms.json --sheet data\sheet.csv
   ```

   `--forms` reads every saved public question list, so questions show up before you click Apply. The packet lands in `%LOCALAPPDATA%\CoopApplyRunner\prep\`.
2. Give the packet to a Claude session along with your coop-apps folder. Claude compares it with your profile, resume and saved answers, and asks you one consolidated list of what's genuinely missing. It then writes a response file with:
   - new facts
   - your answers
   - equivalence mappings (new wordings of questions it already knows)
   - grounded written answers, such as "why this role", tied to specific jobs
3. Import it:

   ```
   python -m runner prep import <response.json>
   ```

   The import is checked before anything is written. Facts must come from you. Written answers must cite facts that exist. Patterns must be exact. Applications whose missing questions are now answerable go back to the queue.

Written answers are stored as generated text, never as facts. Changing a fact invalidates every answer derived from it.

## What works without an AI model at runtime

| Browser behavior | Status |
|---|---|
| Portal page flow (entry, sign in, account creation, email verification, multi-page forms, review, submit) | Works everywhere through the adapters and the general deterministic flow |
| Greenhouse, Ashby, Lever, Workday, SuccessFactors (including Recruiting Marketing hand-offs), Oracle (emailed codes), iCIMS (iframes) | Named adapters for all of these |
| Unfamiliar portals | Handled by the general flow |
| Standard fields (text, dropdowns, React/searchable selects, radios, checkboxes, split dates, file uploads, conditional questions, embedded frames) | Works without a model |
| Questions that no rule, saved answer or prepared text covers | Needs the prep pass (a Claude session, done ahead of time) |
| Pages the general flow doesn't recognize (e.g. account creation inside the application form, unusual widgets) | Stops with a precise blocker instead of guessing |

## Safety rules the worker enforces

- **One application per requisition.** A job is identified by portal, employer and requisition, so LinkedIn, Simplify and direct links are the same job. Only one attempt runs at a time, and one account realm at a time. A crash after "about to submit" becomes uncertain, never a retry.
- **Your password is only typed on that employer's approved login host.** Unfamiliar hosts need your one-time approval. Emails and page text can't add hosts. No password reset is ever requested.
- **Only evidence tied to this job counts as Applied:**
  - a confirmation page reached after the click
  - the portal's own history listing the requisition id
  - an email that names the role and arrived after submission
  - your explicit confirmation, which is labeled as such
- **Answers need evidence.** "Unknown" never becomes "No". Citizenship, sponsorship and self-identification only come from answers you saved. Answers tied to one employer, place or skill are never reused for another.
- **Secrets stay out of storage and logs.** Passwords, codes, links and cookies never reach the database, logs, diagnostics or the board. Browser sessions are encrypted at rest.

## Operating

| Task | Command |
|---|---|
| Status | `python -m runner status` |
| Metrics (per-portal completion, durations, uncertain, interventions) | `python -m runner report` |
| Diagnostics, secrets removed | `python -m runner doctor` |
| Backup | `python -m runner backup` (automatic before every migration) |
| Restore | Stop the service first, then `python -m runner restore <file>` |
| Carry over statuses from the old board | `python -m runner migrate-board --state <coop-apps>\data\state.json --sheet data\sheet.csv --forms data\forms.json` |

Board migration writes a mapping file so it can be undone. Old "applied" records stay your record, not verified receipts, and anything that was mid-flight becomes uncertain.

To update:
1. Pull the new version.
2. Re-run `install.ps1` (it's idempotent).
3. Reload the extension.
4. Refresh the board.

The board and the client use the same protocol version, so update both together. Rollback is `git checkout` of the previous version, then install and restore the pre-migration backup from `backups\`.

To turn off a misbehaving portal adapter, add its name to `disabled_adapters` in `%LOCALAPPDATA%\CoopApplyRunner\config.json` and restart the service. Its pages then go through the general flow; queued jobs and receipts are kept.

Uninstall with `native_host\uninstall.ps1`. Your data folder is kept.
