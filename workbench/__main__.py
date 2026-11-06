import argparse
import json
import os
import plistlib
import subprocess
import sys
import shutil
from pathlib import Path
from .config import ROOT, get_settings
from .models import Analysis, Bundle
from .store import Store


def install_service(settings):
    # A normal app installation outside Desktop avoids macOS privacy prompts
    # when launchd opens the interpreter's pyvenv.cfg at login.
    installation = settings.data_dir / "app"
    installation.mkdir(exist_ok=True, mode=0o700)
    if installation.resolve() != ROOT.resolve():
        for name in ("workbench", "remote"):
            shutil.copytree(ROOT/name, installation/name, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT/"frontend/dist", installation/"frontend/dist", dirs_exist_ok=True)
        shutil.copy2(ROOT/"requirements.txt", installation/"requirements.txt")
    interpreter = installation/".venv/bin/python"
    if not interpreter.exists():
        subprocess.run([sys._base_executable, "-m", "venv", str(installation/".venv")], check=True)
    requirement_hash = __import__("hashlib").sha256((installation/"requirements.txt").read_bytes()).hexdigest()
    marker = installation/".dependencies"
    if not marker.exists() or marker.read_text() != requirement_hash:
        subprocess.run([str(interpreter), "-m", "pip", "install", "-r", str(installation/"requirements.txt")], check=True)
        marker.write_text(requirement_hash)
    target = Path.home()/"Library/LaunchAgents/com.amruth.workbench.plist"
    target.parent.mkdir(exist_ok=True, parents=True)
    spec = dict(Label="com.amruth.workbench", ProgramArguments=[str(interpreter), "-m", "workbench", "serve"], WorkingDirectory=str(installation), RunAtLoad=True, KeepAlive=True, ThrottleInterval=15, EnvironmentVariables={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "WORKBENCH_DATA_DIR": str(settings.data_dir), "WORKBENCH_PORT": str(settings.port)}, StandardOutPath=str(settings.data_dir/"logs/server.log"), StandardErrorPath=str(settings.data_dir/"logs/server-error.log"))
    target.write_bytes(plistlib.dumps(spec))
    target.chmod(0o600)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(target)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    return str(target)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Workbench local support desk")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    refresh = commands.add_parser("refresh")
    refresh.add_argument("--trigger", default="manual")
    refresh.add_argument("--closed-review", action="store_true")
    refresh.add_argument("--collect-only", action="store_true")
    commands.add_parser("status")
    commands.add_parser("doctor")
    commands.add_parser("harvest")
    commands.add_parser("install-service")
    ingest = commands.add_parser("import-bundle")
    ingest.add_argument("path")
    analysis = commands.add_parser("import-analysis")
    analysis.add_argument("path")
    launch = commands.add_parser("analyze")
    launch.add_argument("case_number")
    args = parser.parse_args()
    settings = get_settings()
    store = Store(settings)
    if args.command == "serve":
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port, log_level="warning", access_log=False)
        return
    if args.command == "status":
        print(json.dumps(store.overview(), indent=2))
    elif args.command == "install-service":
        print(install_service(settings))
    elif args.command == "import-bundle":
        changed = store.ingest(Bundle.model_validate_json(Path(args.path).read_bytes()))
        print(json.dumps({"changed_cases": len(changed)}))
    elif args.command == "import-analysis":
        store.ingest_analysis(Analysis.model_validate_json(Path(args.path).read_bytes()))
        print("Analysis imported")
    else:
        from .coordinator import Coordinator
        coordinator = Coordinator(store)
        if args.command == "refresh":
            result = coordinator.refresh(args.trigger, args.closed_review, not args.collect_only)
            print(json.dumps(result))
            if result["state"] == "failed":
                sys.exit(1)
        elif args.command == "harvest":
            coordinator.harvest()
            print("Worker results checked")
        elif args.command == "analyze":
            case = next((c for c in store.cases() if c["number"] == args.case_number), None)
            if not case:
                parser.error("Case is not in the active assigned queue; collect first")
            existing = store.worker(case["id"])
            if existing and existing.get("state") in {"working", "starting"}:
                print(json.dumps({"state": "already_running"}))
            else:
                print(json.dumps(coordinator.launch_case(case, "manual-" + __import__("uuid").uuid4().hex)))
        elif args.command == "doctor":
            print(json.dumps({"data_dir": str(settings.data_dir), "database": settings.db_path.exists(), "arcaterm": coordinator.arca("status"), "frontend": (ROOT/"frontend/dist/index.html").exists()}, indent=2))


if __name__ == "__main__":
    main()
