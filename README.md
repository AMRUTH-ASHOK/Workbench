# Workbench

A local support desk for assigned cases: live ownership, official score history,
evidence-backed next actions, and editable customer/internal/feedback drafts.

## Run locally

Requires macOS, Python 3.12+, Node 22+, ArcaTerm with its CLI, and the existing
authenticated Isaac/dbexec environment in Arclets.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m workbench serve
```

Open **http://127.0.0.1:4318**. There is no cloud dashboard or external analytics.
The service binds to loopback and requires a same-origin token for mutations.

For automatic startup at login:

```sh
.venv/bin/python -m workbench install-service
```

The installer copies the application and built frontend into the private data
directory, creates its own virtual environment, and registers a user LaunchAgent.
This avoids macOS Desktop-folder privacy prompts at background startup. Re-run
the installer after changing the application. `Workbench.command` opens the desk.

## Daily operation

- Monday–Friday, 13:30, 16:15, 19:00 and 22:00 in Asia/Kolkata.
- The first successful refresh each day also reviews a 30-day closure window.
- Codex heartbeat automations trigger the existing coordinator. A local recovery
  check coalesces missed windows into one catch-up during the working shift.
- One reusable Arclet per active case, plus the collector and research workers
  when needed. Only documented Support Agent intake/read agents can run inside
  a case worker, under the same tool gate.
- A source/evaluation revision or due action can trigger investigation. Unchanged
  waiting cases reuse their evidence. Collection and case analysis have separate
  progress, timestamps, and failure states.
- Authentication problems appear under Operations. Complete the normal login in
  ArcaTerm or the linked source, then choose Resume / refresh.

The local recovery check is enabled only after the integration pilot by setting
the private `runtime_ready` flag. Schedule creation is performed through Codex's
automation tools, not by installing a separate OS cron schedule.

## Data and review rules

Scores **8, 9 and 10 are good**; higher is better. Overall SE is the mean of the
non-null five SE dimensions. Escalation Judgment can be N/A. AI SAT, Overall SE,
customer-view engineer experience and product experience remain distinct.

All communications and feedback stay **local drafts**. Copying does not mark a
draft as sent. The manual sent/submitted controls record the engineer's action;
they do not send anything. There are no send/submit endpoints. A case revision
supersedes its old drafts and generated proposed actions. A deterministic live
check of update time, owner and lifecycle gates each worker result.

Support Agent `/start` is submitted as a real user slash command in the owned
ArcaTerm pane. Its `disable-model-invocation` flag is respected: the coordinator
never tries to invoke it through the model Skill tool. Each invocation keeps a
journalled terminal receipt; intake completion is verified separately from dispatch.

Tool coverage distinguishes actual worker calls from official ingested usage.
Useful diagnostics are selected for the case, without tool-call quotas. Feedback
drafts are based on observed tool behavior and never count as submitted feedback.

The worker's PreToolUse gate denies communication writes, feedback submission,
customer-infrastructure jobs, inline shell/code execution and writes outside its
own output files. Approved metadata helpers may receive a single quoted JSON
heredoc; arbitrary shell commands remain unavailable. It permits documented investigation helpers, read tools, EPICA,
and normal Support Agent intake enrichment. Post-tool audits are exported locally.
Unsupported optional steps return gaps; workers never wait on permission menus.

## Storage and privacy

Default data directory:

```text
~/Library/Application Support/Workbench/
  workbench.sqlite3       # current state, append-only evaluations and actions
  evidence/              # exported source bundles, analyses, audits, playbook
  logs/                  # local service diagnostics
  app/                   # installed service, separate from source checkout
```

The directory is private (0700), with database and evidence files at 0600.
Credentials remain in the existing authentication systems. No credentials, case
records, evaluations, customer reports or screenshots belong in Git. The public
repository contains code, invented test fixtures and sanitized documentation.
History is retained locally until explicitly removed; the 30-day lookback controls
the daily closed-case review, not deletion of the engineer's longitudinal history.

Configuration: `WORKBENCH_DATA_DIR`, `WORKBENCH_PORT`, `WORKBENCH_OWNER_EMAIL`,
`WORKBENCH_OWNER_NAME`, `WORKBENCH_ARCATERM`. The owner defaults are configured for
this personal workbench; use a separate data directory when changing identities.

## Commands

```sh
.venv/bin/python -m workbench doctor
.venv/bin/python -m workbench refresh --trigger manual
.venv/bin/python -m workbench refresh --closed-review --collect-only
.venv/bin/python -m workbench analyze <assigned-case-number>
.venv/bin/python -m workbench harvest
.venv/bin/python -m workbench status
```

Run scheduled commands from the installed application directory to use the same
deployed code as the dashboard. A durable lock coalesces overlapping refreshes.
Arclet creation uses persisted UUIDv7 request IDs so retries reuse infrastructure.
Partial collections preserve completed results and never retire missing cases
unless the entire live inventory is verified. Worker exports are validated before
publication. Closed/reassigned case workers are stopped only after export, with
the exact affected tab checked by ArcaTerm.

To stop background startup:

```sh
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.amruth.workbench.plist
```

This stops the local service; pause the four Codex automations separately.

## Learning

Daily closed-case reviews produce descriptive candidates for dimensions below 8.
The engineer reviews each candidate before it enters the local playbook. Workers
receive approved lessons only. `AGENTS.md` stays stable. Action records are paired
only with later evaluations whose evidence cutoffs include the action and whose
source, phase, model and rubric are comparable. Changes show associations, not
proof that the action caused the score to change.

## Verification

```sh
.venv/bin/python -m pytest -q
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Browser tests use an isolated temporary database with invented records on port
4319. They never start collectors or touch live case records. Playwright uses an
installed Chrome binary with a fresh test profile. See [architecture](docs/architecture.md)
and [integration notes](docs/integration.md) for source contracts and limits.
