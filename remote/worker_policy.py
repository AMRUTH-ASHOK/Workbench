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
INTAKE_AGENTS = {
    "support-agent:enrichment-runner", "support-agent:sherpa-enrichment",
    "support-agent:skill-matcher", "support-agent:skill-matcher-v2",
    "support-agent:gather-context", "support-agent:jira-esbl-search",
    "support-agent:slack-similar-issues", "support-agent:sql-query-runner",
    "support-agent:mcp-tool-runner", "support-agent:resolve-cluster-id",
}


def helper_command(command):
    """Accept one shipped helper, optionally receiving literal JSON on stdin."""
    if "<<" not in command:
        return command
    match = re.fullmatch(r"([^\n]+?)\s+<<\s*'([A-Z][A-Z0-9_]*)'\s*\n([\s\S]*?)\n\2\s*", command)
    if not match:
        raise ValueError("Only a single quoted JSON heredoc is supported")
    value = json.loads(match.group(3))
    if not isinstance(value, (dict, list)):
        raise ValueError("Helper stdin must be a JSON object or array")
    return match.group(1)


def permitted(name, value, job_dir, mode="investigation"):
    normalized = name.replace("-", "_")
    if mode == "operational":
        if normalized == "StructuredOutput":
            return True, "Return the operational brief"
        if normalized in {"Read", "Write"}:
            path = Path(value.get("file_path", value.get("path", ""))).expanduser().resolve()
            allowed = {"context.json", "analysis.json", "notes.md"} if normalized == "Read" else {"analysis.json", "notes.md"}
            return path.parent == Path(job_dir).resolve() and path.name in allowed, "Only this operational job's local evidence and result"
        return False, "Operational drafting uses collected evidence only; no skills, enrichment, agents, diagnostics or external actions"
    if normalized in {"Read", "Glob", "Grep", "ToolSearch", "TaskOutput", "TaskList", "TaskGet", "TaskCreate", "TaskUpdate", "TodoWrite", "StructuredOutput"}:
        return True, "Local evidence and progress"
    if normalized in {"Agent", "Task"}:
        return value.get("subagent_type") in INTAKE_AGENTS, "Only documented Support Agent intake/read agents; all child tools remain gated"
    if normalized in {"SendMessage", "TeamCreate", "AskUserQuestion", "CronCreate", "CronDelete", "CronList"}:
        return False, "Return a partial result or gap; the coordinator owns workers and scheduling"
    if normalized == "Skill":
        skill = value.get("skill", "")
        if skill in {"support-agent:start", "support-agent:start-v2"}:
            return False, "This command disables model invocation. The coordinator must submit it as a user slash command in ArcaTerm."
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
        try:
            command = helper_command(value.get("command", ""))
        except (ValueError, TypeError):
            return False, "Use one installed helper with literal JSON stdin"
        if any(s in command for s in ("\n", ";", "&&", "||", "|", "`", "$", ">", "<")):
            return False, "Use one documented helper with absolute paths and literal arguments"
        try:
            parts = shlex.split(command)
        except ValueError:
            return False, "Invalid helper command"
        if not parts:
            return False, "Empty helper command"
        if parts == ["git", "config", "user.name"]:
            return True, "Read the engineer's configured sign-off name"
        if len(parts) == 2 and parts[0] == "sleep" and parts[1].isdigit() and 0 < int(parts[1]) <= 30:
            return True, "Bounded intake enrichment polling delay"
        if parts[0] == "date" and all(p in {"-u", "+%Y-%m-%dT%H:%M:%SZ", "+%Y-%m-%d"} for p in parts[1:]):
            return True, "Read current time"
        if Path(parts[0]).name in {"python3", "python3.10"}:
            args = parts[1:]
            if args and args[0] == "-I":
                args = args[1:]
            if not args:
                return False, "A shipped helper is required"
            path = Path(args[0]).expanduser().resolve()
            plugin_roots = [
                Path.home()/".claude/plugins/cache/experimental-plugin-marketplace/support-agent",
                Path.home()/".claude/plugins/marketplaces/plugin-marketplace/experimental/general/support-agent",
            ]
            if not any(path.is_relative_to(root.resolve()) for root in plugin_roots):
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
        if tool == "connect_to_workspace":
            return value.get("workspace") == "central-logfood-prodtools-azure-westus" and not value.get("host"), "Normal intake connection to the existing internal data workspace only; report any required manual authentication"
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
    if normalized.startswith("mcp__imo__"):
        return normalized.split("__")[-1] in {"get_incidents", "get_incident_details", "get_announcements"}, "Read incident and maintenance context for intake"
    if normalized.startswith("mcp__debug_copilot__"):
        return normalized.split("__")[-1] in {"get_cloud_health", "get_network_health", "analyze_service_dependency"}, "Documented read-only intake health checks"
    if normalized in {"mcp__jira__jira_read_api_call", "mcp__jira__jira_get_api_info", "mcp__jira__jira_get_service_info", "mcp__slack__slack_read_api_call", "mcp__slack__slack_get_api_info", "mcp__slack__slack_get_service_info", "mcp__google__gmail_message_get", "mcp__google__gmail_message_list", "mcp__google__gmail_thread_get"}:
        return True, "Read linked case context"
    return False, "Tool is outside the draft-only worker allowlist"


def main():
    event = json.load(sys.stdin)
    directory = Path(os.environ["WORKBENCH_JOB_DIR"])
    if event.get("hook_event_name") == "SessionStart":
        ready = directory / "terminal-ready.json"
        ready.write_text(json.dumps({"session_id": event.get("session_id")}))
        ready.chmod(0o600)
        return
    name = event.get("tool_name", "")
    kind = event.get("hook_event_name", "PreToolUse")
    allowed, reason = permitted(name, event.get("tool_input", {}), directory, os.environ.get("WORKBENCH_MODE", "investigation"))
    audit = dict(at=datetime.now(timezone.utc).isoformat(), event=kind, tool=name, allowed=allowed, reason=reason, tool_use_id=event.get("tool_use_id"))
    if name == "Skill":
        audit["skill"] = event.get("tool_input", {}).get("skill")
    if name in {"Agent", "Task"}:
        audit["agent_type"] = event.get("tool_input", {}).get("subagent_type")
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
