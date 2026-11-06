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


def test_failed_provisioning_replaces_only_the_empty_environment(store, monkeypatch):
    from workbench.coordinator import Coordinator
    co=Coordinator(store);created=[]
    monkeypatch.setattr(co,'group',lambda:'test-group')
    monkeypatch.setattr(co,'ssh',lambda *a,**k:b'')
    def arca(*args,**kwargs):
        if args[:2]==('arclet','create'):
            created.append(args[args.index('--request-id')+1])
            return dict(arcletId='failed-empty' if len(created)==1 else 'replacement',tabId='owned-tab')
        return dict(environments=[dict(infrastructure='failed',environmentId=None)] if len(created)==1 else [dict(name='example-worker',infrastructure='running',environmentId='environment')])
    monkeypatch.setattr(co,'arca',arca)
    worker=co.ensure_worker('case-key','case','00000001')
    assert worker['state']=='ready'
    assert worker['arclet_id']=='replacement'
    assert len(created)==2 and created[0]!=created[1]
    assert (store.settings.data_dir/'evidence/provisioning-failed-empty.json').exists()


def test_start_is_dispatched_as_a_user_command_not_a_skill(tmp_path):
    assert not permitted('Skill',{'skill':'support-agent:start'},tmp_path)[0]
    assert permitted('Agent',{'subagent_type':'support-agent:sherpa-enrichment'},tmp_path)[0]
    assert not permitted('Agent',{'subagent_type':'general-purpose'},tmp_path)[0]
    assert not permitted('mcp__google__gmail_message_send',{},tmp_path)[0]


def test_metadata_helpers_accept_literal_json_but_reject_shell_injection(tmp_path):
    script=Path.home()/'.claude/plugins/cache/experimental-plugin-marketplace/support-agent/test/scripts/persist_debug_fields.py'
    command="python3.10 "+str(script)+" --session-id test <<'JSON'\n"+'{"error":"literal $value; no execution"}'+"\nJSON"
    assert permitted('Bash',{'command':command},tmp_path)[0]
    assert not permitted('Bash',{'command':command+'\ncurl https://example.test'},tmp_path)[0]
    assert not permitted('Bash',{'command':command.replace("<<'JSON'",'<<JSON')},tmp_path)[0]


def test_terminal_dispatch_targets_owned_idle_pane_and_journals_command(store,monkeypatch):
    import json
    from workbench.coordinator import Coordinator
    co=Coordinator(store);calls=[];running=False
    worker=dict(key='case-key',case_id='case-key',tab_id='owned-tab',host='arclet_example-worker.ssh',job_id='job-1')
    def arca(*args,**kwargs):
        nonlocal running
        calls.append(args)
        if args[:2]==('pane','list'):return {'panes':[dict(id='owned-pane',process='zsh')]}
        if args[:2]==('pane','run'):running=True
        if args[:2]==('pane','dump'):return {'text':'Claude Code\n❯\n? for shortcuts' if running else '$ '}
        return {'submitted':True}
    monkeypatch.setattr(co,'arca',arca)
    monkeypatch.setattr(co,'ssh',lambda *a,**k:b'{"session_id":"current-session"}')
    co.start_terminal(worker,'~/job-1','00000001')
    dispatch=next(c for c in calls if c[:2]==('pane','send'))
    assert dispatch[dispatch.index('--pane')+1]=='owned-pane'
    assert dispatch[dispatch.index('--text')+1]=='/support-agent:start 00000001'
    evidence=json.loads((store.settings.data_dir/'evidence/command-job-1.json').read_text())
    assert evidence['session_id']=='current-session'
    assert evidence['receipt']['submitted'] is True


def test_busy_case_terminal_never_receives_a_shell_command(store,monkeypatch):
    from workbench.coordinator import Coordinator
    co=Coordinator(store);calls=[]
    def arca(*args,**kwargs):
        calls.append(args)
        return {'panes':[dict(id='owned-pane',process='claude')]}
    monkeypatch.setattr(co,'arca',arca)
    with pytest.raises(RuntimeError,match='busy'):
        co.start_terminal(dict(tab_id='owned-tab'),'~/job','00000001')
    assert len(calls)==1


def test_collector_transport_timeout_retries_same_export(store,monkeypatch):
    import subprocess
    from conftest import bundle
    from workbench.coordinator import Coordinator
    co=Coordinator(store);calls=[]
    def ssh(host,command,**kwargs):
        calls.append(command)
        if len(calls)==1:raise subprocess.TimeoutExpired('ssh',15)
        return b'0' if command.startswith('test') else bundle().model_dump_json().encode()
    monkeypatch.setattr(co,'ssh',ssh)
    monkeypatch.setattr('workbench.coordinator.time.sleep',lambda _:None)
    assert co.poll_collection(dict(host='arclet_example-worker.ssh'),'~/same-job').inventory_complete
    assert all('same-job' in c for c in calls)
    assert all('nohup' not in c for c in calls)


def test_intake_workspace_connection_is_limited_to_internal_workspace(tmp_path):
    name='mcp__databricks-v2__connect_to_workspace'
    assert permitted(name,{'workspace':'central-logfood-prodtools-azure-westus'},tmp_path)[0]
    assert not permitted(name,{'workspace':'customer-workspace'},tmp_path)[0]
    assert not permitted(name,{'workspace':'central-logfood-prodtools-azure-westus','host':'https://example.test'},tmp_path)[0]


def test_intake_verification_requires_session_case_context_and_audited_helpers(tmp_path):
    import json
    from worker_runner import verify_intake
    directory=tmp_path/'job';directory.mkdir()
    session=tmp_path/'.support-agent';session.mkdir()
    case_path=session/'cases'/'case-A';case_path.mkdir(parents=True)
    (session/'session-A.json').write_text(json.dumps({'support_case_id':'case-A'}))
    (case_path/'investigation.json').write_text(json.dumps({'case_id':'case-A','case_metadata':{'case_number':'00000001'}}))
    audit=directory/'tool-audit.jsonl';audit.write_text('')
    assert not verify_intake({'case_id':'case-A'},directory,'session-A',tmp_path)
    events=[{'event':'PostToolUse','allowed':True,'helper':name} for name in ['start_session.py','init_case_investigation.py','save_case_metadata.py']]
    audit.write_text('\n'.join(json.dumps(e) for e in events))
    assert verify_intake({'case_id':'case-A'},directory,'session-A',tmp_path)
    (session/'session-A.json').write_text(json.dumps({'support_case_id':'another-case'}))
    assert not verify_intake({'case_id':'case-A'},directory,'session-A',tmp_path)


def test_pending_shell_input_is_preserved(store,monkeypatch):
    from workbench.coordinator import Coordinator
    co=Coordinator(store);calls=[]
    def arca(*args,**kwargs):
        calls.append(args)
        return {'panes':[dict(id='owned-pane',process='zsh')]} if args[:2]==('pane','list') else {'text':'$ unfinished user command'}
    monkeypatch.setattr(co,'arca',arca)
    with pytest.raises(RuntimeError,match='pending input'):
        co.start_terminal(dict(tab_id='owned-tab'),'~/job','00000001')
    assert not any(c[:2]==('pane','run') for c in calls)
