import json
import pytest
from pydantic import ValidationError
from conftest import CASE_ID, OTHER_ID, analysis, bundle, case, evaluation
from workbench.models import DraftPatch, score_band


def test_scores_and_na_match_rubric():
    assert evaluation().scores["overall_se"] == 8.75
    assert [score_band(x) for x in (None,4,7,7.5,8,9,10)] == ["unscored","attention","attention","watch","good","good","good"]
    with pytest.raises(ValidationError):
        evaluation(scores={"ai_sat":float("nan")})
    with pytest.raises(ValidationError):
        evaluation(scores={"responsiveness":9,"overall_se":2})


def test_collection_is_idempotent_and_preserves_revision_history(store):
    b=bundle(evaluations=[evaluation()])
    assert store.ingest(b)==[CASE_ID]
    assert store.ingest(b)==[]
    newer=evaluation(result_id="result-2", produced_at="2026-11-06T12:00:00+00:00", scores={"ai_sat":9})
    store.ingest(bundle(evaluations=[newer]))
    detail=store.case(CASE_ID)
    assert len(detail["history"])==2
    assert detail["evaluation"]["scores"]["ai_sat"]==9
    store.ingest(bundle(evaluations=[evaluation()]))
    assert store.case(CASE_ID)["evaluation"]["result_id"]=="result-2"


def test_wrong_owner_and_orphan_evaluation_do_not_change_database(store):
    b=bundle()
    b.cases[0].owner_email="another@example.test"
    with pytest.raises(ValueError,match="ownership"):
        store.ingest(b)
    assert store.cases()==[]
    e=evaluation(case_id=OTHER_ID)
    with pytest.raises(ValueError,match="inventory"):
        store.ingest(bundle(evaluations=[e]))
    assert store.cases()==[]


def test_only_complete_live_inventory_retires_missing_cases(store):
    store.ingest(bundle(cases=[case(),case(OTHER_ID)]))
    b=bundle(cases=[case()]);b.inventory_complete=False;b.inventory_source="analytics-snapshot"
    store.ingest(b)
    assert len(store.cases())==2
    b.inventory_complete=True
    with pytest.raises(ValueError,match="live inventory"):
        store.ingest(b)
    store.ingest(bundle(cases=[case()]))
    assert len(store.cases())==1
    assert not store.case(OTHER_ID)["active"]


def test_closed_lifecycle_wins_and_drafts_are_superseded(store):
    store.ingest(bundle())
    store.ingest_analysis(analysis())
    draft=store.case(CASE_ID)["drafts"][0]
    store.patch_draft(draft["id"],DraftPatch(state="reviewed"))
    store.ingest(bundle(cases=[case(revision="revision-2",is_closed=True,closed_at="2026-11-06T12:00:00+00:00")]))
    assert store.cases()==[]
    assert len(store.cases("closed"))==1
    assert store.case(CASE_ID)["drafts"][0]["state"]=="superseded"
    with pytest.raises(ValueError,match="superseded"):
        store.patch_draft(draft["id"],DraftPatch(state="copied"))
    with pytest.raises(ValueError,match="superseded"):
        store.ingest_analysis(analysis())


def test_revision_immutability_is_atomic(store):
    store.ingest(bundle(evaluations=[evaluation()]))
    altered=evaluation(scores={"ai_sat":3})
    with pytest.raises(ValueError,match="changed scores"):
        store.ingest(bundle(cases=[case(revision="different")],evaluations=[altered]))
    assert store.case(CASE_ID)["revision"]=="revision-1"


def test_drafts_are_local_and_preserve_human_review(store):
    store.ingest(bundle())
    a=analysis();store.ingest_analysis(a);store.ingest_analysis(a)
    drafts=store.case(CASE_ID)["drafts"]
    assert len(drafts)==1
    store.patch_draft(drafts[0]["id"],DraftPatch(state="manually_sent"))
    store.ingest_analysis(a)
    assert store.case(CASE_ID)["drafts"][0]["state"]=="manually_sent"
    marked=store.case(CASE_ID)
    assert len([a for a in marked['actions'] if a['state']=='done'])==1
    store.patch_draft(drafts[0]['id'],DraftPatch(state='manually_sent'))
    assert len([a for a in store.case(CASE_ID)['actions'] if a['state']=='done'])==1
    store.patch_draft(drafts[0]['id'],DraftPatch(body='Revised local draft'))
    events=store.case(CASE_ID)['events']
    sent=[json.loads(e['detail']) for e in events if e['kind']=='draft_manually_sent']
    assert sent[0]['body']==a.drafts[0].body


def test_worker_actions_and_evidence_have_a_validated_shape(store):
    store.ingest(bundle())
    a=analysis(next_actions=[dict(title='Ask for validation',owner='Customer',waiting_on='customer',reason='No agreed deadline',due_at=None)],evidence=[dict(title='Comment thread',source='support-tooling',detail='Awaiting validation')])
    store.ingest_analysis(a)
    action=store.case(CASE_ID)['actions'][0]
    assert action['title']=='Ask for validation'
    assert action['due_at'] is None
    assert 'Waiting on: customer' in action['note']
    with pytest.raises(ValidationError):
        analysis(next_actions=[dict(action='Silently lost under the old schema')])


def test_current_phase_and_canonical_source_are_preserved(store):
    current=evaluation()
    ledger=evaluation(result_id="ledger-final",source="official.case_enrichment_results",produced_at="2026-11-06T12:00:00+00:00",scores={"ai_sat":9})
    closed=evaluation(result_id="closed",phase="closed",produced_at="2026-11-06T13:00:00+00:00",scores={"ai_sat":2})
    store.ingest(bundle(evaluations=[current,ledger,closed]))
    assert store.case(CASE_ID)["evaluation"]["result_id"]=="result-1"


def test_learning_requires_review_and_never_changes_instructions(store):
    from workbench.models import LessonInput
    identifier=store.add_lesson(LessonInput(title="Test observation",lesson="Keep explicit validation checkpoints",supporting_cases=["00000001"]))
    assert store.lessons()[0]["state"]=="candidate"
    store.review_lesson(identifier,"approved")
    playbook=json.loads((store.settings.data_dir/"evidence/reviewed-playbook.json").read_text())
    assert len(playbook["lessons"])==1
    assert not (store.settings.data_dir/"AGENTS.md").exists()


def test_new_analysis_replaces_automatic_proposals_and_preserves_human_edits(store):
    store.ingest(bundle())
    store.ingest_analysis(analysis())
    first=store.case(CASE_ID)['drafts'][0]
    store.patch_draft(first['id'],DraftPatch(body='Human edited follow-up'))
    next_analysis=analysis(run_id='second-pass',next_actions=[dict(title='Check the validated result',owner='Me')],drafts=[dict(kind='customer',title='Updated follow-up',body='Please confirm the latest result.')])
    store.ingest_analysis(next_analysis)
    detail=store.case(CASE_ID)
    assert {a['title'] for a in detail['actions'] if a['state']=='proposed'}=={'Check the validated result'}
    assert any(d['body']=='Human edited follow-up' and d['state']=='draft' for d in detail['drafts'])
    store.ingest_analysis(analysis(run_id='third-pass'))
    assert any(d['title']=='Updated follow-up' and d['state']=='superseded' for d in store.case(CASE_ID)['drafts'])


def test_worker_date_only_proposal_is_retained_without_inventing_deadline():
    a=analysis(next_actions=[dict(title='Review follow-up',owner='Me',due_at='2026-11-06',reason='Suggested by stored review')])
    assert a.next_actions[0].due_at is None
    assert '2026-11-06' in a.next_actions[0].reason
