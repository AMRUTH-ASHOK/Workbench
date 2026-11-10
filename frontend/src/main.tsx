import React, { useState, useEffect, useCallback } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Copy,
  ExternalLink,
  FileText,
  Inbox,
  LayoutPanelLeft,
  Loader2,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  TriangleAlert,
  X,
} from "lucide-react";
import { api } from "./api";
import type {
  Case,
  Draft,
  Evaluation,
  Evidence,
  Lesson,
  Overview,
  Outcome,
  WritingStyle,
} from "./types";
import "./style.css";

const labels: Record<string, string> = {
  overall_se: "Overall SE",
  ai_sat: "AI SAT",
  responsiveness: "Responsiveness",
  technical_depth: "Technical correctness",
  communication: "Communication",
  escalation_judgment: "Escalation judgment",
  resolution_effectiveness: "Resolution effectiveness",
};
const keys = Object.keys(labels);
const date = (value: string | null | undefined, full = false) =>
  value
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        day: "numeric",
        month: "short",
        ...(full ? { hour: "2-digit", minute: "2-digit" } : {}),
      }).format(new Date(value))
    : "Not available";
const score = (value: number | null | undefined) =>
  value == null
    ? "—"
    : Number.isInteger(value)
      ? String(value)
      : value.toFixed(2).replace(/0$/, "");
const band = (value: number | null | undefined) =>
  value == null
    ? "missing"
    : value >= 8
      ? "good"
      : value > 7
        ? "watch"
        : "attention";
const tidy = (value: string) => value.replaceAll("_", " ");
const audienceLabel = (value: string) =>
  value.charAt(0).toUpperCase() + value.slice(1);
function safeUrl(value: string | null | undefined) {
  try {
    const u = new URL(value || "");
    return ["https:", "http:"].includes(u.protocol) ? u.href : undefined;
  } catch {
    return undefined;
  }
}

function EvidenceList({ items }: { items: Evidence[] }) {
  return (
    <div className="evidence-list">
      {items.map((e, i) => (
        <div className="evidence" key={i}>
          <FileText size={15} />
          <div>
            {safeUrl(e.url) ? (
              <a href={safeUrl(e.url)} target="_blank" rel="noreferrer">
                {e.title || e.source || "Source"} <ArrowUpRight size={12} />
              </a>
            ) : (
              <strong>{e.title || e.source || "Source"}</strong>
            )}
            {e.detail && <p>{e.detail}</p>}
          </div>
        </div>
      ))}
    </div>
  );
}

function History({
  evaluations,
  outcomes = [],
}: {
  evaluations: Evaluation[];
  outcomes?: Outcome[];
}) {
  const [metric, setMetric] = useState("ai_sat");
  const points = evaluations
    .filter((e) => e.scores[metric] != null)
    .sort((a, b) => a.produced_at.localeCompare(b.produced_at));
  const [selected, setSelected] = useState<number | null>(null);
  const current = points[selected ?? points.length - 1];
  const width = 650,
    height = 175,
    pad = 26;
  const xs = points.map((e) => new Date(e.produced_at).getTime()),
    min = xs[0] || 0,
    max = xs[xs.length - 1] || 1;
  const px = (i: number) =>
    max === min
      ? width / 2
      : pad + ((xs[i] - min) / (max - min)) * (width - pad * 2);
  const py = (s: number) => height - pad - (s / 10) * (height - pad * 2);
  const path = points
    .map((p, i) => `${i ? "L" : "M"}${px(i)},${py(p.scores[metric]!)}`)
    .join(" ");
  return (
    <section className="history">
      <div className="section-heading">
        <h3>Official score history</h3>
        <select
          aria-label="History metric"
          value={metric}
          onChange={(e) => {
            setMetric(e.target.value);
            setSelected(null);
          }}
        >
          {keys.map((k) => (
            <option value={k} key={k}>
              {labels[k]}
            </option>
          ))}
        </select>
      </div>
      {points.length ? (
        <>
          <svg
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label={`${labels[metric]} score history, ${points.length} evaluations`}
          >
            <rect
              x={pad}
              y={py(10)}
              width={width - pad * 2}
              height={py(8) - py(10)}
              fill="#edf7f1"
            />
            {[0, 4, 8, 10].map((n) => (
              <g key={n}>
                <line
                  x1={pad}
                  x2={width - pad}
                  y1={py(n)}
                  y2={py(n)}
                  stroke="#dce5e9"
                  strokeDasharray={n === 8 ? "4 4" : undefined}
                />
                <text x={3} y={py(n) + 4}>
                  {n}
                </text>
              </g>
            ))}
            <path d={path} fill="none" stroke="#216b96" strokeWidth={2} />
            {points.map((e, i) => (
              <g
                key={`${e.source}-${e.result_id}`}
                tabIndex={0}
                role="button"
                aria-label={`${date(e.produced_at, true)}: ${score(e.scores[metric])}`}
                onClick={() => setSelected(i)}
                onKeyDown={(ev) => {
                  if (ev.key === "Enter") setSelected(i);
                }}
              >
                <circle
                  cx={px(i)}
                  cy={py(e.scores[metric]!)}
                  r={selected === i ? 6 : 4}
                  fill={
                    band(e.scores[metric]) === "good" ? "#247259" : "#bd7049"
                  }
                  stroke="white"
                  strokeWidth={2}
                />
                <title>
                  {date(e.produced_at, true)}: {score(e.scores[metric])}
                </title>
              </g>
            ))}
          </svg>
          <div className="chart-axis">
            <span>{date(points[0].produced_at, true)}</span>
            <span>{date(points[points.length - 1].produced_at, true)}</span>
          </div>
          {current && (
            <div className="chart-detail">
              <strong>{score(current.scores[metric])}</strong>
              <span>
                {date(current.produced_at, true)} · {current.phase} evaluation
                <br />
                <small>
                  Evidence through {date(current.covered_at, true)}
                  {current.model ? " · " + current.model : ""}
                </small>
              </span>
            </div>
          )}
          <p className="caption">
            Green begins at 8. Revisions can reflect new evidence or evaluator
            changes; a score change alone does not prove an action caused it.
          </p>
          {outcomes.length > 0 && (
            <section className="outcomes">
              <h3>Actions and later evaluations</h3>
              {outcomes.map((o) => (
                <article key={o.action_id}>
                  <strong>{o.title}</strong>
                  <p>Recorded complete {date(o.completed_at, true)}</p>
                  {o.after ? (
                    <>
                      <p>
                        Later comparable evaluation:{" "}
                        {date(o.after.produced_at, true)}
                      </p>
                      <div>
                        {Object.entries(o.deltas).map(([k, v]) => (
                          <span className="pill" key={k}>
                            {labels[k] || tidy(k)} {v > 0 ? "+" : ""}
                            {v}
                          </span>
                        ))}
                      </div>
                    </>
                  ) : (
                    <p className="muted">
                      Waiting for a comparable evaluation whose evidence
                      includes this action.
                    </p>
                  )}
                </article>
              ))}
            </section>
          )}
        </>
      ) : (
        <Empty
          icon={<Activity />}
          title="No history for this metric"
          text="Official revisions will appear after collection. Missing values remain unscored."
        />
      )}
    </section>
  );
}

