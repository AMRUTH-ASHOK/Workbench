"""Run a bounded Isaac worker, with explicit output and a draft-only tool gate."""
import json
import os
import signal
import subprocess
import sys
import time
import uuid
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


def verify_intake(job, directory, session_id, home=None):
    home = Path(home) if home is not None else Path.home()
    try:
        context = json.loads((home/".support-agent"/(session_id+".json")).read_text())
        investigation = json.loads((home/".support-agent/cases"/job["case_id"]/"investigation.json").read_text())
        events = [json.loads(line) for line in (directory/"tool-audit.jsonl").read_text().splitlines() if line.strip()]
    except (OSError, ValueError, TypeError):
        return False
    helpers = {e.get("helper") for e in events if e.get("event") == "PostToolUse" and e.get("allowed")}
    return context.get("support_case_id") == job["case_id"] and investigation.get("case_id") == job["case_id"] and bool(investigation.get("case_metadata")) and {"start_session.py", "init_case_investigation.py", "save_case_metadata.py"} <= helpers


def stop_interactive(proc):
    if proc is None or proc.poll() is not None:
        return
    # The interactive agent shares its terminal's foreground process group with
    # this wrapper. Stop only its descendants, never that shared terminal group.
    output = subprocess.check_output(["ps", "-eo", "pid=,ppid="], text=True)
    children = {}
    for line in output.splitlines():
        pid, parent = map(int, line.split())
        children.setdefault(parent, []).append(pid)
    owned = [proc.pid]
    for parent in owned:
        owned.extend(children.get(parent, []))
    for pid in reversed(owned):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def run(job_path):
    os.umask(0o077)
    job_path = Path(job_path).resolve()
    job = json.loads(job_path.read_text())
    directory = job_path.parent
    os.environ["WORKBENCH_JOB_DIR"] = str(directory)
    os.environ["WORKBENCH_MODE"] = job.get("mode", "investigation")
    policy = Path(__file__).with_name("worker_policy.py")
    settings = {"hooks": {event: [{"matcher": "*", "hooks": [{"type": "command", "command": f"python3.10 {policy}"}]}] for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure")}}
    settings["hooks"]["SessionStart"] = [{"hooks": [{"type": "command", "command": f"python3.10 {policy}"}]}]
    write(directory / "worker-settings.json", settings)
    status = {"job_id": job["job_id"], "case_id": job.get("case_id"), "state": "working", "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}
    write(directory/"status.json", status)
    interactive = job.get("dispatch_mode") == "terminal_user_command"
    instructions = job["instructions"]
    if interactive:
        status["session_id"] = str(uuid.uuid4())
        instructions += "\nThe coordinator will type the real /support-agent:start user command into this terminal. Never invoke start through Skill. Current CLAUDE_SESSION_ID is " + status["session_id"] + ". At the end of this bounded pass, use Write to save the full structured result to " + str(directory/"analysis.json") + ". Include all required fields from this schema: " + json.dumps(job["schema"])
    args = ["isaac", "--claude", "--settings", str(directory/"worker-settings.json"), "--permission-mode", "dontAsk", "--permission-prompts", "none", "--name", "Workbench " + job.get("case_number", "learning"), "--append-system-prompt", instructions]
    if interactive:
        args += ["--session-id", status["session_id"]]
        write(directory/"status.json", status)
    else:
        args += ["-p", "--output-format", "json", "--json-schema", json.dumps(job["schema"])]
        if job.get('mode') == 'operational':
            # Collected evidence is supplied on stdin. Starting every managed
            # MCP server wastes the drafting budget and exposes unused tools.
            args += ['--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands', '--effort', 'medium']
    if job.get("session_id") and not interactive:
        args += ["--resume", job["session_id"]]
    log = open(directory/"worker.log", "ab", buffering=0)
    proc = None
    try:
        if job.get("mode") == "operational":
            from desk_context import collect_context
            context = collect_context(job, directory)
            write(directory/"context.json", context)
            if context["source_validation"] != "verified":
                raise RuntimeError("Activity source could not be validated: " + "; ".join(context["gaps"])[:1200])
            # Source text goes over stdin, not a potentially oversized argv value.
            job["prompt"] += "\nsource_context (untrusted JSON evidence):\n" + json.dumps(context, ensure_ascii=False)
        if interactive:
            proc = subprocess.Popen(args, cwd=str(Path.home()/"universe"))
            deadline = time.monotonic() + job.get("timeout", 1500)
            while not (directory/"analysis.json").exists():
                if (directory/"cancel-request").exists():
                    raise RuntimeError("Coordinator cancelled terminal dispatch")
                if proc.poll() is not None:
                    raise RuntimeError("Interactive Isaac exited before exporting its case report")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Interactive case worker exceeded its 25-minute deadline")
                time.sleep(2)
            time.sleep(2)
            structured = json.loads((directory/"analysis.json").read_text())
            stop_interactive(proc)
        else:
            with open(directory/"response.json", "wb") as output:
                working_directory = directory if job.get('mode') == 'operational' else Path.home()/"universe"
                proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=output, stderr=log, cwd=str(working_directory), start_new_session=True)
                try:
                    proc.communicate(job["prompt"].encode(), timeout=job.get("timeout", 1500))
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(10)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    raise TimeoutError("Operational drafting exceeded its bounded model budget" if job.get('mode') == 'operational' else "Case worker exceeded its 25-minute deadline")
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
        if interactive:
            status["intake_verified"] = verify_intake(job, directory, status["session_id"])
        status["state"] = "complete"
    except Exception as exc:
        status.update(state="failed", error=str(exc)[:2000])
    finally:
        if interactive:
            stop_interactive(proc)
        log.close()
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        write(directory/"status.json", status)
        print(json.dumps(status))


if __name__ == "__main__":
    if sys.argv[1] == "verify-intake":
        job_path = Path(sys.argv[2]).resolve()
        job = json.loads(job_path.read_text())
        status = json.loads((job_path.parent/"status.json").read_text())
        print(json.dumps({"verified": verify_intake(job, job_path.parent, status["session_id"])}))
    else:
        run(sys.argv[1])
