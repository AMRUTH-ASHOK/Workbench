"""Operational delivery, source attribution, and outcome safety with invented data."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'remote'))
from conftest import CASE_ID, OTHER_ID, EMAIL, analysis, bundle, case, evaluation
from workbench.coordinator import Coordinator, run_lock
from workbench.models import DraftPatch, now
from workbench.operational import coverage, prompt
from worker_policy import permitted


def context(store, messages=None, **changes):
    c = store.case(CASE_ID)
    value = dict(case_id=CASE_ID, source_revision=c['revision'], observed_at=now(),
        source_validation='verified', messages=messages or [], documents=[], gaps=[],
        live=dict(case_id=CASE_ID, owner_name='Example Engineer', is_closed=False, last_updated_ts=c['updated_at']))
    value.update(changes)
    return value


def message(**changes):
    value = dict(id='source-1', body='Hello team,\n\nThank you for checking. Please confirm the release window when it is available.\n\nRegards',
        created_at=now(), author_email=EMAIL, author_name='Example Engineer', audience='engineering', provenance='structured_source', source='Jira')
    value.update(changes)
    return value


def complete_report():
    return analysis(mode='operational', drafts=[dict(kind=a, title=f'{a} {p} {v}', body=f'{a} {p} wording in {v} form.', variant=v, scenario=p, use_when='Only if the existing request is still unanswered at the agreed checkpoint.' if p=='follow_up' else '')
        for a in ('customer','engineering','internal') for p in ('update','follow_up') for v in ('detailed','concise','high_level')])


def test_coverage_requires_distinct_lengths_and_followup_conditions():
    report = complete_report().model_dump()
    assert coverage(report)['complete']
    report['drafts'][0]['body'] = report['drafts'][1]['body']
    assert not coverage(report)['complete']
    report = complete_report().model_dump()
    report['drafts'][3]['use_when'] = ''
    assert 'detailed' in coverage(report)['cells'][1]['missing']
    report['message_decisions'] = {'customer:follow_up': 'No pending customer request.'}
    assert coverage(report)['cells'][1]['state']=='incomplete'
    report['drafts'] = [d for d in report['drafts'] if d['kind']!='customer' or d['scenario']!='follow_up']
    assert coverage(report)['complete']
    assert coverage(report)['cells'][1]['state']=='not_needed'


def test_partial_drafts_are_visible_with_explicit_readiness(store):
    store.ingest(bundle())
    store.ingest_analysis(analysis(mode='operational'))
    c = store.case(CASE_ID)
    assert c['draft_count']==1 and c['desk_state']=='incomplete'
    assert not c['draft_coverage']['complete']
    store.ingest_analysis(complete_report())
    assert store.case(CASE_ID)['desk_state']=='ready'
    store.ingest(bundle(cases=[case(revision='new')]))
    c=store.case(CASE_ID)
    assert c['draft_count']==0 and c['analysis_stale']
    assert all(not d['is_current'] for d in c['drafts'])


def test_operational_gate_does_not_admit_diagnostics_or_outbound(tmp_path):
    for tool, args in [('Skill',{'skill':'support-agent:start'}), ('Agent',{'subagent_type':'support-agent:enrichment-runner'}), ('Bash',{'command':'date'}), ('mcp__databricks_v2__execute_parameterized_sql',{'statement':'SELECT 1'}), ('mcp__google__gmail_message_send',{}), ('mcp__support_agent__generate_case_summary',{})]:
        assert not permitted(tool,args,tmp_path,'operational')[0]
    assert permitted('Read',{'file_path':str(tmp_path/'context.json')},tmp_path,'operational')[0]
    assert not permitted('Write',{'file_path':str(tmp_path/'context.json')},tmp_path,'operational')[0]
    assert permitted('Write',{'file_path':str(tmp_path/'analysis.json')},tmp_path,'operational')[0]
    assert not permitted('Read',{'file_path':str(tmp_path.parent/'context.json')},tmp_path,'operational')[0]


def test_default_refresh_starts_drafts_before_independent_scores(store,monkeypatch):
    co=Coordinator(store); calls=[]
    def collect(*a):
        b=bundle();store.ingest(b);return b
    monkeypatch.setattr(co,'collect',collect)
    monkeypatch.setattr(co,'reconcile_workers',lambda:None)
    monkeypatch.setattr(co,'consolidate_due',lambda:(calls.append('operational') or [],[]))
    monkeypatch.setattr(co,'start_scores',lambda *a:calls.append('scores'))
    monkeypatch.setattr(co,'launch_case',lambda *a:pytest.fail('Routine refresh launched diagnostics'))
    result=co.refresh('manual-test')
    assert calls==['operational','scores']
    assert result['state']=='complete'


def test_inventory_only_collector_never_opens_analytics(tmp_path,monkeypatch):
    import collector
    servers=[]
    class Client:
        def __init__(self,name,*a):servers.append(name)
        def close(self):pass
    monkeypatch.setattr(collector,'MCP',Client)
    monkeypatch.setattr(collector,'salesforce_inventory',lambda *a,**k:[])
    result=collector.collect(dict(run_id='inventory-test',owner_email=EMAIL,owner_name='Example Engineer',inventory_only=True),tmp_path)
    assert servers==['salesforce'] and result['inventory_complete']
    assert result['evaluations']==[]


def test_score_checkpoint_cannot_rollback_live_inventory(store):
    store.ingest(bundle(cases=[case(revision='new')]))
    old=bundle(cases=[case()],evaluations=[evaluation()])
    store.ingest_scores(old)
    c=store.case(CASE_ID)
    assert c['revision']=='new' and c['active'] and c['evaluation']
    store.ingest_scores(old)
    assert len(store.case(CASE_ID)['history'])==1


def test_existing_job_is_preserved_and_retry_budget_is_bounded(store,monkeypatch):
    store.ingest(bundle()); co=Coordinator(store)
    store.worker(CASE_ID,dict(key=CASE_ID,role='case',state='working',job_id='existing'))
    assert co.launch_operational(store.case(CASE_ID),'new')['job_id']=='existing'
    calls=[]
    monkeypatch.setattr(co,'launch_operational',lambda c,r:calls.append(c['id']))
    assert co.consolidate_due()==([],[])
    store.worker(CASE_ID,dict(key=CASE_ID,role='case',mode='operational',state='failed',requested_at=store.case(CASE_ID)['observed_at'],source_revision='revision-1',attempts=3))
    assert co.consolidate_due()==([],[])
    assert not calls
    store.ingest(bundle(cases=[case(revision='new')]))
    co.consolidate_due()
    assert calls==[CASE_ID]


def test_only_one_incomplete_coverage_repair_per_window(store,monkeypatch):
    store.ingest(bundle());store.ingest_analysis(analysis(mode='operational'))
    co=Coordinator(store);calls=[]
    worker=dict(key=CASE_ID,role='case',mode='operational',state='complete',requested_at=store.case(CASE_ID)['observed_at'],source_revision='revision-1',attempts=2,repair_count=1)
    store.worker(CASE_ID,worker)
    monkeypatch.setattr(co,'launch_operational',lambda c,r:calls.append(c['id']))
    co.consolidate_due();assert not calls
    worker['repair_count']=0;store.worker(CASE_ID,worker)
    co.consolidate_due();assert calls==[CASE_ID]


def test_locked_score_export_does_not_block_case_harvest(store,monkeypatch):
    co=Coordinator(store);calls=[]
    store.worker('score-collector',dict(key='score-collector',role='scores',state='working'))
    store.worker(CASE_ID,dict(key=CASE_ID,role='case',state='working'))
    monkeypatch.setattr(co,'harvest_case',lambda k:calls.append(k))
    with run_lock(store.settings.data_dir/'scores-dispatch.lock'):
        co.harvest()
    assert calls==[CASE_ID]


@pytest.mark.parametrize('changes',[{'source_validation':'unverified'},{'source_revision':'old'}, {'live':dict(case_id=CASE_ID,is_closed=True,owner_name='Example Engineer',last_updated_ts='2026-11-06T10:00:00Z')}, {'live':dict(case_id=CASE_ID,is_closed=False,owner_name='Someone else',last_updated_ts='2026-11-06T10:00:00Z')}])
def test_context_requires_current_owner_lifecycle_revision(store,changes):
    store.ingest(bundle())
    with pytest.raises(ValueError):store.ingest_context(context(store,[message()],**changes))
    assert store.writing_style()['audiences']['engineering']['sample_count']==0


def test_observed_own_sent_message_is_idempotent_and_audience_specific(store):
    store.ingest(bundle())
    sent=message(created_at='2026-11-06T11:00:00Z')
    value=context(store,[sent,message(id='other',author_email='other@example.test'),message(id='summary',body='Privacy summary of a useful message; this is not verbatim prose.')])
    store.ingest_context(value);store.ingest_context(value)
    detail=store.case(CASE_ID)
    assert len([a for a in detail['actions'] if a['state']=='done'])==1
    assert detail['actions'][0]['completed_at']=='2026-11-06T11:00:00+00:00'
    style=store.writing_style()
    assert style['audiences']['engineering']['sample_count']==1
    assert style['audiences']['customer']['sample_count']==0
    assert sent['body'] not in json.dumps(store.writing_style(for_worker=True))
    store.forget_writing_example(style['examples'][0]['id'])
    store.ingest_context(value)
    assert store.writing_style()['audiences']['engineering']['sample_count']==0


def test_post_matches_edited_draft_and_completes_only_communication_action(store):
    store.ingest(bundle())
    text='Hello team, please confirm the release window for the documented change. We need the owner and next checkpoint before updating the customer.'
    a=analysis(mode='operational',drafts=[dict(kind='engineering',title='Release check',body=text,completes_actions=['Send release question','Deploy the change'])],next_actions=[dict(title='Send release question',owner='Me'),dict(title='Deploy the change',owner='Engineering')])
    store.ingest_analysis(a)
    sent=message(body=text.replace('Hello team','Hi team'),created_at=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat())
    store.ingest_context(context(store,[sent]))
    detail=store.case(CASE_ID)
    assert detail['drafts'][0]['state']=='manually_sent'
    states={a['title']:a['state'] for a in detail['actions']}
    assert states['Send release question']=='done'
    assert states['Deploy the change']=='proposed'
    assert store.writing_style()['audiences']['engineering']['sample_count']==1
    assert store.learning_outcomes()['recorded_actions']==1


def test_exact_generated_text_is_not_style_training_but_saved_human_edit_is(store):
    store.ingest(bundle());store.ingest_analysis(analysis())
    draft=store.case(CASE_ID)['drafts'][0]
    sent_time=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
    store.ingest_context(context(store,[message(body=draft['body'],audience='customer',created_at=sent_time)]))
    assert store.writing_style()['audiences']['customer']['sample_count']==0
    store.patch_draft(draft['id'],DraftPatch(body=draft['body']+' Thank you for your help with this validation.'))
    draft=store.case(CASE_ID)['drafts'][0]
    store.ingest_context(context(store,[message(id='edited',body=draft['body'],audience='customer',created_at=sent_time)]))
    assert store.writing_style()['audiences']['customer']['sample_count']==1


def test_observation_reconciles_existing_manual_action_without_double_counting(store):
    store.ingest(bundle());store.ingest_analysis(analysis())
    draft=store.case(CASE_ID)['drafts'][0]
    store.patch_draft(draft['id'],DraftPatch(state='manually_sent'))
    sent=message(body=draft['body'],audience='customer',created_at=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat())
    store.ingest_context(context(store,[sent]))
    assert len([a for a in store.case(CASE_ID)['actions'] if a['state']=='done'])==1
    assert store.learning_outcomes()['recorded_actions']==1


def test_outcomes_require_pre_action_baseline_and_comparable_later_coverage(store):
    from workbench.models import ActionInput
    baseline=evaluation(model='model-a',rubric='rubric-a')
    retrospective=evaluation(result_id='retro',model='model-a',rubric='rubric-a',produced_at='2026-11-06T12:30:00Z',covered_at='2026-11-06T10:15:00Z')
    later=evaluation(result_id='later',model='model-a',rubric='rubric-a',produced_at='2026-11-06T13:00:00Z',covered_at='2026-11-06T12:00:00Z',scores={'ai_sat':9})
    store.ingest(bundle(evaluations=[baseline,retrospective,later]))
    identifier=store.add_action(CASE_ID,ActionInput(title='Record verified follow-up'))
    store.complete_action(identifier,'done')
    with store.connect() as db:db.execute('UPDATE actions SET completed_at=? WHERE id=?',('2026-11-06T11:00:00+00:00',identifier))
    outcome=store.case(CASE_ID)['outcomes'][0]
    assert outcome['before']['result_id']=='result-1' and outcome['after']['result_id']=='later'
    assert outcome['deltas']['ai_sat']==1
    observations=store.learning_outcomes()
    assert observations['comparable_outcomes']==1
    assert observations['observations'][0]['confidence']=='limited'
    assert 'Association only' in observations['observations'][0]['guidance']
    assert not store.lessons()


def test_source_collection_keeps_privacy_summary_out_of_confirmed_messages(tmp_path,monkeypatch):
    import desk_context
    class Client:
        def __init__(self,*a,**k):pass
        def close(self):pass
        def call(self,name,args):
            if name=='get_support_case':return dict(case_id=CASE_ID,owner_name='Example Engineer',is_closed=False,last_updated_ts='2026-11-06T10:00:00Z')
            return {'comments':[dict(comment_id='private',comment_body='Privacy summary: engineer requested release timing.',created_ts='2026-11-06T09:00:00Z',created_by_email=EMAIL),dict(comment_id='exact',comment_body='Please confirm the release owner and next checkpoint.',created_ts='2026-11-06T09:00:00Z',created_by_email=EMAIL)]}
    monkeypatch.setattr(desk_context,'MCP',Client)
    result=desk_context.collect_context(dict(case_id=CASE_ID,source_revision='revision-1',case_updated_at='2026-11-06T10:00:00+00:00',owner_name='Example Engineer'),tmp_path)
    assert result['source_validation']=='verified'
    assert [m['id'] for m in result['messages']]==['exact']
    assert result['documents'] and result['gaps']


def test_paused_learning_still_tracks_sent_actions_without_teaching_style(store):
    store.ingest(bundle());store.set_kv('writing_preferences',dict(learning_enabled=False,preferences={}))
    store.ingest_context(context(store,[message()]))
    assert store.learning_outcomes()['recorded_actions']==1
    assert store.writing_style()['audiences']['engineering']['sample_count']==0


def test_changed_final_source_requests_inventory_refresh(store,monkeypatch):
    store.ingest(bundle());co=Coordinator(store)
    store.worker(CASE_ID,dict(key=CASE_ID,role='case',mode='operational',state='working',case_id=CASE_ID,job_id='current',job_dir='~/job',source_revision='revision-1',run_id='test',host='arclet_example-worker.ssh',started_at=now(),context_imported=True))
    status=dict(job_id='current',case_id=CASE_ID,state='failed',source_validation='changed',error='Live case changed')
    monkeypatch.setattr(co,'ssh',lambda h,c,**k:json.dumps(status).encode() if c.endswith('/status.json') else b'')
    co.harvest_case(CASE_ID)
    assert store.kv('needs_inventory_refresh')
    assert store.worker(CASE_ID)['state']=='failed'


def test_failed_diagnostics_cooldown_does_not_delay_operational_fallback(store,monkeypatch):
    store.ingest(bundle());co=Coordinator(store);calls=[]
    store.worker(CASE_ID,dict(key=CASE_ID,role='case',state='failed',retry_after=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()))
    monkeypatch.setattr(co,'launch_operational',lambda c,r:calls.append(c['id']))
    co.consolidate_due()
    assert calls==[CASE_ID]


def test_missing_comments_narrow_brief_instead_of_blocking_all_drafting(tmp_path,monkeypatch):
    import desk_context
    class Client:
        def __init__(self,*a,**k):pass
        def close(self):pass
        def call(self,name,args):
            if name=='get_support_case':return dict(case_id=CASE_ID,owner_name='Example Engineer',is_closed=False,last_updated_ts='2026-11-06T10:00:00Z')
            raise TimeoutError('Comments unavailable')
    monkeypatch.setattr(desk_context,'MCP',Client)
    result=desk_context.collect_context(dict(case_id=CASE_ID,source_revision='revision-1',case_updated_at='2026-11-06T10:00:00+00:00',owner_name='Example Engineer'),tmp_path)
    assert result['source_validation']=='verified' and not result['messages']
    assert 'Latest comments unavailable' in result['gaps'][0]


def test_engineering_style_attribution_requires_explicit_structured_email():
    from desk_context import jira_messages
    value={'fields':{'comment':{'comments':[{'id':'1','body':'Please confirm the release owner and next checkpoint.','created':'2026-11-06T10:00:00Z','author':{'emailAddress':EMAIL}}, {'id':'2','body':'Another comment','created':'2026-11-06T10:00:00Z','author':{'accountId':'opaque-id'}}]}}}
    messages=jira_messages('ES-123',value)
    assert len(messages)==1 and messages[0]['audience']=='engineering'
    assert messages[0]['author_email']==EMAIL


def test_ai_investigation_document_is_observed_but_not_style_training(store):
    store.ingest(bundle())
    store.ingest_context(context(store,[message(body='This report was generated using AI and is not validated. Please use it as reference only.')]))
    assert store.learning_outcomes()['recorded_actions']==1
    assert store.writing_style()['audiences']['engineering']['sample_count']==0


def test_operational_runner_has_no_global_mcp_startup_and_supplies_evidence_on_stdin(tmp_path,monkeypatch):
    import worker_runner, desk_context, mcp_client
    job=dict(job_id='job-test',mode='operational',case_id=CASE_ID,case_number='00000001',source_revision='revision-1',case_updated_at='2026-11-06T10:00:00+00:00',owner_name='Example Engineer',run_id='run-test',instructions='Operational instructions',prompt='Prepare messages',schema={},timeout=180)
    path=tmp_path/'job.json';path.write_text(json.dumps(job));calls=[]
    monkeypatch.setattr(desk_context,'collect_context',lambda *a:dict(source_validation='verified',messages=[{'body':'private source prose'}]))
    class Process:
        returncode=0
        def __init__(self,args,**kwargs):
            calls.append(args)
            kwargs['stdout'].write(json.dumps({'structured_output':dict(case_id=CASE_ID,source_revision='revision-1',summary='Current operational summary')}).encode())
        def communicate(self,body,timeout):calls.append(body.decode());assert timeout==180
    class Client:
        def __init__(self,*a,**k):pass
        def call(self,*a):return dict(case_id=CASE_ID,last_updated_ts=job['case_updated_at'],owner_name=job['owner_name'],is_closed=False)
        def close(self):pass
    monkeypatch.setattr(worker_runner.subprocess,'Popen',Process)
    monkeypatch.setattr(mcp_client,'MCP',Client)
    worker_runner.run(path)
    assert '--strict-mcp-config' in calls[0] and '--disable-slash-commands' in calls[0]
    assert 'private source prose' not in ' '.join(calls[0])
    assert 'private source prose' in calls[1]
    assert json.loads((tmp_path/'status.json').read_text())['source_validation']=='verified'


def test_model_retry_reuses_dated_context_only_after_fresh_live_validation(tmp_path,monkeypatch):
    import desk_context
    calls=[]
    class Client:
        def __init__(self,*a,**k):pass
        def close(self):pass
        def call(self,name,args):
            calls.append(name)
            assert name=='get_support_case'
            return dict(case_id=CASE_ID,owner_name='Example Engineer',is_closed=False,last_updated_ts='2026-11-06T10:00:00Z')
    monkeypatch.setattr(desk_context,'MCP',Client)
    collected=now()
    job=dict(case_id=CASE_ID,source_revision='revision-1',case_updated_at='2026-11-06T10:00:00+00:00',owner_name='Example Engineer',cached_context=dict(case_id=CASE_ID,source_revision='revision-1',source_validation='verified',observed_at=collected,messages=[],documents=[],gaps=[]))
    result=desk_context.collect_context(job,tmp_path)
    assert calls==['get_support_case'] and result['reused']
    assert result['observed_at']==collected and 'dated' in result['gaps'][0]
