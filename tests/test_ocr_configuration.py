from pathlib import Path

from backend.ocr.configuration import load_config


def test_credential_overrides_do_not_modify_public_template(monkeypatch):
    template = Path(__file__).resolve().parents[1] / "backend/ocr/config.example.toml"
    original = template.read_bytes()
    monkeypatch.setenv("GRADING_OCR_API_KEY", "test-ocr-only")
    monkeypatch.setenv("DOCMIND_ACCESS_KEY_ID", "test-docmind-id")
    monkeypatch.setenv("DOCMIND_ACCESS_KEY_SECRET", "test-docmind-secret")
    monkeypatch.setenv("DOCMIND_SECURITY_TOKEN", "test-session-token")
    config = load_config(template)
    assert config["bailian"]["api_key"] == "test-ocr-only"
    assert config["docmind"]["access_key_id"] == "test-docmind-id"
    assert config["docmind"]["access_key_secret"] == "test-docmind-secret"
    assert config["docmind"]["security_token"] == "test-session-token"
    assert template.read_bytes() == original


def test_blank_environment_preserves_local_configuration(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[bailian]\napi_key="local-test"\n[docmind]\naccess_key_id="local-id"\n')
    for key in ("GRADING_OCR_API_KEY", "DOCMIND_ACCESS_KEY_ID", "DOCMIND_ACCESS_KEY_SECRET", "DOCMIND_SECURITY_TOKEN"):
        monkeypatch.setenv(key, "")
    assert load_config(path)["bailian"]["api_key"] == "local-test"
