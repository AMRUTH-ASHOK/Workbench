from fastapi.testclient import TestClient
from workbench.api import create_app
from conftest import CASE_ID, analysis, bundle


def test_loopback_boundary_and_no_external_action_endpoints(store):
    app=create_app(store.settings,enable_recovery=False)
    app.state.store.ingest(bundle())
    app.state.store.ingest_analysis(analysis())
    with TestClient(app) as client:
        assert client.get('/api/health').status_code==200
        assert client.get('/api/cases',headers={'Host':'attacker.example'}).status_code==403
        assert client.post('/api/refresh',json={}).status_code==403
        token=client.get('/api/session').json()['token']
        headers={'X-Workbench-Token':token,'Origin':'http://127.0.0.1:4318'}
        draft=client.get('/api/cases/'+CASE_ID).json()['drafts'][0]
        r=client.patch('/api/drafts/'+draft['id'],headers=headers,json={'body':'Reviewed local draft','state':'reviewed'})
        assert r.status_code==200
        assert r.json()['state']=='reviewed'
        assert client.post('/api/send',headers=headers,json={}).status_code==404
        assert client.post('/api/feedback/submit',headers=headers,json={}).status_code==404
        assert client.get('/api/cases').headers['cache-control']=='no-store'


def test_cross_origin_requests_cannot_change_drafts(store):
    app=create_app(store.settings,enable_recovery=False)
    app.state.store.ingest(bundle());app.state.store.ingest_analysis(analysis())
    with TestClient(app) as client:
        token=client.get('/api/session').json()['token']
        draft=client.get('/api/cases/'+CASE_ID).json()['drafts'][0]
        response=client.patch('/api/drafts/'+draft['id'],headers={'X-Workbench-Token':token,'Origin':'https://other.example'},json={'body':'changed'})
        assert response.status_code==403
        assert app.state.store.case(CASE_ID)['drafts'][0]['body']!= 'changed'
