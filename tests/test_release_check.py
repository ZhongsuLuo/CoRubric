from scripts.release_check import contains_secret, private_path, SECRET_ASSIGNMENT


def test_private_paths_and_templates():
    for name in ("history/README.md", "backend/.env", "frontend/.env.production",
                 "backend/ocr/config.local.toml", "backend/data/report.pdf", "key.pem"):
        assert private_path(name)
    for name in ("backend/.env.example", "backend/ocr/config.example.toml", "backend/libs/config.py"):
        assert not private_path(name)


def test_known_secret_and_key_pattern_detection():
    assert contains_secret(b"value=local-sensitive-value", {b"local-sensitive-value"})
    assert contains_secret(b"prefix=" + b"sk-" + b"a" * 30, set())
    assert not contains_secret(b"BAILIAN_API_KEY=", set())


def test_empty_template_does_not_consume_next_variable():
    assert SECRET_ASSIGNMENT.findall("API_KEY=\nBASE_URL=https://example.test\n") == []
    assert SECRET_ASSIGNMENT.findall('api_key = ""\naccess_key_id = ""\n') == []
