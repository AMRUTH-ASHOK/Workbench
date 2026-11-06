from pathlib import Path
import pytest
from workbench.config import Settings
from workbench.models import Bundle, CaseRecord, Evaluation, Analysis, DraftInput
from workbench.store import Store

CASE_ID = "500000000000000001"
OTHER_ID = "500000000000000002"
EMAIL = "engineer@example.test"


@pytest.fixture
def store(tmp_path):
    return Store(Settings(data_dir=tmp_path, owner_email=EMAIL, owner_name="Example Engineer"))


def case(identifier=CASE_ID, revision="revision-1", **overrides):
    return CaseRecord(id=identifier, number="00000001" if identifier == CASE_ID else "00000002", account="Example account", status="Awaiting Customer Response", priority="High", owner_email=EMAIL, updated_at="2026-11-06T10:00:00+00:00", revision=revision, **overrides)


def bundle(cases=None, evaluations=None, **overrides):
    return Bundle(run_id="test-run", owner_email=EMAIL, collected_at="2026-11-06T11:00:00+00:00", inventory_complete=True, inventory_source="salesforce-live", cases=cases if cases is not None else [case()], evaluations=evaluations or [], **overrides)


def evaluation(**overrides):
    values=dict(case_id=CASE_ID, result_id="result-1", source="official.open_ai_csat_current", phase="open", produced_at="2026-11-06T10:30:00+00:00", covered_at="2026-11-06T10:00:00+00:00", scores={"responsiveness":9,"technical_depth":9,"communication":9,"escalation_judgment":None,"resolution_effectiveness":8,"ai_sat":8})
    values.update(overrides)
    return Evaluation(**values)


def analysis(**overrides):
    values=dict(case_id=CASE_ID, run_id="test-analysis", source_revision="revision-1", summary="A confirmed workaround is awaiting customer validation.", drafts=[DraftInput(kind="customer", title="Validation follow-up", body="Please confirm whether the documented workaround resolved the issue.")], next_actions=[{"title":"Review the customer validation", "owner":"Me", "due_at":None}])
    values.update(overrides)
    return Analysis(**values)
