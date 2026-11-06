# Integration contracts and operating limits

- **ArcaTerm:** JSON CLI lifecycle, explicit Workbench-owned Arclets/tabs, SSH
  exports. The transcript endpoint is optional because discovery observed identity
  and transcript-availability failures. New Arclet requests are journalled with
  stable UUIDv7 IDs. No commands are typed into an arbitrary focused pane.
- **Inventory:** production Salesforce `Case`, with selected allowed identifiers,
  status, priority, ownership/account relationships, IsClosed and lifecycle
  timestamps. Keyset pages contain at most 50 records. Subject and restricted
  identifiers are never selected through this connector.
- **Current open scores:** `main.eng_support_ops_efficiency.open_ai_csat_current`.
- **Closed scores:** `main.eng_support_ops_efficiency.enriched_cases`.
- **Revisions:** `main.eng_support_ops_efficiency.case_enrichment_results`.
- **Official usage:** `main.eng_support_ops_efficiency.support_agent_skill_invocations`.
- **Case evidence/diagnostics:** existing Support Agent and related read MCPs.
- **Watchtower:** Product Supportability remains the inspected UI reference.
  Stored source data is preferred over browser scraping for scheduled collection.

The UI label Technical Correctness maps to `technical_depth`. Overall SE excludes
null dimensions. Open and closed evaluation pipelines differ. Official evaluations
can lag case updates or revise unchanged evidence. The workbench does not launch
live re-scoring; four refreshes do not promise four new official scores.

Authentication stays with the existing Arclet/Isaac environment. Explicit refresh
and login are centralized operational tasks; case workers cannot start their own
workspace-login flows. Each environment can still encounter managed credential
expiry. The dashboard reports the affected stage and permits a manual resume.
It cannot collect or notify while the Mac is powered off or offline.

Collection and case workers are bounded to approximately 25 minutes. A failed
worker receives a retry cooldown rather than an immediate loop. Source queries
and source schemas can change upstream; failures are visible rather than replaced
by invented scores. A serving-view timeout can fall back to retained ledger history.

The daily learning pass proposes descriptive review items. It does not invent a
causal explanation, promote a lesson automatically, or rewrite trusted project
instructions. Human-marked sent/submitted states are evidence of the engineer's
recorded action; the system does not claim to have sent or verified delivery.
