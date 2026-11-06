"""Isolated browser-test server. Uses invented cases and never starts collectors."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.api import create_app
from workbench.config import Settings
from conftest import analysis, bundle, case, evaluation
import uvicorn

with tempfile.TemporaryDirectory(prefix='workbench-ui-') as directory:
    app=create_app(Settings(data_dir=Path(directory),port=4319,owner_email='engineer@example.test',owner_name='Example Engineer'),enable_recovery=False)
    s=app.state.store
    first=evaluation(result_id='earlier',produced_at='2026-11-05T12:00:00+00:00',covered_at='2026-11-05T11:00:00+00:00',scores={'ai_sat':7,'responsiveness':7,'technical_depth':8,'communication':8,'escalation_judgment':None,'resolution_effectiveness':7})
    second=case('500000000000000002')
    second.account='Example unscored account'
    s.ingest(bundle(cases=[case(),second],evaluations=[first,evaluation()]))
    s.ingest_analysis(analysis())
    s.source('inventory','ready','Complete live inventory fixture')
    uvicorn.run(app,host='127.0.0.1',port=4319,log_level='warning')
