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


# Stock-analysis unit tests must not download institutional data or write to
# the user's live database. Integration tests inject their isolated service.
import pytest


@pytest.fixture(autouse=True)
def isolate_institutional_service(monkeypatch):
    from app.institutional_flow import integration
    def unavailable():
        raise RuntimeError('Use an injected institutional service in tests')
    monkeypatch.setattr(integration, 'InstitutionalFlowService', unavailable)
