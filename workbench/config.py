from dataclasses import dataclass
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    data_dir: Path
    port: int = 4318
    owner_email: str = "amruth.ashok@databricks.com"
    owner_name: str = "Amruth Ashoka"
    timezone: str = "Asia/Kolkata"
    arcaterm: str = str(Path.home() / "Applications/ArcaTerm.app/Contents/Resources/bin/arcaterm")

    @property
    def db_path(self):
        return self.data_dir / "workbench.sqlite3"

    def prepare(self):
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir.chmod(0o700)
        for name in ("evidence", "exports", "logs"):
            (self.data_dir / name).mkdir(exist_ok=True, mode=0o700)
        return self


def get_settings():
    return Settings(
        data_dir=Path(os.environ.get("WORKBENCH_DATA_DIR", str(Path.home() / "Library/Application Support/Workbench"))),
        port=int(os.environ.get("WORKBENCH_PORT", "4318")),
        owner_email=os.environ.get("WORKBENCH_OWNER_EMAIL", "amruth.ashok@databricks.com"),
        owner_name=os.environ.get("WORKBENCH_OWNER_NAME", "Amruth Ashoka"),
        arcaterm=os.environ.get("WORKBENCH_ARCATERM", str(Path.home() / "Applications/ArcaTerm.app/Contents/Resources/bin/arcaterm")),
    ).prepare()
