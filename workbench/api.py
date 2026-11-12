import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .config import ROOT, get_settings
from .models import ActionInput, DraftPatch, LessonInput, Record, WritingPreferences, WritingExample
from .store import Store


class RefreshRequest(Record):
    closed_review: bool = False
    analyze: bool = True


class ActionPatch(Record):
    state: Literal["proposed", "done", "dismissed"]


class LessonPatch(Record):
    state: Literal["approved", "rejected", "candidate"]


def create_app(settings=None, enable_recovery=True):
    settings = settings or get_settings()
    store = Store(settings)
    refresh_tasks = set()

    def kick_refresh(closed=False, trigger="dashboard", analyze=True):
        from .coordinator import Coordinator
        task = asyncio.create_task(asyncio.to_thread(Coordinator(store).refresh, trigger, closed, analyze))
        refresh_tasks.add(task)
        task.add_done_callback(refresh_tasks.discard)

    @asynccontextmanager
    async def lifespan(app):
        recovery = None
        harvester = None
        if enable_recovery:
            async def watch_workers():
                from .coordinator import AlreadyRunning, Coordinator
                while True:
                    try:
                        await asyncio.to_thread(Coordinator(store).harvest)
                        if store.kv('runtime_ready',False):
                            await asyncio.to_thread(Coordinator(store).consolidate_due)
                    except AlreadyRunning:
                        pass
                    except Exception as exc:
                        store.source("worker_export", "unavailable", str(exc)[:1000])
                    await asyncio.sleep(20)
            harvester = asyncio.create_task(watch_workers())
            async def watch_resume():
                from .schedule import latest_window
                await asyncio.sleep(20)
                while True:
                    key = latest_window()
                    if store.kv('runtime_ready',False) and store.kv('needs_inventory_refresh',False) and not refresh_tasks:
                        kick_refresh(trigger='activity-change')
                    elif store.kv("runtime_ready", False) and key and store.kv("completed_window") != key and store.kv("last_attempt_window") != key:
                        # A single catch-up collection, even when several windows were missed.
                        kick_refresh(trigger="catch-up:" + key)
                    await asyncio.sleep(60)
            recovery = asyncio.create_task(watch_resume())
        yield
        if recovery:
            recovery.cancel()
        if harvester:
            harvester.cancel()
        for task in refresh_tasks:
            task.cancel()

    app = FastAPI(title="Workbench", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    token = secrets.token_urlsafe(32)
    app.state.token = token

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "Workbench is available on this Mac only"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = {f"http://127.0.0.1:{settings.port}", f"http://localhost:{settings.port}"}
            if origin not in expected or request.headers.get("x-workbench-token") != token:
                return JSONResponse({"detail": "Reload Workbench before making changes"}, status_code=403)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        return response

    @app.get("/api/session")
    def session():
        return {"token": token, "draft_only": True}

    @app.get("/api/overview")
    def overview():
        return store.overview()

    @app.get("/api/cases")
    def cases(scope: Literal["open", "closed", "all"] = "open"):
        return store.cases(scope)

    @app.get("/api/cases/{case_id}")
    def case(case_id: str):
        value = store.case(case_id)
        if value is None:
            raise HTTPException(404, "Case not found")
        return value

    @app.post("/api/refresh", status_code=202)
    async def refresh(body: RefreshRequest):
        if refresh_tasks:
            return {"state": "already_running"}
        kick_refresh(body.closed_review, analyze=body.analyze)
        return {"state": "queued"}

    @app.patch("/api/drafts/{draft_id}")
    def draft(draft_id: str, patch: DraftPatch):
        try:
            return store.patch_draft(draft_id, patch)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/cases/{case_id}/actions", status_code=201)
    def add_action(case_id: str, action: ActionInput):
        try:
            return {"id": store.add_action(case_id, action)}
        except KeyError as exc:
            raise HTTPException(404, str(exc))

    @app.patch("/api/actions/{action_id}")
    def action(action_id: str, body: ActionPatch):
        try:
            store.complete_action(action_id, body.state)
            return {"state": body.state}
        except KeyError as exc:
            raise HTTPException(404, str(exc))

    @app.get("/api/lessons")
    def lessons():
        return store.lessons()

    @app.get('/api/writing-style')
    def writing_style():
        return store.writing_style()

    @app.get('/api/learning-outcomes')
    def learning_outcomes():
        return store.learning_outcomes()

    @app.patch('/api/writing-style')
    def writing_preferences(body: WritingPreferences):
        store.set_kv('writing_preferences', body.model_dump())
        return store.writing_style()

    @app.post('/api/writing-style/examples', status_code=201)
    def writing_example(body: WritingExample):
        try:
            return {'id': store.add_writing_example(body)}
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.delete('/api/writing-style/examples/{identifier}')
    def forget_writing_example(identifier: str):
        try:
            store.forget_writing_example(identifier)
            return {'state': 'forgotten'}
        except KeyError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/api/lessons", status_code=201)
    def add_lesson(body: LessonInput):
        return {"id": store.add_lesson(body)}

    @app.patch("/api/lessons/{lesson_id}")
    def review_lesson(lesson_id: str, body: LessonPatch):
        try:
            store.review_lesson(lesson_id, body.state)
            return {"state": body.state}
        except KeyError as exc:
            raise HTTPException(404, str(exc))

    @app.get("/api/health")
    def health():
        return {"state": "ready", "version": "0.1.0"}

    dist = ROOT / "frontend/dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (dist / "index.html").exists():
            raise HTTPException(503, "Build the dashboard with npm --prefix frontend run build")
        return FileResponse(dist / "index.html")

    return app