function Empty({
  icon,
  title,
  text,
}: {
  icon: React.ReactNode;
  title: string;
  text: string;
}) {
  return (
    <div className="empty">
      {icon}
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}

function DraftEditor({
  draft,
  onChange,
  notify,
}: {
  draft: Draft;
  onChange: () => Promise<void>;
  notify: (s: string) => void;
}) {
  const [body, setBody] = useState(draft.body),
    [busy, setBusy] = useState(false);
  useEffect(() => setBody(draft.body), [draft.id, draft.body]);
  const stale = draft.state === "superseded";
  async function update(state?: string, learnStyle = false) {
    setBusy(true);
    try {
      await api("/drafts/" + draft.id, "PATCH", {
        body,
        learn_style: learnStyle,
        ...(state ? { state } : {}),
      });
      await onChange();
      notify(
        learnStyle
          ? "Draft saved. Your edit was added to your private writing profile."
          : state === "copied"
            ? "Copied. This does not mark it as sent."
            : state === "manually_sent"
              ? "Recorded as manually sent."
              : state === "submitted_manually"
                ? "Recorded as manually submitted."
                : "Draft saved.",
      );
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className={`draft-editor ${stale ? "superseded" : ""}`}>
      <div className="section-heading">
        <h3>{draft.title}</h3>
        <span className="pill">{tidy(draft.state)}</span>
      </div>
      {draft.variant && draft.variant !== "standard" && (
        <p className="draft-variant">
          {draft.variant === "high_level" ? "High-level" : tidy(draft.variant)}{" "}
          · {draft.scenario === "follow_up" ? "Follow-up" : "Update"}
        </p>
      )}
      {stale && (
        <p className="notice">
          This draft uses an older case version. Use a current draft before
          taking action.
        </p>
      )}
      {draft.use_when && (
        <p className="notice">
          <strong>Use when:</strong> {draft.use_when}
        </p>
      )}
      {draft.review_notes && (
        <details className="draft-review-notes">
          <summary>Review notes — excluded from message</summary>
          <p className="preserve">{draft.review_notes}</p>
        </details>
      )}
      <textarea
        aria-label={draft.title}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        readOnly={stale}
        spellCheck
      />
      <div className="draft-toolbar">
        <button disabled={busy || stale} onClick={() => update()}>
          Save draft
        </button>
        {draft.kind !== "feedback" && (
          <button
            disabled={
              busy || stale || body === draft.body || body.trim().length < 40
            }
            onClick={() => update(undefined, true)}
          >
            Save &amp; learn from my edit
          </button>
        )}
        <button disabled={busy || stale} onClick={() => update("reviewed")}>
          <Check size={14} />
          Mark reviewed
        </button>
        <button
          className="primary"
          disabled={busy || stale}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(body);
              await update("copied");
            } catch {
              notify("Clipboard unavailable. Select and copy the draft text.");
            }
          }}
        >
          <Copy size={14} />
          Copy
        </button>
      </div>
      {!stale && (
        <button
          className="text-button manual-mark"
          disabled={busy}
          onClick={() =>
            update(
              draft.kind === "feedback"
                ? "submitted_manually"
                : "manually_sent",
            )
          }
        >
          {draft.kind === "feedback"
            ? "I submitted this feedback manually"
            : "I sent this manually"}
        </button>
      )}
      {draft.evidence.length > 0 && (
        <details>
          <summary>Supporting evidence</summary>
          <EvidenceList items={draft.evidence} />
        </details>
      )}
    </article>
  );
}

