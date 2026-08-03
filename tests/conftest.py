import os
import tempfile
from pathlib import Path


def _configure_tempdir() -> None:
    base_dir = Path(__file__).resolve().parents[1] / ".pytest_tmp"
    base_dir.mkdir(parents=True, exist_ok=True)

    for env_name in ("TMPDIR", "TEMP", "TMP"):
        os.environ[env_name] = str(base_dir)

    tempfile.tempdir = str(base_dir)


_configure_tempdir()
