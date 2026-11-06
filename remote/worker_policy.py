"""Claude hooks that enforce draft-only workers independently of the prompt."""
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

SAFE_SCRIPTS = {
    "parse_support_case_arg.py", "start_session.py", "init_case_investigation.py",
    "get_active_support_case_id.py", "save_case_metadata.py", "save_session_context.py",
    "load_investigation.py", "step_banner.py", "start_skill.py", "end_skill.py",
    "end_session.py", "save_skill_findings.py", "save_judge_verdict.py", "persist_debug_fields.py",
    "refresh_log_anchors.py", "pending_enrichment.py", "register_skill.py", "log_event.py",
    "send_telemetry.py", "render_html_report.py", "parse_glean_cases.py",
    "resolve_paths.py", "read_skill_rca.py", "generate_dbr_doctor_link.py",
}
SUPPORT_READS = {
    "get_support_case", "get_case_id_by_case_number", "list_support_case_comments",
    "list_support_case_attachments", "get_support_case_attachment", "get_server_info",
    "fetch_case_summary", "fetch_similar_cases", "fetch_next_best_action", "get_sql_tools",
    "run_sql_tool", "check_sql_query_status", "get_sawmill_url",
    # Normal /start enrichment is authorized. Customer-infrastructure jobs are not.
    "generate_case_summary", "generate_similar_cases", "generate_next_best_action",
}


def permitted(name, value, job_dir):
    normalized = name.replace("-", "_")
    if normalized in {"Read", "Glob", "Grep", "ToolSearch", "TaskOutput", "TaskList", "TaskGet", "TaskCreate", "TaskUpdate", "TodoWrite", "StructuredOutput"}:
        return True, "Local evidence and progress"
    if normalized in {"Agent", "Task", "SendMessage", "TeamCreate", "AskUserQuestion", "CronCreate", "CronDelete", "CronList"}:
        return False, "Return a partial result or gap; the coordinator owns workers and scheduling"
    if normalized == "Skill":
        skill = value.get("skill", "")
        if skill.startswith("support-agent:") and not any(x in skill for x in ("feedback", "gap-capture", "sandbox", "noc-", "oncall", "weekly", "briefing", "deploy")):
            return True, "Support Agent investigation/drafting skill; its tools are gated separately"
        if skill in {"case-compass:whats-my-aisat", "case-compass:next-move"}:
            return True, "Case Compass analysis; outbound tools remain denied"
        return False, "Skill is not part of the validated worker path"
    if normalized in {"Write", "Edit", "MultiEdit"}:
        path = Path(value.get("file_path", value.get("path", ""))).expanduser().resolve()
        root = Path(job_dir).resolve()
        if path.parent == root and path.name in {"analysis.json", "notes.md", "drafts.md"}:
            return True, "Local worker result"
        return False, "Workers may write only their own result and notes files"
    if normalized == "Bash":
        command = value.get("command", "")
        if any(s in command for s in ("\n", ";", "&&", "||", "|", "`", "$", ">", "<")):
            return False, "Use one documented helper with absolute paths and literal arguments"
        try:
            parts = shlex.split(command)
        except ValueError:
            return False, "Invalid helper command"
        if not parts:
            return False, "Empty helper command"
        if parts[0] == "date" and all(p in {"-u", "+%Y-%m-%dT%H:%M:%SZ", "+%Y-%m-%d"} for p in parts[1:]):
            return True, "Read current time"
        if Path(parts[0]).name in {"python3", "python3.10"}:
            args = parts[1:]
            if args and args[0] == "-I":
                args = args[1:]
            if not args:
                return False, "A shipped helper is required"
            path = Path(args[0]).expanduser().resolve()
            plugin_root = (Path.home()/".claude/plugins/cache/experimental-plugin-marketplace").resolve()
            try:
                path.relative_to(plugin_root)
            except ValueError:
                return False, "Only installed plugin helpers are allowed"
            if path.name not in SAFE_SCRIPTS or any(a in {"--fast", "--send", "--submit", "--apply", "--push"} for a in args[1:]):
                return False, "Helper or flag can cause an unreviewed external action"
            return True, "Installed investigation helper; feedback/submission helpers are excluded"
        return False, "Shell access is limited to documented investigation helpers"
    if normalized.startswith("mcp__support_agent__"):
        tool = normalized.split("__")[-1]
        return tool in SUPPORT_READS, "Support reads and normal intake enrichment only"
    if normalized.startswith("mcp__databricks_v2__"):
        tool = normalized.split("__")[-1]
        if tool in {"check_statement_status", "cancel_statement", "list_available_workspaces"}:
            return True, "Read or stop own query"
        if tool == "execute_parameterized_sql":
            statement = value.get("statement", "").strip()
            syntax = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|/\*[\s\S]*?\*/|--[^\n]*", " ", statement)
            syntax = syntax.strip()
            forbidden = re.search(r"\b(insert|update|delete|merge|create|drop|alter|truncate|call|grant|revoke|copy|put|restore)\b", syntax, re.I)
            allowed = syntax.lower().startswith(("select ", "with ", "describe ")) and not forbidden and ";" not in syntax
            return bool(allowed), "Read-only stored evaluations and diagnostics"
        return False, "Authentication is centralized; jobs, writes and live scoring are unavailable"
    if normalized.startswith("mcp__glean__"):
        return normalized.split("__")[-1] in {"search", "read_document", "resolve_go_link", "get_person"}, "Read supporting documentation"
    if normalized in {"mcp__jira__jira_read_api_call", "mcp__jira__jira_get_api_info", "mcp__jira__jira_get_service_info", "mcp__slack__slack_read_api_call", "mcp__slack__slack_get_api_info", "mcp__slack__slack_get_service_info", "mcp__google__gmail_message_get", "mcp__google__gmail_message_list", "mcp__google__gmail_thread_get"}:
        return True, "Read linked case context"
    return False, "Tool is outside the draft-only worker allowlist"


def main():
    event = json.load(sys.stdin)
    directory = Path(os.environ["WORKBENCH_JOB_DIR"])
    name = event.get("tool_name", "")
    kind = event.get("hook_event_name", "PreToolUse")
    allowed, reason = permitted(name, event.get("tool_input", {}), directory)
    audit = dict(at=datetime.now(timezone.utc).isoformat(), event=kind, tool=name, allowed=allowed, reason=reason, tool_use_id=event.get("tool_use_id"))
    if name == "Skill":
        audit["skill"] = event.get("tool_input", {}).get("skill")
    if name == "Bash":
        try:
            parts = shlex.split(event.get("tool_input", {}).get("command", ""))
            audit["helper"] = next((Path(p).name for p in parts if p.endswith(".py")), None)
        except ValueError:
            pass
    with (directory / "tool-audit.jsonl").open("a") as out:
        out.write(json.dumps(audit) + "\n")
    if kind == "PreToolUse":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow" if allowed else "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
