"""Run a bounded Isaac worker, with explicit output and a draft-only tool gate."""
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def write(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False))
    tmp.chmod(0o600)
    tmp.replace(path)


def bind_manifest(structured, job):
    if not isinstance(structured, dict):
        raise ValueError("Worker result must be an object")
    if job.get("run_id"):
        if structured.get("case_id") != job["case_id"] or structured.get("source_revision") != job["source_revision"]:
            raise ValueError("Worker content does not match the assigned case revision")
        # Run identity belongs to the harness. A resumed conversation can retain
        # an earlier run label even when it correctly processes current evidence.
        structured = dict(structured, run_id=job["run_id"])
    return structured


def run(job_path):
    os.umask(0o077)
    job_path = Path(job_path).resolve()
    job = json.loads(job_path.read_text())
    directory = job_path.parent
    os.environ["WORKBENCH_JOB_DIR"] = str(directory)
    policy = Path(__file__).with_name("worker_policy.py")
    settings = {"hooks": {event: [{"matcher": "*", "hooks": [{"type": "command", "command": f"python3.10 {policy}"}]}] for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure")}}
    write(directory / "worker-settings.json", settings)
    status = {"job_id": job["job_id"], "case_id": job.get("case_id"), "state": "working", "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}
    write(directory/"status.json", status)
    args = ["isaac", "--claude", "-p", "--output-format", "json", "--settings", str(directory/"worker-settings.json"), "--permission-mode", "dontAsk", "--permission-prompts", "none", "--name", "Workbench " + job.get("case_number", "learning"), "--append-system-prompt", job["instructions"], "--json-schema", json.dumps(job["schema"])]
    if job.get("session_id"):
        args += ["--resume", job["session_id"]]
    log = open(directory/"worker.log", "ab", buffering=0)
    proc = None
    try:
        with open(directory/"response.json", "wb") as output:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=output, stderr=log, cwd=str(Path.home()/"universe"), start_new_session=True)
            try:
                proc.communicate(job["prompt"].encode(), timeout=job.get("timeout", 1500))
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                raise TimeoutError("Case worker exceeded its 25-minute deadline")
        result = json.loads((directory/"response.json").read_text())
        if isinstance(result, list):
            result = next((r for r in reversed(result) if r.get("type") == "result"), {})
        status["session_id"] = result.get("session_id")
        status["duration_ms"] = result.get("duration_ms")
        if proc.returncode or result.get("is_error"):
            raise RuntimeError(str(result.get("result", "Isaac worker failed"))[:2000])
        structured = result.get("structured_output")
        if structured is None:
            raw = str(result.get("result", "")).strip()
            if raw.startswith("```"):
                raw = raw.split("\n",1)[1].rsplit("```",1)[0]
            try:
                structured = json.loads(raw)
            except ValueError:
                candidate = directory/"analysis.json"
                if candidate.exists():
                    structured = json.loads(candidate.read_text())
                else:
                    raise RuntimeError("Worker did not return a structured result")
        structured = bind_manifest(structured, job)
        if job.get("case_id"):
            from mcp_client import MCP
            from collector import stamp
            client = MCP("support-agent", directory, timeout=60)
            try:
                live = client.call("get_support_case", {"salesforce_case_id": job["case_id"]})
                unchanged = live.get("case_id") == job["case_id"] and stamp(live.get("last_updated_ts")) == job["case_updated_at"] and live.get("owner_name") == job["owner_name"] and not live.get("is_closed")
                status["source_validation"] = "verified" if unchanged else "changed"
                if not unchanged:
                    raise RuntimeError("Live case changed or ownership/lifecycle no longer matches; refresh before publishing drafts")
            finally:
                client.close()
        write(directory/"result.json", structured)
        status["state"] = "complete"
    except Exception as exc:
        status.update(state="failed", error=str(exc)[:2000])
    finally:
        log.close()
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        write(directory/"status.json", status)
        print(json.dumps(status))


if __name__ == "__main__":
    run(sys.argv[1])
