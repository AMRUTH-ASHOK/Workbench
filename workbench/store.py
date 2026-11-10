import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from .models import Analysis, Bundle, now, score_band


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def draft_key(case_id, revision, kind, body, variant="standard", scenario="update"):
    # Preserve legacy identifiers, including human review and sent history.
    suffix = "" if (variant, scenario) == ("standard", "update") else f":{variant}:{scenario}"
    return hashlib.sha256(f"{case_id}:{revision}:{kind}:{body}{suffix}".encode()).hexdigest()


class Store:
    def __init__(self, settings):
        self.settings = settings
        settings.prepare()
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY, number TEXT NOT NULL, owner_email TEXT NOT NULL,
                is_closed INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                revision TEXT NOT NULL, record TEXT NOT NULL, observed_at TEXT NOT NULL,
                analysis TEXT, analysis_revision TEXT
            );
            CREATE TABLE IF NOT EXISTS evaluations (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                result_id TEXT NOT NULL, phase TEXT NOT NULL, source TEXT NOT NULL,
                produced_at TEXT NOT NULL, record TEXT NOT NULL,
                UNIQUE(case_id, result_id, source)
            );
            CREATE INDEX IF NOT EXISTS eval_case_time ON evaluations(case_id, phase, produced_at);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, trigger TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, state TEXT NOT NULL, stage TEXT NOT NULL,
                detail TEXT, closed_review INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                revision TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, evidence TEXT NOT NULL, state TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                title TEXT NOT NULL, owner TEXT NOT NULL, due_at TEXT,
                state TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sources (key TEXT PRIMARY KEY, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workers (key TEXT PRIMARY KEY, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS draft_options (draft_id TEXT PRIMARY KEY REFERENCES drafts(id), record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS writing_examples (id TEXT PRIMARY KEY, audience TEXT NOT NULL, record TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS lessons (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, lesson TEXT NOT NULL,
                supporting_cases TEXT NOT NULL, limitations TEXT NOT NULL,
                state TEXT NOT NULL, created_at TEXT NOT NULL, reviewed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, case_id TEXT, kind TEXT NOT NULL,
                detail TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """)
            if "origin_revision" not in {r[1] for r in db.execute("PRAGMA table_info(actions)")}:
                db.execute("ALTER TABLE actions ADD COLUMN origin_revision TEXT")
        settings.db_path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.settings.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def kv(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_kv(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, dumps(value)))

    def source(self, key, state, message, login_url=None):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO sources VALUES (?,?)", (key, dumps(dict(key=key, state=state, message=message, checked_at=now(), login_url=login_url))))

    def start_run(self, trigger, closed_review=False):
        run_id = str(uuid.uuid4())
        with self.connect() as db:
            # Called only while holding the process run lock; unfinished rows are crash remnants.
            db.execute("UPDATE runs SET state='interrupted',finished_at=?,detail='Process stopped before completion' WHERE state='running'", (now(),))
            db.execute("INSERT INTO runs(id,trigger,started_at,state,stage,closed_review) VALUES (?,?,?,'running','Checking connections',?)", (run_id, trigger, now(), int(closed_review)))
        return run_id

    def stage(self, run_id, stage):
        with self.connect() as db:
            db.execute("UPDATE runs SET stage=? WHERE id=?", (stage, run_id))

    def finish_run(self, run_id, state, detail):
        with self.connect() as db:
            db.execute("UPDATE runs SET finished_at=?,state=?,detail=?,stage='Finished' WHERE id=?", (now(), state, detail, run_id))

    def ingest(self, bundle: Bundle):
        if bundle.owner_email.lower() != self.settings.owner_email.lower():
            raise ValueError("Bundle owner does not match configured engineer")
        ids = {c.id for c in bundle.cases}
        if len(ids) != len(bundle.cases):
            raise ValueError("Duplicate case identifiers in inventory")
        if any(c.owner_email.lower() != self.settings.owner_email.lower() for c in bundle.cases):
            raise ValueError("Case ownership mismatch")
        if any(e.case_id not in ids for e in bundle.evaluations):
            raise ValueError("Evaluation does not belong to the supplied inventory")
        if bundle.inventory_complete and bundle.inventory_source != "salesforce-live":
            raise ValueError("Only a complete live inventory can retire missing assignments")
        changed = []
        with self.connect() as db:
            for c in bundle.cases:
                previous = db.execute("SELECT revision,record FROM cases WHERE id=?", (c.id,)).fetchone()
                if not previous or previous["revision"] != c.revision:
                    changed.append(c.id)
                    db.execute("UPDATE drafts SET state='superseded',updated_at=? WHERE case_id=? AND state IN ('draft','reviewed','copied')", (now(), c.id))
                    db.execute("UPDATE actions SET state='superseded' WHERE case_id=? AND state='proposed' AND origin_revision IS NOT NULL", (c.id,))
                    db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (str(uuid.uuid4()), c.id, "case_update", dumps(dict(revision=c.revision, status=c.status)), bundle.collected_at))
                db.execute("""INSERT INTO cases(id,number,owner_email,is_closed,active,revision,record,observed_at)
                    VALUES (?,?,?,?,1,?,?,?) ON CONFLICT(id) DO UPDATE SET number=excluded.number,
                    owner_email=excluded.owner_email,is_closed=excluded.is_closed,active=1,
                    revision=excluded.revision,record=excluded.record,observed_at=excluded.observed_at""",
                    (c.id, c.number, c.owner_email, int(c.is_closed), c.revision, dumps(c.model_dump()), bundle.collected_at))
            if bundle.inventory_complete:
                open_ids = [c.id for c in bundle.cases if not c.is_closed]
                if open_ids:
                    marks = ",".join("?" for _ in open_ids)
                    missing = db.execute(f"SELECT id FROM cases WHERE is_closed=0 AND active=1 AND id NOT IN ({marks})", open_ids).fetchall()
                else:
                    missing = db.execute("SELECT id FROM cases WHERE is_closed=0 AND active=1").fetchall()
                for row in missing:
                    db.execute("UPDATE cases SET active=0 WHERE id=?", (row[0],))
                    db.execute("UPDATE drafts SET state='superseded',updated_at=? WHERE case_id=? AND state IN ('draft','reviewed','copied')", (now(), row[0]))
                    db.execute("UPDATE actions SET state='superseded' WHERE case_id=? AND state='proposed' AND origin_revision IS NOT NULL", (row[0],))
            for e in bundle.evaluations:
                identifier = hashlib.sha256(f"{e.case_id}:{e.source}:{e.result_id}".encode()).hexdigest()
                previous = db.execute("SELECT record FROM evaluations WHERE id=?", (identifier,)).fetchone()
                # A result revision is immutable. Recollecting it can update freshness metadata only.
                if previous:
                    old = json.loads(previous[0])
                    if old["scores"] != e.scores:
                        raise ValueError("An existing evaluation revision changed scores")
                db.execute("INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?,?,?)", (identifier, e.case_id, e.result_id, e.phase, e.source, e.produced_at, dumps(e.model_dump())))
            for source in bundle.sources:
                db.execute("INSERT OR REPLACE INTO sources VALUES (?,?)", (source.key, dumps(source.model_dump())))
            for u in bundle.usage:
                if u.get("case_id") in ids:
                    uid = u.get("invocation_id") or hashlib.sha256(dumps(u).encode()).hexdigest()
                    db.execute("INSERT OR REPLACE INTO usage VALUES (?,?,?)", (uid, u["case_id"], dumps(u)))
            for key, value in {"inventory_complete": bundle.inventory_complete, "inventory_source": bundle.inventory_source, "last_collected_at": bundle.collected_at, "collection_notes": bundle.notes}.items():
                db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, dumps(value)))
        self.save_evidence(f"{bundle.run_id}.json", bundle.model_dump())
        return changed

    def save_evidence(self, filename, value):
        # Only coordinator-generated names, never source-controlled paths.
        if "/" in filename or ".." in filename:
            raise ValueError("Invalid evidence filename")
        path = self.settings.data_dir / "evidence" / filename
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(dumps(value))
        temporary.replace(path)
        return str(path)

    def ingest_analysis(self, analysis: Analysis):
        with self.connect() as db:
            case = db.execute("SELECT revision,active FROM cases WHERE id=?", (analysis.case_id,)).fetchone()
            if not case or not case["active"] or case["revision"] != analysis.source_revision:
                raise ValueError("Analysis is for an unknown or superseded case revision")
            db.execute("UPDATE cases SET analysis=?,analysis_revision=? WHERE id=?", (dumps(analysis.model_dump()), analysis.source_revision, analysis.case_id))
            draft_keys = {draft_key(analysis.case_id, analysis.source_revision, d.kind, d.body, d.variant, d.scenario) for d in analysis.drafts}
            for old in db.execute("SELECT * FROM drafts WHERE case_id=? AND state='draft'", (analysis.case_id,)).fetchall():
                options = self._draft_options(db, old['id'])
                original_key = draft_key(analysis.case_id, old['revision'], old['kind'], old['body'], options['variant'], options['scenario'])
                if old["id"] not in draft_keys and old["id"] == original_key:
                    db.execute("UPDATE drafts SET state='superseded',updated_at=? WHERE id=?", (now(), old["id"]))
            for draft in analysis.drafts:
                key = draft_key(analysis.case_id, analysis.source_revision, draft.kind, draft.body, draft.variant, draft.scenario)
                db.execute("""INSERT OR IGNORE INTO drafts VALUES (?,?,?,?,?,?,?,'draft',?,?)""", (key, analysis.case_id, analysis.source_revision, draft.kind, draft.title, draft.body, dumps([e.model_dump() for e in draft.evidence]), now(), now()))
                options = {k: getattr(draft, k) for k in ('variant', 'scenario', 'use_when', 'review_notes')}
                db.execute("INSERT OR REPLACE INTO draft_options VALUES (?,?)", (key, dumps(options)))
            if self.kv('writing_preferences', {'learning_enabled': True})['learning_enabled']:
                for observation in analysis.writing_observations:
                    self._save_writing_observation(db, analysis.case_id, observation)
            action_keys = {hashlib.sha256(f"{analysis.case_id}:{analysis.source_revision}:{a.title}".encode()).hexdigest() for a in analysis.next_actions}
            for old in db.execute("SELECT id FROM actions WHERE case_id=? AND state='proposed' AND origin_revision IS NOT NULL", (analysis.case_id,)).fetchall():
                if old["id"] not in action_keys:
                    db.execute("UPDATE actions SET state='superseded' WHERE id=?", (old["id"],))
            for proposal in analysis.next_actions:
                action = proposal.model_dump()
                if not action.get("title"):
                    continue
                key = hashlib.sha256(f"{analysis.case_id}:{analysis.source_revision}:{action['title']}".encode()).hexdigest()
                from .models import timestamp
                note = "Waiting on: " + action["waiting_on"] + ". " + action.get("reason", "")
                db.execute("INSERT OR IGNORE INTO actions VALUES (?,?,?,?,?,'proposed',?,?,NULL,?)", (key, analysis.case_id, action["title"], action.get("owner", "Me"), timestamp(action.get("due_at")), note, now(), analysis.source_revision))
        self.save_evidence(f"analysis-{analysis.case_id}-{analysis.run_id}.json", analysis.model_dump())

    def _case(self, db, row, detail=False):
        case = json.loads(row["record"])
        case.update(active=bool(row["active"]), observed_at=row["observed_at"], analysis=json.loads(row["analysis"]) if row["analysis"] else None, analysis_stale=row["analysis_revision"] != row["revision"])
        phase = "closed" if case["is_closed"] else "open"
        evaluations = [json.loads(r[0]) for r in db.execute("SELECT record FROM evaluations WHERE case_id=? ORDER BY produced_at", (case["id"],))]
        current = [e for e in evaluations if e["phase"] == phase]
        canonical = [e for e in current if e["source"].endswith("open_ai_csat_current" if phase == "open" else "enriched_cases")]
        if canonical:
            current = canonical
        case["evaluation"] = current[-1] if current else None
        case["bands"] = {k: score_band(v) for k, v in (case["evaluation"]["scores"] if case["evaluation"] else {}).items()}
        case["draft_count"] = db.execute("SELECT count(*) FROM drafts WHERE case_id=? AND state IN ('draft','reviewed')", (case["id"],)).fetchone()[0]
        case["actions"] = [dict(r) for r in db.execute("SELECT * FROM actions WHERE case_id=? ORDER BY CASE state WHEN 'proposed' THEN 0 WHEN 'done' THEN 1 ELSE 2 END, due_at IS NULL, due_at,created_at", (case["id"],))]
        if detail:
            case["history"] = evaluations
            case["drafts"] = [dict(r) | {"evidence": json.loads(r["evidence"])} | self._draft_options(db, r['id']) for r in db.execute("SELECT * FROM drafts WHERE case_id=? ORDER BY created_at DESC", (case["id"],))]
            case["usage"] = [json.loads(r[0]) for r in db.execute("SELECT record FROM usage WHERE case_id=?", (case["id"],))]
            case["events"] = [dict(r) for r in db.execute("SELECT * FROM events WHERE case_id=? ORDER BY created_at DESC LIMIT 100", (case["id"],))]
            case["outcomes"] = []
            for action in case["actions"]:
                if action["state"] != "done" or not action["completed_at"]:
                    continue
                before = [e for e in evaluations if e["covered_at"] and e["covered_at"] <= action["completed_at"]]
                after = [e for e in evaluations if e["covered_at"] and e["covered_at"] > action["completed_at"]]
                baseline = before[-1] if before else None
                followup = next((e for e in after if baseline and e["source"] == baseline["source"] and e["phase"] == baseline["phase"] and e["model"] == baseline["model"] and e["rubric"] == baseline["rubric"]), None)
                deltas = {k: round(followup["scores"][k] - v, 3) for k, v in baseline["scores"].items() if v is not None and followup and followup["scores"].get(k) is not None} if baseline else {}
                case["outcomes"].append(dict(action_id=action["id"], title=action["title"], completed_at=action["completed_at"], before=baseline, after=followup, deltas=deltas))
        return case

    def cases(self, scope="open"):
        with self.connect() as db:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
            clause = "active=1 AND is_closed=0" if scope == "open" else "is_closed=1 AND json_extract(record,'$.closed_at') >= ?" if scope == "closed" else "1=1"
            args = (cutoff,) if scope == "closed" else ()
            return [self._case(db, row) for row in db.execute(f"SELECT * FROM cases WHERE {clause} ORDER BY observed_at DESC,number DESC", args)]

    def case(self, case_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            return self._case(db, row, True) if row else None

    def overview(self):
        with self.connect() as db:
            return dict(
                owner=self.settings.owner_name, timezone=self.settings.timezone,
                schedule=["13:30", "16:15", "19:00", "22:00"], days="Monday–Friday",
                inventory_complete=self.kv("inventory_complete", False),
                inventory_source=self.kv("inventory_source", "Not collected"),
                collected_at=self.kv("last_collected_at"), notes=self.kv("collection_notes", []),
                sources=[json.loads(r[0]) for r in db.execute("SELECT record FROM sources ORDER BY key")],
                runs=[dict(r) for r in db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 12")],
                workers=[json.loads(r[0]) for r in db.execute("SELECT record FROM workers")],
                good_threshold=8,
            )

    def patch_draft(self, draft_id, patch):
        with self.connect() as db:
            draft = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if not draft:
                raise KeyError("Draft not found")
            case = db.execute("SELECT revision,active FROM cases WHERE id=?", (draft["case_id"],)).fetchone()
            if draft["state"] == "superseded" or not case["active"] or case["revision"] != draft["revision"]:
                raise ValueError("This draft is superseded. Refresh its case before reviewing it.")
            body = patch.body if patch.body is not None else draft["body"]
            state = patch.state or ("draft" if patch.body is not None else draft["state"])
            if draft["kind"] != "feedback" and state == "submitted_manually":
                raise ValueError("Only feedback has a submitted state")
            if draft["kind"] == "feedback" and state == "manually_sent":
                raise ValueError("Feedback uses submitted_manually")
            options = self._draft_options(db, draft_id)
            edited = draft_key(draft['case_id'], draft['revision'], draft['kind'], body, options['variant'], options['scenario']) != draft_id
            if patch.learn_style and (draft['kind'] == 'feedback' or not edited):
                raise ValueError("Style learning requires your own edits to a communication draft")
            prefs = self.kv('writing_preferences', {'learning_enabled': True, 'preferences': {}})
            if patch.learn_style and not prefs['learning_enabled']:
                raise ValueError("Writing style learning is paused")
            if prefs['learning_enabled'] and edited and draft['kind'] != 'feedback' and (patch.learn_style or state == 'manually_sent'):
                self._save_writing_example(db, 'draft:' + draft_id, draft['kind'], body, 'My edited draft', 'draft:' + draft_id)
            db.execute("UPDATE drafts SET body=?,state=?,updated_at=? WHERE id=?", (body, state, now(), draft_id))
            # Preserve the exact reviewed/sent text even if the editable draft changes later.
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (str(uuid.uuid4()), draft["case_id"], "draft_" + state, dumps({"draft_id": draft_id, "kind": draft["kind"], "previous_body": draft["body"], "body": body, "previous_state": draft["state"]}), now()))
            if state in {"manually_sent", "submitted_manually"}:
                recorded = now()
                title = ("Feedback manually submitted: " if state == "submitted_manually" else "Communication manually sent: ") + draft["title"]
                db.execute("INSERT OR IGNORE INTO actions VALUES (?,?,?,?,?,'done',?,?,?,NULL)", ("draft:" + draft_id, draft["case_id"], title, self.settings.owner_name, None, "Engineer recorded this action manually. Workbench did not send or submit anything.", recorded, recorded))
            return dict(db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone())

    @staticmethod
    def _draft_options(db, identifier):
        row = db.execute('SELECT record FROM draft_options WHERE draft_id=?', (identifier,)).fetchone()
        return json.loads(row[0]) if row else dict(variant='standard', scenario='update', use_when='', review_notes='')

    def _save_writing_example(self, db, identifier, audience, body, label, reference):
        from .writing import features
        if len(body.strip()) < 40:
            raise ValueError('Use at least 40 characters to teach writing style')
        record = dict(id=identifier, audience=audience, body=body, features=features(body),
                      origin='confirmed', source_label=label, source_reference=reference, author_email=self.settings.owner_email, updated_at=now())
        db.execute('INSERT OR REPLACE INTO writing_examples VALUES (?,?,?,?)', (identifier, audience, dumps(record), record['updated_at']))

    def _save_writing_observation(self, db, case_id, observation):
        if observation.author_email.lower() != self.settings.owner_email.lower():
            return
        identifier = 'history:' + hashlib.sha256((case_id + observation.audience + ':'.join(sorted(observation.source_message_ids))).encode()).hexdigest()
        forgotten = db.execute('SELECT value FROM kv WHERE key=?', ('forgotten_writing:' + identifier,)).fetchone()
        if forgotten:
            return
        record = dict(id=identifier, audience=observation.audience, origin='historical',
                      source_label='Attributed sent-message history (privacy summary)', source_reference=case_id,
                      source_message_ids=observation.source_message_ids, author_email=observation.author_email,
                      features=observation.model_dump(exclude={'audience', 'author_email', 'source_message_ids'}), updated_at=now())
        db.execute('INSERT OR REPLACE INTO writing_examples VALUES (?,?,?,?)', (identifier, observation.audience, dumps(record), record['updated_at']))

    def add_writing_example(self, example):
        if not self.kv('writing_preferences', {'learning_enabled': True})['learning_enabled']:
            raise ValueError('Writing style learning is paused')
        identifier = hashlib.sha256((example.audience + ':' + example.body).encode()).hexdigest()
        with self.connect() as db:
            self._save_writing_example(db, identifier, example.audience, example.body, example.source_label, 'user_confirmed_example')
        return identifier

    def forget_writing_example(self, identifier):
        with self.connect() as db:
            if not db.execute('DELETE FROM writing_examples WHERE id=?', (identifier,)).rowcount:
                raise KeyError('Writing example not found')
            if identifier.startswith('history:'):
                db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', ('forgotten_writing:' + identifier, dumps(True)))

    def writing_style(self, for_worker=False):
        from .writing import AUDIENCES, describe, historical_descriptions
        prefs = self.kv('writing_preferences', {'learning_enabled': True, 'preferences': {}})
        with self.connect() as db:
            records = [json.loads(r[0]) for r in db.execute('SELECT record FROM writing_examples ORDER BY updated_at DESC')]
        # Only this engineer's confirmed text is eligible. A worker's output or
        # privacy summary cannot insert a sample through analysis ingestion.
        records = [r for r in records if r['author_email'].lower() == self.settings.owner_email.lower()]
        audiences = {}
        for audience in AUDIENCES:
            samples = [r for r in records if r['audience'] == audience and r.get('origin') != 'historical']
            history = [r for r in records if r['audience'] == audience and r.get('origin') == 'historical']
            audiences[audience] = dict(preferences=prefs['preferences'].get(audience, ''), sample_count=len(samples),
                confidence='none' if not samples else 'limited' if len(samples) < 3 else 'developing',
                observations=describe(samples) if not for_worker or prefs['learning_enabled'] else [],
                historical_source_count=len(history), historical_observations=historical_descriptions(history) if not for_worker or prefs['learning_enabled'] else [])
        result = dict(learning_enabled=prefs['learning_enabled'], audiences=audiences)
        if not for_worker:
            result['examples'] = [{k: v for k, v in r.items() if k not in {'body', 'features', 'author_email'}} | {'preview': r.get('body', 'Structural observations only; tentative attribution from privacy summaries.')[:160]} for r in records]
        return result

    def add_action(self, case_id, action):
        if not self.case(case_id):
            raise KeyError("Case not found")
        identifier = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO actions VALUES (?,?,?,?,?,'proposed',?,?,NULL,NULL)", (identifier, case_id, action.title, action.owner, action.due_at, action.note, now()))
        return identifier

    def complete_action(self, identifier, state):
        with self.connect() as db:
            row = db.execute("SELECT * FROM actions WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError("Action not found")
            db.execute("UPDATE actions SET state=?,completed_at=? WHERE id=?", (state, now() if state == "done" else None, identifier))
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (str(uuid.uuid4()), row["case_id"], "action_" + state, dumps({"title": row["title"], "action_id": identifier}), now()))

    def lessons(self):
        with self.connect() as db:
            return [dict(r) | {"supporting_cases": json.loads(r["supporting_cases"])} for r in db.execute("SELECT * FROM lessons ORDER BY created_at DESC")]

    def add_lesson(self, lesson):
        identifier = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO lessons VALUES (?,?,?,?,?,'candidate',?,NULL)", (identifier, lesson.title, lesson.lesson, dumps(lesson.supporting_cases), lesson.limitations, now()))
        return identifier

    def review_lesson(self, identifier, state):
        with self.connect() as db:
            if not db.execute("SELECT id FROM lessons WHERE id=?", (identifier,)).fetchone():
                raise KeyError("Lesson not found")
            db.execute("UPDATE lessons SET state=?,reviewed_at=? WHERE id=?", (state, now(), identifier))
        self.save_evidence("reviewed-playbook.json", {"version": now(), "lessons": [l for l in self.lessons() if l["state"] == "approved"]})

    def worker(self, key, record=None):
        with self.connect() as db:
            if record is not None:
                db.execute("INSERT OR REPLACE INTO workers VALUES (?,?)", (key, dumps(record)))
                return record
            row = db.execute("SELECT record FROM workers WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None
