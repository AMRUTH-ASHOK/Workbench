"""Run inside an owned Arclet; export a versioned, own-case collection bundle.

Uses existing dbexec MCP transports and their current credentials. No outbound
communication, feedback submission, live re-scoring or infrastructure jobs.
"""
import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from mcp_client import MCP

HOST = "central-logfood-prodtools-azure-westus"
OPEN = "main.eng_support_ops_efficiency.open_ai_csat_current"
CLOSED = "main.eng_support_ops_efficiency.enriched_cases"
LEDGER = "main.eng_support_ops_efficiency.case_enrichment_results"
USAGE = "main.eng_support_ops_efficiency.support_agent_skill_invocations"
DIMENSIONS = ["responsiveness", "technical_depth", "communication", "escalation_judgment", "resolution_effectiveness"]


def stamp(value=None):
    if not value:
        return datetime.now(timezone.utc).isoformat()
    value = str(value).replace(" ", "T").replace("Z", "+00:00")
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def unjson(value, default=None):
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default if default is not None else {}
    value = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", str(value))
    return json.loads(value)


def field(record, key, default=None):
    return record.get("Case." + key, record.get(key, default))


def salesforce_inventory(sf, email, closed=False):
    # The engineer selected Case, these fields, own-email scope and the 30-day
    # closure window during setup. Never select Subject or another blocked field.
    discovery = sf.call("discover_salesforce_fields_on_object", {"sfObjectName": "Case", "fieldNames": ["Id", "CaseNumber", "Status", "Priority", "OwnerId", "AccountId", "IsClosed", "ClosedDate", "LastModifiedDate"]})
    fields = {f["api_name"]: f for f in discovery.get("ranked", [])}
    blocked = {f["api_name"] for f in discovery.get("blocked", [])}
    required = ["Id", "CaseNumber", "Status", "Priority", "OwnerId", "AccountId", "IsClosed", "ClosedDate", "LastModifiedDate"]
    if any(k not in fields or k in blocked for k in required):
        raise RuntimeError("Live inventory schema changed; review the allowed Case fields")
    user_fields = sf.call("discover_salesforce_fields_on_object", {"sfObjectName": "User", "fieldNames": ["Email"]})
    account_fields = sf.call("discover_salesforce_fields_on_object", {"sfObjectName": "Account", "fieldNames": ["Name"]})
    email_contract = next((f for f in user_fields["ranked"] if f["api_name"] == "Email"), None)
    account_contract = next((f for f in account_fields["ranked"] if f["api_name"] == "Name"), None)
    if not email_contract or not account_contract:
        raise RuntimeError("Live inventory relationship schema changed")
    records, last = [], None
    for _ in range(40):
        where = [
            {"field": "Owner.Email", "operator": "=", "value": email, "fieldType": email_contract["data_type"], "logic": "AND"},
            {"field": "IsClosed", "operator": "=", "value": str(closed).lower(), "fieldType": fields["IsClosed"]["data_type"], "logic": "AND"},
        ]
        if closed:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            where.append({"field": "ClosedDate", "operator": ">=", "value": cutoff, "fieldType": fields["ClosedDate"]["data_type"], "logic": "AND"})
        if last:
            where.append({"field": "CaseNumber", "operator": ">", "value": last, "fieldType": fields["CaseNumber"]["data_type"], "logic": "AND"})
        response = sf.call("execute_soql", {"objectName": "Case", "fields": required + ["Owner.Email", "Account.Name"], "where": where, "orderBy": {"field": "CaseNumber", "direction": "ASC"}, "limit": 50})
        page = response if isinstance(response, list) else response.get("results", response.get("records")) if isinstance(response, dict) else None
        if page is None:
            raise RuntimeError("Unexpected live inventory response: " + str(response)[:1000])
        records.extend(page)
        if len(page) < 50:
            return records
        cursor = field(page[-1], "CaseNumber")
        if not cursor or cursor == last:
            raise RuntimeError("Live inventory pagination did not advance")
        last = cursor
    raise RuntimeError("Live inventory exceeds 2000 records; inventory is incomplete")


