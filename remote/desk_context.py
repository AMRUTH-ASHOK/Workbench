"""Bounded collection of existing activity. No enrichment or diagnostic calls."""
import json
import re
import time
from datetime import datetime, timezone
from mcp_client import MCP
from collector import stamp


def collect_context(job, directory):
    end = time.monotonic() + 180
    clients = {}
    result = dict(case_id=job["case_id"], source_revision=job["source_revision"],
                  observed_at=stamp(), messages=[], documents=[], gaps=[], source_validation="unverified")

    def call(server, name, arguments):
        reserve = 20 if server != 'support-agent' else 0
        remaining = end - time.monotonic() - reserve
        if remaining <= 0:
            raise TimeoutError("Activity collection reached its bounded source budget")
        if server not in clients:
            clients[server] = MCP(server, directory, timeout=min(30, remaining))
        remaining = end - time.monotonic() - reserve
        if remaining <= 0:
            raise TimeoutError("Activity collection reached its bounded source budget")
        clients[server].timeout = min(75 if name == 'list_support_case_comments' else 45, remaining)
        return clients[server].call(name, arguments)

    def matches(live):
        return isinstance(live, dict) and live.get("case_id") == job["case_id"] and stamp(live.get("last_updated_ts")) == job["case_updated_at"] and live.get("owner_name") == job["owner_name"] and not live.get("is_closed")

    try:
        live = call("support-agent", "get_support_case", {"salesforce_case_id": job["case_id"]})
        result["live"] = live
        if not matches(live):
            result['source_validation'] = 'changed'
            raise ValueError("Live owner/lifecycle/update changed; recollect this case before drafting")
        cached = job.get('cached_context')
        if isinstance(cached, dict) and cached.get('case_id') == job['case_id'] and cached.get('source_revision') == job['source_revision'] and cached.get('source_validation') == 'verified':
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp(cached['observed_at']))).total_seconds()
            except (KeyError, ValueError, TypeError):
                age = 901
            if 0 <= age <= 900:
                # Model/coverage retries reuse evidence, not a false new read.
                result = dict(cached, live=live, live_checked_at=stamp(), reused=True,
                    gaps=cached.get('gaps', []) + ['Retry reused activity collected at '+cached['observed_at']+'. Live case metadata was rechecked; linked activity remains dated to its original collection.'])
                return result
        try:
            page = call("support-agent", "list_support_case_comments", {"salesforce_case_id": job["case_id"], "page_size": 60, "use_cache": False})
        except Exception as exc:
            # Verified live metadata and dated evidence can still support a
            # limited brief. Never invent activity or repeat an unverified ask.
            page = {}
            result['gaps'].append('Latest comments unavailable: '+str(exc)[:300]+'. Earlier activity is dated evidence; completed requests and latest wording could not be checked.')
        if isinstance(page, dict):
            comments = page.get("comments", page.get("case_comments", page.get("support_case_comments", [])))
            if page.get("next_page_token"):
                result["gaps"].append("Only the newest 60 comments were read; older history is dated prior evidence.")
        elif isinstance(page, list):
            comments = page
        else:
            comments = []
            result["documents"].append(dict(source="Salesforce comments (privacy summary)", text=str(page)))
            result["gaps"].append("Comments are a privacy summary, not structured messages; exact-text matching and style learning were skipped.")
        for c in comments:
            if not isinstance(c, dict) or not c.get("comment_id") or not c.get("created_ts"):
                continue
            body = c.get("comment_body", "")
            if not isinstance(body, str):
                continue
            if re.search(r'privacy[- ]summary|privacy[- ]summarized|sanitized summary|redacted summary', body, re.I):
                result['documents'].append(dict(source='Salesforce comment (privacy summary)', text=body))
                result['gaps'].append('A summarized comment was excluded from exact-text matching and style learning.')
                continue
            result["messages"].append(dict(id=c["comment_id"], body=body,
                created_at=stamp(c["created_ts"]), author_email=c.get("created_by_email", ""),
                author_name=c.get("created_by_name", ""), audience="internal" if c.get("is_internal") else "customer",
                provenance="structured_source", source="Salesforce"))
        text = json.dumps(page, ensure_ascii=False) + json.dumps(job.get("linked_evidence", []))
        # Only explicitly linked tickets; no broad search or arbitrary URLs.
        keys = list(dict.fromkeys(re.findall(r"\b(?:ES|BL|GENIECODE)-\d+\b", text)))[:3]
        for key in keys:
            if end - time.monotonic() < 50:
                result["gaps"].append("Additional linked activity deferred to preserve the live-validation budget.")
                break
            try:
                value = call("jira", "jira_read_api_call", {"endpoint": "issues.get", "params": {"issue_id": key}})
                result["documents"].append(dict(source="Linked Jira " + key, text=json.dumps(value, ensure_ascii=False)[:20000]))
                result['messages'].extend(jira_messages(key, value))
            except Exception as exc:
                result["gaps"].append("Linked Jira " + key + ": " + str(exc)[:250])
        # Linked Slack threads are read only, scoped to exact channel/timestamp.
        links = list(dict.fromkeys(re.findall(r"https://[a-zA-Z0-9.-]+\.slack\.com/archives/([A-Z0-9]+)/p(\d{16})", text)))[:2]
        for channel, ts in links:
            if end - time.monotonic() < 50:
                result["gaps"].append("Additional linked threads deferred; no diagnostic work was started.")
                break
            try:
                value = call("slack", "slack_read_api_call", {"endpoint": "conversations.replies", "params": {"channel": channel, "ts": ts[:-6] + "." + ts[-6:], "limit": 30}})
                result["documents"].append(dict(source="Linked Slack " + channel, text=json.dumps(value, ensure_ascii=False)[:20000]))
                # Slack user IDs alone cannot prove engineer attribution. No learning from them.
            except Exception as exc:
                result["gaps"].append("Linked Slack thread: " + str(exc)[:250])
        after = call("support-agent", "get_support_case", {"salesforce_case_id": job["case_id"]})
        if not matches(after):
            result['live'] = after
            result['source_validation'] = 'changed'
            raise ValueError("Case changed during activity collection; drafts must wait for recollection")
        result["source_validation"] = "verified"
    except Exception as exc:
        result["gaps"].append(str(exc)[:500])
    finally:
        for client in clients.values():
            client.close()
    return result


def jira_messages(key, value):
    """Only structured comments with an explicit email can establish attribution."""
    if not isinstance(value, dict):
        return []
    comments = value.get('fields', {}).get('comment', {}).get('comments', [])
    messages = []
    for comment in comments[-30:]:
        author = comment.get('author') or {}
        body = comment.get('body')
        if not isinstance(body, str) or not author.get('emailAddress') or not comment.get('id') or not comment.get('created'):
            continue
        if re.search(r'privacy[- ]summary|privacy[- ]summarized|sanitized summary|redacted summary', body, re.I):
            continue
        try:
            created = stamp(comment['created'])
        except (ValueError, TypeError):
            continue
        messages.append(dict(id='jira:'+key+':'+str(comment['id']),body=body,created_at=created,
            author_email=author['emailAddress'],author_name=author.get('displayName',''),
            audience='engineering',provenance='structured_source',source='Jira '+key))
    return messages
