"""Os testes nunca leem a sessão pessoal nem escrevem relatórios no projeto."""
import pytest


@pytest.fixture(autouse=True)
def isolate_runtime_paths(monkeypatch, tmp_path):
    monkeypatch.setenv('RUN_REPORT_FILE', str(tmp_path / 'last_run.json'))
    monkeypatch.setenv('ML_STORAGE_STATE_FILE', str(tmp_path / 'ml_state.json'))