function CaseWorkspace({
  item,
  onReload,
  onBack,
  notify,
}: {
  item: Case;
  onReload: () => Promise<void>;
  onBack: () => void;
  notify: (s: string) => void;
}) {
  const [tab, setTab] = useState("brief"),
    [metric, setMetric] = useState<string | null>(null),
    [draftKind, setDraftKind] = useState("customer"),
    [draftVariant, setDraftVariant] = useState("all"),
    [draftScenario, setDraftScenario] = useState("update"),
    [showOld, setShowOld] = useState(false),
    [newAction, setNewAction] = useState("");
  useEffect(() => {
    setMetric(null);
    setTab("brief");
    setDraftVariant("all");
    setDraftScenario("update");
  }, [item.id]);
  const evaluation = item.evaluation,
    analysis = item.analysis,
    actions = item.actions.filter((a) => a.state === "proposed"),
    drafts = (item.drafts || []).filter(
      (d) =>
        d.kind === draftKind &&
        (showOld || d.state !== "superseded") &&
        (draftKind === "feedback" ||
          ((draftVariant === "all" ||
            (d.variant || "standard") === draftVariant) &&
            (d.scenario || "update") === draftScenario)),
    );
  const next = actions[0];
  return (
    <main className="case-workspace">
      <button className="mobile-back text-button" onClick={onBack}>
        <ArrowLeft size={16} />
        All cases
      </button>
      <header className="case-header">
        <div className="case-kicker">
          <span>Case {item.number}</span>
          <span
            className={`priority ${item.priority === "High" ? "high" : ""}`}
          >
            {item.priority || "Priority unavailable"}
          </span>
        </div>
        <h1>{item.account || "Account unavailable"}</h1>
        <p className="case-subject">
          {item.subject ||
            analysis?.summary?.split("\n")[0]?.slice(0, 180) ||
            "Review the case evidence and next action below."}
        </p>
        <div className="case-meta">
          <span className="status-dot" />
          {item.status}
          <a
            href={`https://databricks.my.salesforce.com/${item.id}`}
            target="_blank"
            rel="noreferrer"
          >
            Open case <ExternalLink size={12} />
          </a>
        </div>
      </header>
      <div className="next-action">
        <div className="next-icon">
          <ChevronRight size={21} />
        </div>
        <div>
          <span className="small-label">Next action</span>
          <strong>{next?.title || "Case analysis pending"}</strong>
          <p>
            {next
              ? `${next.owner}${next.due_at ? " · Due " + date(next.due_at, true) : " · No deadline set"}`
              : "The case worker will identify an evidence-backed next step."}
          </p>
        </div>
        {next && (
          <button
            title="Record action completed"
            aria-label="Record action completed"
            onClick={async () => {
              try {
                await api("/actions/" + next.id, "PATCH", { state: "done" });
                await onReload();
                notify(
                  "Action recorded. Later official scores will remain separate evidence.",
                );
              } catch (e) {
                notify((e as Error).message);
              }
            }}
          >
            <Check size={16} />
          </button>
        )}
      </div>
      <section className="score-section">
        <div className="section-heading">
          <h2>Performance</h2>
          <span className="legend">
            <i />
            8–10 good
          </span>
        </div>
        <div className="scores">
          {keys.map((k) => (
            <button
              className={`score-cell ${band(evaluation?.scores[k])} ${metric === k ? "selected" : ""}`}
              key={k}
              onClick={() => {
                setMetric(metric === k ? null : k);
                setTab("brief");
              }}
            >
              <span>{labels[k]}</span>
              <strong>
                {k === "escalation_judgment" &&
                evaluation &&
                evaluation.scores[k] === null
                  ? "N/A"
                  : score(evaluation?.scores[k])}
              </strong>
              <span className="score-track">
                <i style={{ width: `${(evaluation?.scores[k] ?? 0) * 10}%` }} />
              </span>
            </button>
          ))}
        </div>
        <div className="freshness">
          <Clock3 size={13} />
          <span>
            {evaluation
              ? `Scored ${date(evaluation.produced_at, true)} · Covers updates through ${date(evaluation.covered_at, true)}`
              : "No official evaluation available yet"}
          </span>
          {evaluation && evaluation.score_status !== "latest" && (
            <span className="pill warning">
              {tidy(evaluation.score_status)}
            </span>
          )}
        </div>
        {metric && (
          <div className="score-reason">
            <strong>
              {labels[metric]} · {score(evaluation?.scores[metric])}
            </strong>
            <p>
              {evaluation?.reasoning[metric] ||
                (metric === "escalation_judgment" &&
                evaluation?.scores[metric] == null
                  ? "No scored escalation decision. N/A is excluded from Overall SE."
                  : "No explanation supplied by this evaluation.")}
            </p>
            {metric === "overall_se" && (
              <p>
                Mean of the available five SE dimensions. AI SAT is a separate
                assessment.
              </p>
            )}
          </div>
        )}
      </section>
      <nav className="case-tabs" aria-label="Case views">
        {[
          ["brief", "Case brief"],
          [
            "drafts",
            `Drafts${item.draft_count ? " (" + item.draft_count + ")" : ""}`,
          ],
          ["history", "Score history"],
          ["activity", "Actions & coverage"],
        ].map(([k, l]) => (
          <button
            key={k}
            className={tab === k ? "active" : ""}
            onClick={() => setTab(k)}
          >
            {l}
          </button>
        ))}
      </nav>
      <div className="tab-body">
        {tab === "brief" && (
          <>
            {item.analysis_stale && analysis && (
              <p className="notice">
                The case has changed since this analysis. Updated guidance is
                pending.
              </p>
            )}
            {analysis ? (
              <>
                <section className="brief-summary">
                  <h3>Where things stand</h3>
                  <p className="preserve">{analysis.summary}</p>
                </section>
                {analysis.concerns.length > 0 && (
                  <section>
                    <h3>What needs attention</h3>
                    {analysis.concerns.map((c, i) => (
                      <div className="concern" key={i}>
                        <TriangleAlert size={16} />
                        <div>
                          <strong>{c.title || c.metric}</strong>
                          <p>{c.reason}</p>
                        </div>
                      </div>
                    ))}
                  </section>
                )}
                <section>
                  <h3>Evidence</h3>
                  <EvidenceList items={analysis.evidence} />
                </section>
                {analysis.gaps.length > 0 && (
                  <section className="gaps">
                    <h3>Still to verify</h3>
                    <ul>
                      {analysis.gaps.map((g, i) => (
                        <li key={i}>{g}</li>
                      ))}
                    </ul>
                  </section>
                )}
              </>
            ) : (
              <Empty
                icon={<FileText />}
                title="The case is in your queue"
                text="Its investigation and communication drafts will appear when the worker finishes. Official scores can arrive independently."
              />
            )}
            <div className="source-footer">
              Case collected {date(item.observed_at, true)} ·{" "}
              {tidy(item.source)}
            </div>
          </>
        )}
        {tab === "drafts" && (
          <>
            <div className="draft-switch">
              <div className="segmented">
                {["customer", "engineering", "internal", "feedback"].map(
                  (k) => (
                    <button
                      key={k}
                      className={draftKind === k ? "active" : ""}
                      onClick={() => setDraftKind(k)}
                    >
                      {audienceLabel(k)}
                    </button>
                  ),
                )}
              </div>
              <label>
                <input
                  type="checkbox"
                  checked={showOld}
                  onChange={(e) => setShowOld(e.target.checked)}
                />
                Show superseded
              </label>
            </div>
            {draftKind !== "feedback" && (
              <div className="draft-filters">
                <label>
                  Message purpose
                  <select
                    aria-label="Message purpose"
                    value={draftScenario}
                    onChange={(e) => setDraftScenario(e.target.value)}
                  >
                    <option value="update">Update</option>
                    <option value="follow_up">Follow-up</option>
                  </select>
                </label>
                <label>
                  Message length
                  <select
                    aria-label="Message length"
                    value={draftVariant}
                    onChange={(e) => setDraftVariant(e.target.value)}
                  >
                    <option value="all">All lengths</option>
                    <option value="detailed">Detailed</option>
                    <option value="concise">Concise</option>
                    <option value="high_level">High-level</option>
                    <option value="standard">Original</option>
                  </select>
                </label>
              </div>
            )}
            <p className="review-note">
              <ShieldCheck size={15} />
              Review and copy drafts here. Sending and feedback submission
              happen manually in their source apps. EPIC-A is reference
              material; these options are written from the case evidence.
            </p>
            {drafts.length ? (
              drafts.map((d) => (
                <DraftEditor
                  key={d.id}
                  draft={d}
                  onChange={onReload}
                  notify={notify}
                />
              ))
            ) : (
              <Empty
                icon={<FileText />}
                title="No current draft in this category"
                text="A draft is prepared when the case evidence supports a useful communication or feedback item."
              />
            )}
          </>
        )}
        {tab === "history" && (
          <>
            <History
              evaluations={item.history || []}
              outcomes={item.outcomes || []}
            />
          </>
        )}
        {tab === "activity" && (
          <>
            <section>
              <h3>Action log</h3>
              {item.actions.length ? (
                item.actions.map((a) => (
                  <div className="action-row" key={a.id}>
                    <button
                      className={`check-action ${a.state === "done" ? "done" : ""}`}
                      aria-label={
                        a.state === "done" ? "Reopen action" : "Complete action"
                      }
                      onClick={async () => {
                        try {
                          await api("/actions/" + a.id, "PATCH", {
                            state: a.state === "done" ? "proposed" : "done",
                          });
                          await onReload();
                        } catch (e) {
                          notify((e as Error).message);
                        }
                      }}
                    >
                      {a.state === "done" ? <Check size={13} /> : null}
                    </button>
                    <div>
                      <strong>{a.title}</strong>
                      <p>
                        {a.owner} · {a.state}
                        {a.due_at ? " · Due " + date(a.due_at, true) : ""}
                      </p>
                      {a.note && <small>{a.note}</small>}
                    </div>
                  </div>
                ))
              ) : (
                <p className="muted">No actions recorded yet.</p>
              )}
              <form
                className="inline-form"
                onSubmit={async (e) => {
                  e.preventDefault();
                  try {
                    await api("/cases/" + item.id + "/actions", "POST", {
                      title: newAction,
                      owner: "Me",
                    });
                    setNewAction("");
                    await onReload();
                  } catch (error) {
                    notify((error as Error).message);
                  }
                }}
              >
                <input
                  aria-label="New action"
                  placeholder="Record a next action…"
                  value={newAction}
                  onChange={(e) => setNewAction(e.target.value)}
                  required
                />
                <button>Add action</button>
              </form>
            </section>
            <section>
              <h3>Support Agent & tool coverage</h3>
              {analysis?.coverage.map((c, i) => (
                <div className="coverage-row" key={i}>
                  <CheckCircle2 size={16} />
                  <div>
                    <strong>{c.name || c.tool}</strong>
                    <p>
                      {c.state} · {c.detail}
                    </p>
                  </div>
                </div>
              ))}
              <p className="caption">
                Local worker evidence and official usage records are separate.
                Upstream telemetry can take time to appear. Feedback drafts do
                not count as submitted.
              </p>
              {item.usage?.length ? (
                <details>
                  <summary>{item.usage.length} official usage records</summary>
                  <pre>{JSON.stringify(item.usage, null, 2)}</pre>
                </details>
              ) : (
                <p className="muted">
                  No official usage records collected for this case.
                </p>
              )}
            </section>
            {item.events?.length ? (
              <section>
                <h3>Recent activity</h3>
                {item.events.map((e) => (
                  <div className="timeline-row" key={e.id}>
                    <span />
                    <div>
                      <strong>{tidy(e.kind)}</strong>
                      <small>{date(e.created_at, true)}</small>
                    </div>
                  </div>
                ))}
              </section>
            ) : null}
          </>
        )}
      </div>
    </main>
  );
}

