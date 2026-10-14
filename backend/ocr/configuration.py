"""Public OCR settings with optional server-only credential overrides."""
import os
import tomllib


def load_config(path):
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    for section, field, variable in (
        ("bailian", "api_key", "GRADING_OCR_API_KEY"),
        ("docmind", "access_key_id", "DOCMIND_ACCESS_KEY_ID"),
        ("docmind", "access_key_secret", "DOCMIND_ACCESS_KEY_SECRET"),
        ("docmind", "security_token", "DOCMIND_SECURITY_TOKEN"),
    ):
        value = os.getenv(variable)
        if value:
            config[section][field] = value
    return config
