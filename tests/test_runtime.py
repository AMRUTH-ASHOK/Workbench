import sys
from pathlib import Path
from datetime import datetime
import pytest
from zoneinfo import ZoneInfo
from workbench.coordinator import AlreadyRunning, run_lock
from workbench.schedule import latest_window

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'remote'))
from worker_policy import permitted
from collector import stamp, score_parts


def test_run_locks_prevent_overlapping_refreshes(tmp_path):
    path=tmp_path/'run.lock'
    with run_lock(path):
        with pytest.raises(AlreadyRunning):
            with run_lock(path):
                pytest.fail('Second run acquired the same lock')
    with run_lock(path):
        pass


def test_schedule_uses_ist_weekdays_and_one_latest_window():
    def at(day,hour,minute): return datetime(2026,11,day,hour,minute,tzinfo=ZoneInfo('Asia/Kolkata'))
    assert latest_window(at(6,13,29)) is None
    assert latest_window(at(6,16,17))=='2026-11-06T16:15'
    assert latest_window(at(6,22,1))=='2026-11-06T22:00'
    assert latest_window(at(6,22,31)) is None
    assert latest_window(at(7,14,0)) is None


@pytest.mark.parametrize('tool,args',[
 ('mcp__google__gmail_message_send',{}),
 ('mcp__slack__slack_write_api_call',{}),
 ('mcp__jira__jira_write_api_call',{}),
 ('mcp__salesforce__execute_upsert',{}),
 ('mcp__support-agent__execute_static_debug_script',{}),
 ('mcp__support-agent__trigger_job',{}),
 ('mcp__databricks-v2__execute_parameterized_sql',{'statement':'WITH c AS (SELECT 1) INSERT INTO t SELECT * FROM c'}),
 ('Bash',{'command':'curl https://example.test -d customer-data'}),
 ('Bash',{'command':'python3 -c "print(1)"'}),
 ('Write',{'file_path':str(Path.home()/'.claude/settings.json')}),
])
def test_worker_boundary_denies_external_writes(tool,args,tmp_path):
    assert not permitted(tool,args,tmp_path)[0]


def test_worker_boundary_allows_useful_case_reads_and_drafting(tmp_path):
    assert permitted('StructuredOutput',{},tmp_path)[0]
    assert permitted('mcp__support-agent__list_support_case_comments',{},tmp_path)[0]
    assert permitted('mcp__databricks-v2__execute_parameterized_sql',{'statement':'SELECT * FROM t WHERE case_id=:case_id'},tmp_path)[0]
    assert permitted('Skill',{'skill':'support-agent:tool-epica-comms'},tmp_path)[0]
    assert permitted('Write',{'file_path':str(tmp_path/'analysis.json')},tmp_path)[0]


def test_salesforce_timestamp_and_nullable_dimensions():
    assert stamp('2026-11-04T23:16:46.000+0000')=='2026-11-04T23:16:46+00:00'
    scores,_=score_parts({'case_review':{'support_engineer':{'responsiveness':{'score':9,'reasoning':'Substantive reply'},'escalation_judgment':{'score':None}}},'overall_satisfaction':{'score':8}})
    assert scores['responsiveness']==9
    assert scores['escalation_judgment'] is None
    assert scores['ai_sat']==8


def test_arclet_creation_retry_reuses_persisted_request_identity(store, monkeypatch):
    from workbench.coordinator import Coordinator
    co=Coordinator(store)
    requests=[]
    monkeypatch.setattr(co,'group',lambda:'test-group')
    def unavailable(*args,**kwargs):
        requests.append(args[args.index('--request-id')+1])
        raise RuntimeError('Temporary ArcaTerm outage')
    monkeypatch.setattr(co,'arca',unavailable)
    for _ in range(2):
        with pytest.raises(RuntimeError,match='Temporary'):
            co.ensure_worker('case-worker','case','00000001')
    assert requests[0]==requests[1]
    import uuid
    assert uuid.UUID(requests[0]).version==7


def test_read_sql_can_contain_quoted_mutation_words(tmp_path):
    assert permitted('mcp__databricks-v2__execute_parameterized_sql',{'statement':"SELECT 'update; delete' AS description FROM t -- insert"},tmp_path)[0]


def test_transient_export_timeout_keeps_same_worker_pollable(store, monkeypatch):
    import subprocess
    from workbench.coordinator import Coordinator
    from workbench.models import now
    worker=dict(key='case-key',role='case',state='working',host='arclet_example-worker.ssh',job_dir='~/job',started_at=now())
    store.worker(worker['key'],worker)
    co=Coordinator(store)
    def timeout(*args,**kwargs):
        raise subprocess.TimeoutExpired('ssh',15)
    monkeypatch.setattr(co,'ssh',timeout)
    co.harvest()
    retained=store.worker(worker['key'])
    assert retained['state']=='working'
    assert retained['job_dir']==worker['job_dir']
    assert 'retrying the same job' in retained['error']


def test_same_case_launch_reuses_running_job(store):
    from workbench.coordinator import Coordinator
    from conftest import CASE_ID
    worker=dict(key=CASE_ID,role='case',state='working',job_id='existing-job')
    store.worker(CASE_ID,worker)
    assert Coordinator(store).launch_case({'id':CASE_ID},'new-run')['job_id']=='existing-job'


def test_runner_owns_run_identity_but_rejects_wrong_case_revision():
    from worker_runner import bind_manifest
    job=dict(run_id='new-run',case_id='case-A',source_revision='current')
    result=dict(run_id='previous-run',case_id='case-A',source_revision='current')
    assert bind_manifest(result,job)['run_id']=='new-run'
    with pytest.raises(ValueError,match='case revision'):
        bind_manifest(dict(result,source_revision='old'),job)