function Operations({
  overview,
  onRefresh,
}: {
  overview: Overview | null;
  onRefresh: () => void;
}) {
  return (
    <main className="wide-workspace">
      <div className="page-intro">
        <Server size={26} />
        <div>
          <h1>Operations</h1>
          <p>Connections, worker progress, and refresh history.</p>
        </div>
        <button onClick={onRefresh}>
          <RefreshCw size={14} />
          Resume / refresh
        </button>
      </div>
      <section className="schedule-panel">
        <h3>Your working rhythm</h3>
        <div className="schedule-times">
          {overview?.schedule.map((s, i) => (
            <div key={s}>
              <Clock3 size={18} />
              <strong>{s}</strong>
              <span>
                {i === 0 ? "Open + 30-day closed review" : "Open-case refresh"}
              </span>
            </div>
          ))}
        </div>
        <p>
          Monday–Friday · IST · One catch-up refresh after this Mac resumes
          during your shift.
        </p>
      </section>
      <section>
        <h2>Source connections</h2>
        <div className="connection-list">
          {overview?.sources.length ? (
            overview.sources.map((s) => (
              <article key={s.key}>
                <span className={`connection-dot ${s.state}`} />
                <div>
                  <h3>{tidy(s.key)}</h3>
                  <p>{s.message}</p>
                  <small>Checked {date(s.checked_at, true)}</small>
                </div>
                <span
                  className={`pill ${s.state === "needs_auth" ? "warning" : ""}`}
                >
                  {tidy(s.state)}
                </span>
                {safeUrl(s.login_url) && (
                  <a
                    className="button"
                    href={safeUrl(s.login_url)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open login <ExternalLink size={13} />
                  </a>
                )}
              </article>
            ))
          ) : (
            <p className="muted">Run a refresh to check your sources.</p>
          )}
        </div>
      </section>
      <section>
        <h2>Arclet workers</h2>
        {overview?.workers.length ? (
          <div className="worker-grid">
            {overview.workers.map((w) => (
              <article key={w.key}>
                <Server size={18} />
                <div>
                  <strong>{w.name || w.key}</strong>
                  <p>
                    {w.role} · {tidy(w.state)}
                  </p>
                  {w.error && <small>{w.error}</small>}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="muted">
            Workers are created when a case needs investigation.
          </p>
        )}
      </section>
      <section>
        <h2>Refresh history</h2>
        <div className="run-list">
          {overview?.runs.map((r) => (
            <article key={r.id}>
              <span className={`pill ${r.state === "failed" ? "warning" : ""}`}>
                {r.state}
              </span>
              <div>
                <strong>{date(r.started_at, true)}</strong>
                <p>
                  {r.stage} · {r.trigger}
                </p>
                {r.detail && <small>{r.detail}</small>}
              </div>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}

function WritingPreferencesPanel({ notify }: { notify: (s: string) => void }) {
  const [profile, setProfile] = useState<WritingStyle | null>(null),
    [preferences, setPreferences] = useState<Record<string, string>>({}),
    [enabled, setEnabled] = useState(true),
    [audience, setAudience] = useState("customer"),
    [body, setBody] = useState(""),
    [sourceLabel, setSourceLabel] = useState("My sent message"),
    [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const p = await api<WritingStyle>("/writing-style");
    setProfile(p);
    setEnabled(p.learning_enabled);
    setPreferences(
      Object.fromEntries(
        Object.entries(p.audiences).map(([k, v]) => [k, v.preferences]),
      ),
    );
  }, []);
  useEffect(() => {
    load().catch((e) => notify(e.message));
  }, [load, notify]);
  async function change(action: () => Promise<unknown>, message: string) {
    setBusy(true);
    try {
      await action();
      await load();
      notify(message);
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (!profile)
    return (
      <section>
        <h2>Your writing style</h2>
        <p>Loading your private writing profile…</p>
      </section>
    );
  return (
    <section className="writing-style">
      <h2>Your writing style</h2>
      <p>
        Messages evolve from your own sent history, confirmed examples and
        edits. Customer, engineering and internal preferences stay separate.
        EPIC-A is reference material, not required wording.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          change(
            () =>
              api("/writing-style", "PATCH", {
                learning_enabled: enabled,
                preferences,
              }),
            "Writing preferences saved.",
          );
        }}
      >
        <label className="writing-toggle">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Use adaptive style learning
        </label>
        <p className="caption">
          Pausing stops new learning and stops using learned observations in
          future investigations. Your explicit preferences still apply. Existing
          drafts are unchanged.
        </p>
        <div className="writing-audiences">
          {Object.entries(profile.audiences).map(([key, p]) => (
            <article key={key}>
              <h3>{audienceLabel(key)}</h3>
              <p className="caption">
                {p.sample_count} confirmed examples ·{" "}
                {p.confidence === "none"
                  ? "No confirmed profile yet"
                  : `${tidy(p.confidence)} profile`}
              </p>
              <label>
                Preferences for {key}
                <textarea
                  aria-label={`Preferences for ${key}`}
                  value={preferences[key] || ""}
                  maxLength={5000}
                  onChange={(e) =>
                    setPreferences({ ...preferences, [key]: e.target.value })
                  }
                  placeholder="Tone, structure, openings, or phrases you prefer"
                />
              </label>
              {p.observations.length > 0 && (
                <ul>
                  {p.observations.map((o) => (
                    <li key={o}>{o}</li>
                  ))}
                </ul>
              )}
              {p.historical_observations.length > 0 && (
                <details>
                  <summary>
                    Tentative patterns from sent history (
                    {p.historical_source_count} source groups)
                  </summary>
                  <p className="caption">
                    Attributed privacy summaries, not exact quotations. These
                    are guidance with limited confidence.
                  </p>
                  <ul>
                    {p.historical_observations.map((o) => (
                      <li key={o}>{o}</li>
                    ))}
                  </ul>
                </details>
              )}
            </article>
          ))}
        </div>
        <button className="primary" disabled={busy}>
          Save writing preferences
        </button>
      </form>
      <details className="writing-examples">
        <summary>Teach with a message I wrote</summary>
        <p className="caption">
          Add your own sent text, or use “Save &amp; learn from my edit” on a
          draft. Unchanged generated drafts, copied or reviewed text, and other
          authors are not examples of your voice. Raw examples stay in this
          private local store; workers receive structural observations.
        </p>
        <form
          className="lesson-form"
          onSubmit={(e) => {
            e.preventDefault();
            change(async () => {
              await api("/writing-style/examples", "POST", {
                audience,
                body,
                source_label: sourceLabel,
              });
              setBody("");
            }, "Your example was added to your private writing profile.");
          }}
        >
          <label>
            Example audience
            <select
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
            >
              {["customer", "engineering", "internal"].map((a) => (
                <option key={a} value={a}>
                  {audienceLabel(a)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Example source label
            <input
              value={sourceLabel}
              maxLength={300}
              required
              onChange={(e) => setSourceLabel(e.target.value)}
            />
          </label>
          <label>
            A message I wrote
            <textarea
              value={body}
              minLength={40}
              maxLength={30000}
              required
              onChange={(e) => setBody(e.target.value)}
            />
          </label>
          <button disabled={busy || !profile.learning_enabled}>
            Learn from my message
          </button>
        </form>
      </details>
      {profile.examples.length > 0 && (
        <details className="writing-examples">
          <summary>
            Manage private examples and history ({profile.examples.length})
          </summary>
          {profile.examples.map((e) => (
            <article key={e.id}>
              <strong>
                {audienceLabel(e.audience)} · {e.source_label}
              </strong>
              <p>{e.preview}</p>
              <button
                disabled={busy}
                onClick={() =>
                  change(
                    () =>
                      api(
                        "/writing-style/examples/" + encodeURIComponent(e.id),
                        "DELETE",
                      ),
                    "Writing example forgotten.",
                  )
                }
              >
                Forget example
              </button>
            </article>
          ))}
        </details>
      )}
    </section>
  );
}

function Learning({
  cases,
  notify,
}: {
  cases: Case[];
  notify: (s: string) => void;
}) {
  const [lessons, setLessons] = useState<Lesson[]>([]),
    [title, setTitle] = useState(""),
    [lesson, setLesson] = useState(""),
    [limits, setLimits] = useState(""),
    [support, setSupport] = useState("");
  const load = useCallback(
    async () => setLessons(await api<Lesson[]>("/lessons")),
    [],
  );
  useEffect(() => {
    load().catch((e) => notify(e.message));
  }, [load, notify]);
  const scored = cases.filter((c) => c.evaluation?.scores.overall_se != null),
    good = scored.filter((c) => c.evaluation!.scores.overall_se! >= 8);
  return (
    <main className="wide-workspace">
      <div className="page-intro">
        <BookOpen size={26} />
        <div>
          <h1>Learning desk</h1>
          <p>
            Review closed cases, connect actions to later evaluations, and keep
            useful lessons.
          </p>
        </div>
      </div>
      <WritingPreferencesPanel notify={notify} />
      <div className="learning-summary">
        <div>
          <strong>{cases.length}</strong>
          <span>Cases in the closed review</span>
        </div>
        <div>
          <strong>
            {good.length}/{scored.length}
          </strong>
          <span>Scored cases with Overall SE ≥8</span>
        </div>
        <p>
          30-day closure lookback, refreshed once each working day. Before/after
          score changes show associations; product issues, customer activity,
          and evaluator revisions can affect them.
        </p>
      </div>
      <section>
        <h2>Playbook candidates</h2>
        {lessons.length ? (
          lessons.map((l) => (
            <article className="lesson" key={l.id}>
              <div className="section-heading">
                <h3>{l.title}</h3>
                <span className="pill">{l.state}</span>
              </div>
              <p className="preserve">{l.lesson}</p>
              <small>
                Supporting cases:{" "}
                {l.supporting_cases.join(", ") || "None attached"}
              </small>
              {l.limitations && (
                <p className="caption">Limitations: {l.limitations}</p>
              )}
              <div className="draft-toolbar">
                <button
                  disabled={l.state === "approved"}
                  onClick={async () => {
                    await api("/lessons/" + l.id, "PATCH", {
                      state: "approved",
                    });
                    await load();
                    notify("Lesson approved in the local playbook.");
                  }}
                >
                  <Check size={14} />
                  Approve lesson
                </button>
                <button
                  disabled={l.state === "rejected"}
                  onClick={async () => {
                    await api("/lessons/" + l.id, "PATCH", {
                      state: "rejected",
                    });
                    await load();
                  }}
                >
                  Reject
                </button>
              </div>
            </article>
          ))
        ) : (
          <Empty
            icon={<BookOpen />}
            title="The playbook starts with evidence"
            text="Daily closed-case reviews propose lessons here. You approve each one before workers use it."
          />
        )}
      </section>
      <section>
        <h2>Add an observation</h2>
        <form
          className="lesson-form"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await api("/lessons", "POST", {
                title,
                lesson,
                limitations: limits,
                supporting_cases: support
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              });
              setTitle("");
              setLesson("");
              setLimits("");
              setSupport("");
              await load();
            } catch (error) {
              notify((error as Error).message);
            }
          }}
        >
          <label>
            Lesson title
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
            />
          </label>
          <label>
            What the evidence suggests
            <textarea
              value={lesson}
              onChange={(e) => setLesson(e.target.value)}
              required
            />
          </label>
          <label>
            Supporting case numbers
            <input
              value={support}
              onChange={(e) => setSupport(e.target.value)}
              placeholder="Comma-separated case numbers"
            />
          </label>
          <label>
            Limitations or counterexamples
            <textarea
              value={limits}
              onChange={(e) => setLimits(e.target.value)}
            />
          </label>
          <button className="primary">Save candidate</button>
        </form>
      </section>
    </main>
  );
}

function App() {
  const [view, setView] = useState("cases"),
    [scope, setScope] = useState("open"),
    [items, setItems] = useState<Case[]>([]),
    [closed, setClosed] = useState<Case[]>([]),
    [overview, setOverview] = useState<Overview | null>(null),
    [selected, setSelected] = useState<string | null>(null),
    [item, setItem] = useState<Case | null>(null),
    [search, setSearch] = useState(""),
    [filter, setFilter] = useState("all"),
    [toast, setToast] = useState(""),
    [error, setError] = useState(""),
    [refreshing, setRefreshing] = useState(false),
    [mobileDetail, setMobileDetail] = useState(false);
  const notify = useCallback((s: string) => setToast(s), []);
  const load = useCallback(async () => {
    const [o, c, cl] = await Promise.all([
      api<Overview>("/overview"),
      api<Case[]>("/cases?scope=" + scope),
      api<Case[]>("/cases?scope=closed"),
    ]);
    setOverview(o);
    setItems(c);
    setClosed(cl);
    setError("");
  }, [scope]);
  useEffect(() => {
    load().catch((e) => setError(e.message));
    const timer = setInterval(
      () => load().catch((e) => setError(e.message)),
      10000,
    );
    return () => clearInterval(timer);
  }, [load]);
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(""), 6000);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  const rank = (c: Case) => {
    const overdue = c.actions.some(
      (a) =>
        a.state === "proposed" &&
        a.due_at &&
        new Date(a.due_at).getTime() < Date.now(),
    );
    return (
      (overdue ? 100 : 0) +
      (c.evaluation?.scores.ai_sat != null
        ? (10 - c.evaluation.scores.ai_sat) * 5
        : 20) +
      (c.priority === "High" ? 10 : 0)
    );
  };
  const filtered = items
    .filter((c) =>
      (
        c.account +
        " " +
        c.number +
        " " +
        c.status +
        " " +
        (c.analysis?.summary || "")
      )
        .toLowerCase()
        .includes(search.toLowerCase()),
    )
    .filter(
      (c) =>
        filter === "all" ||
        (filter === "attention"
          ? Object.values(c.evaluation?.scores || {}).some(
              (v) => v != null && v < 8,
            )
          : filter === "drafts"
            ? c.draft_count > 0
            : !c.evaluation),
    )
    .sort((a, b) => rank(b) - rank(a));
  useEffect(() => {
    if (!selected && filtered.length) setSelected(filtered[0].id);
  }, [filtered, selected]);
  const reloadCase = useCallback(async () => {
    if (selected) {
      const c = await api<Case>("/cases/" + selected);
      setItem(c);
    }
    await load();
  }, [selected, load]);
  useEffect(() => {
    let current = true;
    setItem(null);
    if (selected)
      api<Case>("/cases/" + selected)
        .then((c) => {
          if (current) setItem(c);
        })
        .catch((e) => setError(e.message));
    return () => {
      current = false;
    };
  }, [selected]);
  useEffect(() => {
    if (selected)
      api<Case>("/cases/" + selected)
        .then(setItem)
        .catch(() => {});
  }, [
    overview?.collected_at,
    overview?.workers.map((w) => w.state).join(","),
    selected,
  ]);
  const running = overview?.runs.some((r) => r.state === "running") || false;
  async function refresh() {
    setRefreshing(true);
    try {
      const r = await api<{ state: string }>("/refresh", "POST", {
        closed_review: false,
        analyze: true,
      });
      notify(
        r.state === "already_running"
          ? "A refresh is already running."
          : "Refresh started. Results appear as cases finish.",
      );
      await load();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  }
  return (
    <div className={`app ${mobileDetail ? "show-detail" : ""}`}>
      <aside className="rail">
        <a href="/" className="brand" aria-label="Workbench home">
          <LayoutPanelLeft size={27} />
        </a>
        <nav>
          {[
            ["cases", Inbox, "Cases"],
            ["learning", BookOpen, "Learning"],
            ["operations", Server, "Operations"],
          ].map(([k, Icon, label]) => {
            const C = Icon as typeof Inbox;
            return (
              <button
                title={label as string}
                aria-label={label as string}
                key={k as string}
                className={view === k ? "active" : ""}
                onClick={() => setView(k as string)}
              >
                <C size={21} />
                <span>{label as string}</span>
              </button>
            );
          })}
        </nav>
        <div className="rail-bottom">
          <span className="local-indicator" title="Runs on this Mac" />
          <div className="avatar" title={overview?.owner || "My queue"}>
            AA
          </div>
        </div>
      </aside>
      <div className="app-body">
        <header className="topbar">
          <div>
            <strong>Workbench</strong>
            <span className="topbar-divider" />
            <span>My support desk</span>
          </div>
          <div>
            <span className="top-date">
              {date(new Date().toISOString())} · IST
            </span>
            <button
              className="refresh-button"
              onClick={refresh}
              disabled={refreshing || running}
            >
              {running || refreshing ? (
                <Loader2 className="spin" size={15} />
              ) : (
                <RefreshCw size={15} />
              )}
              <span>{running ? "Refreshing…" : "Refresh"}</span>
            </button>
          </div>
        </header>
        {error && (
          <div className="error-banner">
            <TriangleAlert size={16} />
            {error}
            <button onClick={() => load().catch((e) => setError(e.message))}>
              Retry
            </button>
          </div>
        )}
        {view === "cases" ? (
          <div className="case-layout">
            <aside className="queue">
              <div className="queue-heading">
                <div>
                  <h2>{scope === "open" ? "Your queue" : "Closed cases"}</h2>
                  <span className="count">{items.length}</span>
                </div>
                <p>
                  {scope === "open"
                    ? "Every assigned case. A clear next step."
                    : "The last 30 days of closures."}
                </p>
              </div>
              <div className="queue-tools">
                <div className="segmented scope">
                  <button
                    className={scope === "open" ? "active" : ""}
                    onClick={() => {
                      setScope("open");
                      setSelected(null);
                    }}
                  >
                    Open cases
                  </button>
                  <button
                    className={scope === "closed" ? "active" : ""}
                    onClick={() => {
                      setScope("closed");
                      setSelected(null);
                    }}
                  >
                    Recently closed
                  </button>
                </div>
                <label className="search">
                  <Search size={15} />
                  <input
                    aria-label="Search cases"
                    placeholder="Search account or case…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
                <div className="filter-row">
                  <select
                    aria-label="Filter cases"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  >
                    <option value="all">All cases</option>
                    <option value="attention">Scores below 8</option>
                    <option value="drafts">Drafts ready</option>
                    <option value="unscored">Unscored</option>
                  </select>
                  <small>{filtered.length} shown</small>
                </div>
              </div>
              <div className="case-list">
                {filtered.map((c) => (
                  <button
                    className={`case-row ${selected === c.id ? "active" : ""}`}
                    key={c.id}
                    onClick={() => {
                      setSelected(c.id);
                      setMobileDetail(true);
                    }}
                  >
                    <div className="row-top">
                      <span>{c.number}</span>
                      <span
                        className={`queue-score ${band(c.evaluation?.scores.ai_sat)}`}
                      >
                        {score(c.evaluation?.scores.ai_sat)}
                        <small> AI SAT</small>
                      </span>
                    </div>
                    <strong>{c.account || "Account unavailable"}</strong>
                    <p>
                      {c.analysis?.summary?.split("\n")[0]?.slice(0, 95) ||
                        c.status}
                    </p>
                    <div className="row-bottom">
                      <span className={c.priority === "High" ? "high" : ""}>
                        {c.priority}
                      </span>
                      <span>
                        {c.draft_count ? (
                          <>
                            <FileText size={12} />
                            {c.draft_count} drafts
                          </>
                        ) : c.analysis ? (
                          "Analysis ready"
                        ) : (
                          "Analysis pending"
                        )}
                      </span>
                    </div>
                  </button>
                ))}
                {!filtered.length && (
                  <Empty
                    icon={<Inbox />}
                    title={
                      items.length
                        ? "No matching cases"
                        : "Your queue is waiting"
                    }
                    text={
                      items.length
                        ? "Try another search or filter."
                        : "Run a refresh to collect your assigned cases."
                    }
                  />
                )}
              </div>
              <footer className="queue-footer">
                <span
                  className={`connection-dot ${overview?.inventory_complete ? "ready" : "partial"}`}
                />
                <div>
                  {overview?.inventory_complete
                    ? "Live inventory verified"
                    : "Inventory not yet verified"}
                  <small>Collected {date(overview?.collected_at, true)}</small>
                </div>
              </footer>
            </aside>
            {item ? (
              <CaseWorkspace
                item={item}
                onReload={reloadCase}
                onBack={() => setMobileDetail(false)}
                notify={notify}
              />
            ) : (
              <div className="workspace-loading">
                {selected ? (
                  <>
                    <Loader2 size={24} className="spin" />
                    <p>Loading case…</p>
                  </>
                ) : (
                  <Empty
                    icon={<Inbox />}
                    title="A working desk for your cases"
                    text="Your queue, score explanations, next steps and reviewable drafts will appear here."
                  />
                )}
              </div>
            )}
          </div>
        ) : view === "operations" ? (
          <Operations overview={overview} onRefresh={refresh} />
        ) : (
          <Learning cases={closed} notify={notify} />
        )}
      </div>
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={17} />
          <span>{toast}</span>
          <button
            aria-label="Dismiss notification"
            onClick={() => setToast("")}
          >
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