def sql_text(client, statement, parameters=None, limit=2000):
    arguments = {"statement": statement, "parameters": parameters or [], "row_limit": limit, "host": HOST, "lineage_tables": []}
    result = client.call("execute_parameterized_sql", arguments)
    deadline = time.monotonic() + 120
    pending = None
    while isinstance(result, str) and "still running" in result.lower():
        match = re.search(r"statement ID:\s*([\w-]+)", result, re.I)
        if not match:
            raise RuntimeError("SQL returned an unrecognized pending statement")
        pending = match.group(1)
        if time.monotonic() > deadline:
            client.call("cancel_statement", {"statement_id": pending, "host": HOST})
            raise TimeoutError("Stored-score query exceeded its deadline")
        time.sleep(3)
        result = client.call("check_statement_status", {"statement_id": pending, "host": HOST})
        if isinstance(result, dict):
            result = result.get("result", str(result))
    if not isinstance(result, str):
        raise RuntimeError("Unexpected SQL response type")
    if any(s in result.lower() for s in ("query failed", "execution failed", "error executing", "not connected", "authentication required", "please connect")):
        raise RuntimeError(result[:2000])
    spill = re.search(r"Full untruncated result written to (\S+) \(", result)
    if spill:
        path = Path(spill.group(1)).resolve()
        if not str(path).startswith("/tmp/databricks_sql_results_"):
            raise RuntimeError("Unexpected SQL spill path")
        result = path.read_text()
    if "...[truncated" in result:
        raise RuntimeError("Refusing to ingest truncated SQL cells")
    return result


def sql_rows(client, statement, parameters=None, limit=2000):
    text = sql_text(client, "SELECT to_json(struct(*)) AS row_json FROM (" + statement + ") wb", parameters, limit)
    rows = []
    for line in text.splitlines():
        if line.lstrip().startswith("{"):
            rows.append(json.loads(line.strip()))
    if not rows and not any(s in text.lower() for s in ("no results", "no rows", "row_json", "0 rows")):
        raise RuntimeError("Unexpected SQL result: " + text[:1200])
    if len(rows) >= limit:
        raise RuntimeError("SQL result reached its row cap; refusing a silently truncated collection")
    return rows


def score_parts(review):
    doc = unjson(review)
    section = doc.get("case_review", {}).get("support_engineer", {})
    if not section:
        section = doc.get("support_engineer", doc)
    scores, reasons = {}, {}
    for name in DIMENSIONS:
        part = section.get(name)
        if isinstance(part, dict):
            scores[name] = part.get("score")
            reasons[name] = str(part.get("reasoning") or "")
        else:
            scores[name] = part if isinstance(part, (float, int)) else None
    overall = doc.get("overall_satisfaction", {})
    scores["ai_sat"] = overall.get("score")
    reasons["ai_sat"] = str(overall.get("reasoning") or "")
    return scores, reasons


def evaluation(case_id, row, source, phase):
    review = row.get("case_review_open_json") if phase == "open" and source == OPEN else row.get("result_json")
    scores, reasons = score_parts(review) if review else ({}, {})
    if source == CLOSED:
        scores = {k: row.get("support_engineer_" + k + "_score") for k in DIMENSIONS}
        scores.update(ai_sat=row.get("satisfaction_overall_score"), customer_engineer=row.get("satisfaction_support_engineer_score"), customer_product=row.get("satisfaction_product_score"))
        reasons = {k: str(row.get("support_engineer_" + k + "_reasoning") or "") for k in DIMENSIONS}
        reasons["ai_sat"] = str(row.get("satisfaction_overall_reasoning") or "")
    if source == OPEN:
        scores["ai_sat"] = row.get("ai_csat", scores.get("ai_sat"))
    produced = row.get("processed_at") or row.get("latest_result_processed_at") or row.get("case_enrichment_cache_processed_at")
    if not produced:
        return None
    raw = dict(case_id=case_id, result_id=str(row.get("result_id") or (source + ":" + str(produced))), source=source, phase=phase, produced_at=stamp(produced), covered_at=stamp(row.get("result_covered_update_at") or row.get("case_updated_at")) if row.get("result_covered_update_at") or row.get("case_updated_at") else None, model=row.get("model_name") or row.get("score_model_name"), rubric=row.get("prompt_model_hash") or row.get("serving_contract_version"), score_status=row.get("score_status") or "latest", scores=scores, reasoning=reasons, evidence={k: row[k] for k in ("alert_decision", "alert_severity", "needs_ai_csat_refresh", "serving_contract_version", "use_case", "enriched_cases_materialized_at") if k in row})
    return raw


