import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from conftest import CASE_ID, EMAIL, analysis, bundle, case
from workbench.api import create_app
from workbench.models import DraftInput, DraftPatch, WritingExample, WritingObservation
from workbench.store import draft_key


def test_variants_have_distinct_identity_and_preserve_edits(store):
    store.ingest(bundle())
    body = 'Please confirm whether the documented workaround resolved the issue.'
    legacy = hashlib.sha256(f'{CASE_ID}:revision-1:customer:{body}'.encode()).hexdigest()
    assert draft_key(CASE_ID, 'revision-1', 'customer', body) == legacy
    drafts = [DraftInput(kind=kind, variant=v, scenario=s, title=f'{kind} {v} {s}', body=body,
                         use_when='After a pending request' if s == 'follow_up' else '')
              for kind in ('customer', 'engineering', 'internal')
              for v in ('detailed', 'concise', 'high_level') for s in ('update', 'follow_up')]
    store.ingest_analysis(analysis(drafts=drafts))
    current = store.case(CASE_ID)['drafts']
    assert len({d['id'] for d in current}) == 18
    edited = current[0]
    store.patch_draft(edited['id'], DraftPatch(body='My own clarified wording for the next validation step.'))
    store.ingest_analysis(analysis(drafts=[]))
    current = store.case(CASE_ID)['drafts']
    assert next(d for d in current if d['id'] == edited['id'])['state'] == 'draft'
    assert sum(d['state'] == 'superseded' for d in current) == 17
    store.ingest(bundle(cases=[case(revision='new-revision')]))
    assert all(d['state'] == 'superseded' for d in store.case(CASE_ID)['drafts'])


def test_learns_only_confirmed_own_edits_and_separates_audiences(store):
    store.ingest(bundle()); store.ingest_analysis(analysis())
    draft = store.case(CASE_ID)['drafts'][0]
    for state in ('reviewed', 'copied', 'manually_sent'):
        store.patch_draft(draft['id'], DraftPatch(state=state))
    assert store.writing_style()['audiences']['customer']['sample_count'] == 0
    with pytest.raises(ValueError, match='own edits'):
        store.patch_draft(draft['id'], DraftPatch(learn_style=True))
    edited = 'Hi there,\n\nThank you for checking. Please confirm the next test result.\n\nBest regards,\nEngineer'
    store.patch_draft(draft['id'], DraftPatch(body=edited, learn_style=True))
    store.patch_draft(draft['id'], DraftPatch(body=edited + '\nAdditional clarification.', state='manually_sent'))
    profile = store.writing_style()
    assert profile['audiences']['customer']['sample_count'] == 1
    assert profile['audiences']['engineering']['sample_count'] == 0
    assert profile['audiences']['internal']['sample_count'] == 0
    worker = json.dumps(store.writing_style(for_worker=True))
    assert 'Additional clarification' not in worker and 'there' not in worker
    assert 'best regards' in worker
    store.forget_writing_example(profile['examples'][0]['id'])
    assert store.writing_style()['audiences']['customer']['sample_count'] == 0


def test_pause_feedback_and_blank_example_boundaries(store):
    store.ingest(bundle()); store.ingest_analysis(analysis(drafts=[DraftInput(kind='feedback', title='Tool observation', body='A sufficiently long factual observation about a tool result.')]))
    draft = store.case(CASE_ID)['drafts'][0]
    with pytest.raises(ValueError, match='communication draft'):
        store.patch_draft(draft['id'], DraftPatch(body=draft['body']+' Edit.', learn_style=True))
    store.set_kv('writing_preferences', {'learning_enabled': False, 'preferences': {'customer': 'Use short paragraphs'}})
    with pytest.raises(ValueError, match='paused'):
        store.add_writing_example(WritingExample(audience='customer', body='Hi there, this is a confirmed example of my own sent message.'))
    worker = store.writing_style(for_worker=True)
    assert worker['audiences']['customer']['preferences'] == 'Use short paragraphs'
    assert not worker['audiences']['customer']['observations']


def test_attributed_history_is_tentative_bounded_and_forgettable(store):
    store.ingest(bundle())
    own = WritingObservation(audience='customer', author_email=EMAIL, source_message_ids=['message-1'], greeting='hi', good_day=True, structure=['numbered_issues'])
    other = own.model_copy(update={'author_email': 'someoneelse@example.test', 'audience': 'engineering'})
    store.ingest_analysis(analysis(writing_observations=[own, other]))
    store.ingest_analysis(analysis(writing_observations=[own]))
    profile = store.writing_style()
    assert profile['audiences']['customer']['sample_count'] == 0
    assert profile['audiences']['customer']['historical_source_count'] == 1
    assert profile['audiences']['engineering']['historical_source_count'] == 0
    assert 'source_message_ids' not in json.dumps(store.writing_style(for_worker=True))
    store.forget_writing_example(profile['examples'][0]['id'])
    store.ingest_analysis(analysis(writing_observations=[own]))
    assert not store.writing_style()['examples']
    store.set_kv('writing_preferences', {'learning_enabled': False, 'preferences': {}})
    store.ingest_analysis(analysis(writing_observations=[own.model_copy(update={'source_message_ids': ['message-2']})]))
    assert not store.writing_style()['examples']


def test_writing_api_is_private_and_write_guarded(store):
    app = create_app(store.settings, enable_recovery=False)
    with TestClient(app) as client:
        example = dict(audience='internal', body='Engineering owner requested. Please confirm who can review the reproducible issue.', source_label='My own note')
        assert client.post('/api/writing-style/examples', json=example).status_code == 403
        token = client.get('/api/session').json()['token']
        headers = {'X-Workbench-Token': token, 'Origin': 'http://127.0.0.1:4318'}
        r = client.post('/api/writing-style/examples', headers=headers, json=example)
        assert r.status_code == 201
        identifier = r.json()['id']
        assert client.get('/api/writing-style').json()['audiences']['internal']['sample_count'] == 1
        assert client.patch('/api/writing-style', headers=headers, json={'learning_enabled': False, 'preferences': {}}).status_code == 200
        assert client.post('/api/writing-style/examples', headers=headers, json=example).status_code == 409
        assert client.delete('/api/writing-style/examples/'+identifier, headers={**headers, 'Origin':'https://external.example'}).status_code == 403
        assert client.delete('/api/writing-style/examples/'+identifier, headers=headers).status_code == 200


def test_worker_gets_options_and_structural_style_only(store, monkeypatch):
    from workbench.coordinator import Coordinator
    store.ingest(bundle())
    store.add_writing_example(WritingExample(audience='customer', body='Hi CustomerSecretName,\n\nThank you. Private incident identifier Secret123.\n\nBest regards,\nEngineer'))
    co = Coordinator(store)
    monkeypatch.setattr(co, 'ensure_worker', lambda *a: {'host':'arclet_test-worker.ssh'})
    monkeypatch.setattr(co, 'install_remote', lambda *a: None)
    jobs = []
    monkeypatch.setattr(co, 'ssh', lambda host, command, data=None, **kw: jobs.append(json.loads(data)) if data else None)
    monkeypatch.setattr(co, 'start_terminal', lambda worker,*a: worker)
    co.launch_case(store.case(CASE_ID), 'draft-options-run')
    prompt = jobs[0]['instructions']
    assert 'Secret123' not in prompt and 'CustomerSecretName' not in prompt
    assert 'high_level' in prompt and 'follow_up' in prompt
    assert 'optional reference' in prompt and 'engineering and internal separate' in prompt
    assert jobs[0]['prompt'] == '/support-agent:start 00000001'