def collect(request, directory):
    bundle = dict(schema_version=1, run_id=request["run_id"], owner_email=request["owner_email"], collected_at=stamp(), inventory_complete=False, inventory_source="salesforce-live", cases=[], evaluations=[], sources=[], usage=[], notes=[])
    clients = {}

    def client(name):
        if name not in clients:
            clients[name] = MCP(name, directory)
        return clients[name]

    def status(key, state, message):
        bundle["sources"] = [s for s in bundle["sources"] if s["key"] != key]
        bundle["sources"].append(dict(key=key, state=state, message=str(message)[:1800], checked_at=stamp(), login_url="https://central-logfood-prodtools-azure-westus.azuredatabricks.net" if state == "needs_auth" and key == "scores" else None))

    def failure(key, exc):
        text = str(exc)
        auth = any(s in text.lower() for s in ("auth", "credential", "certificate", "expired", "401", "403", "not connected", "please connect"))
        status(key, "needs_auth" if auth else "unavailable", text)

    try:
        if request.get("score_cases") is not None:
            bundle["cases"] = request["score_cases"]
        else:
            sf = client("salesforce")
            inventory = salesforce_inventory(sf, request["owner_email"])
            status("inventory", "ready", f"Complete live queue: {len(inventory)} assigned open cases; all classes included")
            if request.get("closed_review"):
                try:
                    closed = salesforce_inventory(sf, request["owner_email"], True)
                    inventory += closed
                    status("closed_cases", "ready", f"{len(closed)} assigned cases closed during the past 30 days")
                except Exception as exc:
                    failure("closed_cases", exc)
            for row in inventory:
                case_id = field(row, "Id")
                email = row.get("Owner.Email") or row.get("Owner", {}).get("Email")
                if email.lower() != request["owner_email"].lower():
                    raise RuntimeError("Live inventory returned a different owner")
                record = dict(id=case_id, number=field(row, "CaseNumber"), account=row.get("Account.Name") or row.get("Account", {}).get("Name", ""), subject="", status=field(row, "Status"), priority=field(row, "Priority", ""), owner_email=email, owner_name=request["owner_name"], is_closed=bool(field(row, "IsClosed")), closed_at=stamp(field(row, "ClosedDate")) if field(row, "ClosedDate") else None, updated_at=stamp(field(row, "LastModifiedDate")), source="salesforce-live", revision="", metadata={})
                record["revision"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
                bundle["cases"].append(record)
            bundle["inventory_complete"] = True
            # Publish a usable live queue even if an analytics source fails afterward.
            write_bundle(directory, bundle)
        if request.get("inventory_only"):
            return bundle
        try:
            sql = client("databricks-v2")
            for table in (OPEN, CLOSED, LEDGER):
                schema = sql_text(sql, "DESCRIBE " + table, limit=2000)
                (directory / (table.rsplit(".", 1)[1] + "-schema.txt")).write_text(schema)
            count = 0
            for record in bundle["cases"]:
                cid = record["id"]
                params = [{"name": "case_id", "value": cid}]
                if record["is_closed"]:
                    statement = f"SELECT salesforce_case_id,case_enrichment_cache_processed_at,enriched_cases_materialized_at,support_engineer_score," + ",".join("support_engineer_" + d + suffix for d in DIMENSIONS for suffix in ("_score", "_reasoning")) + ",satisfaction_overall_score,satisfaction_overall_reasoning,satisfaction_support_engineer_score,satisfaction_product_score FROM " + CLOSED + " WHERE salesforce_case_id=:case_id"
                    source, phase = CLOSED, "closed"
                else:
                    statement = f"SELECT salesforce_case_id,case_review_open_json,ai_csat,result_covered_update_at,latest_result_processed_at,score_status,score_model_name,serving_contract_version,alert_decision,alert_severity,needs_ai_csat_refresh FROM {OPEN} WHERE salesforce_case_id=:case_id"
                    source, phase = OPEN, "open"
                try:
                    rows = sql_rows(sql, statement, params, 20)
                    for row in rows:
                        result = evaluation(cid, row, source, phase)
                        if result:
                            bundle["evaluations"].append(result)
                            count += 1
                    ledger = sql_rows(sql, f"SELECT result_id,use_case,model_name,prompt_model_hash,case_updated_at,last_comment_ts,processed_at,result_json FROM {LEDGER} WHERE case_id=:case_id AND use_case IN ('case_review_open','case_review_stage1','case_review') ORDER BY processed_at DESC LIMIT 500", params, 501)
                    for row in ledger:
                        result = evaluation(cid, row, LEDGER, "open" if row["use_case"] == "case_review_open" else "closed")
                        if result:
                            # Stage 1 carries dimensions; final closed review carries AI SAT.
                            bundle["evaluations"].append(result)
                    write_bundle(directory, bundle)
                except Exception as exc:
                    bundle["notes"].append(record["number"] + ": score collection incomplete: " + str(exc)[:600])
                    # A slow serving view must not hide the independent revision
                    # ledger. This remains labelled as ledger history, not a
                    # successful current-view read.
                    try:
                        ledger = sql_rows(sql, f"SELECT result_id,use_case,model_name,prompt_model_hash,case_updated_at,last_comment_ts,processed_at,result_json FROM {LEDGER} WHERE case_id=:case_id AND use_case IN ('case_review_open','case_review_stage1','case_review') ORDER BY processed_at DESC LIMIT 500", params, 501)
                        for row in ledger:
                            result = evaluation(cid, row, LEDGER, "open" if row["use_case"] == "case_review_open" else "closed")
                            if result:
                                result["score_status"] = "ledger_fallback"
                                bundle["evaluations"].append(result)
                        write_bundle(directory, bundle)
                    except Exception as ledger_exc:
                        bundle["notes"].append(record["number"] + ": revision ledger unavailable: " + str(ledger_exc)[:400])
            status("scores", "partial" if bundle["notes"] else "ready", f"{count} current evaluations collected; revisions retained. " + ("Some case queries failed; see case notes." if bundle["notes"] else "Unscored cases remain visible."))
            try:
                sql_text(sql, "DESCRIBE " + USAGE, limit=500)
                ids = [r["id"] for r in bundle["cases"] if not r["is_closed"]]
                if ids:
                    params = [{"name": f"c{i}", "value": cid} for i, cid in enumerate(ids)]
                    marks = ",".join(":c" + str(i) for i in range(len(ids)))
                    bundle["usage"] = sql_rows(sql, f"SELECT case_id,skill_use_id,session_id,completed,tool_invocations,tool_failures,tool_names,feedback_normalized FROM {USAGE} WHERE case_id IN ({marks})", params)
                    for u in bundle["usage"]:
                        u["invocation_id"] = str(u["session_id"]) + ":" + str(u["skill_use_id"])
                status("usage", "ready", "Official usage records collected; upstream ingestion may lag")
            except Exception as exc:
                failure("usage", exc)
        except Exception as exc:
            failure("scores", exc)
        try:
            import subprocess
            cert = Path.home() / ".dbcert/creds.cert.pem"
            expiry = subprocess.check_output(["openssl", "x509", "-in", str(cert), "-enddate", "-noout"], text=True).strip()
            expired = subprocess.run(["openssl", "x509", "-in", str(cert), "-checkend", "0", "-noout"], capture_output=True).returncode != 0
            soon = subprocess.run(["openssl", "x509", "-in", str(cert), "-checkend", "86400", "-noout"], capture_output=True).returncode != 0
            status("certificate", "needs_auth" if expired else "partial" if soon else "ready", expiry + (". Renew your dbcert in ArcaTerm, then Resume." if expired or soon else ""))
        except Exception as exc:
            failure("certificate", exc)
    except Exception as exc:
        bundle["inventory_complete"] = False
        failure("inventory", exc)
    finally:
        bundle["collected_at"] = stamp()
        write_bundle(directory, bundle)
        for c in clients.values():
            c.close()
    return bundle


def write_bundle(directory, bundle):
    path = directory / "bundle.json"
    temp = directory / "bundle.tmp"
    temp.write_text(json.dumps(bundle, ensure_ascii=False))
    temp.chmod(0o600)
    temp.replace(path)


if __name__ == "__main__":
    import signal
    def deadline(signum, frame):
        raise TimeoutError("Collector reached its overall deadline")
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(1400)
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    args = parser.parse_args()
    request_path = Path(args.request).resolve()
    bundle = collect(json.loads(request_path.read_text()), request_path.parent)
    print(json.dumps({"cases": len(bundle["cases"]), "evaluations": len(bundle["evaluations"]), "sources": bundle["sources"]}))
