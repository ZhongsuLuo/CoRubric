import json
import socket

from fastapi.testclient import TestClient

from scripts.case_study_replay import create_app


def test_replay_returns_recorded_responses_and_never_falls_back(tmp_path, monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError("Replay attempted an outbound connection")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    expected = {"status": "ok", "data": {"item": {"criteria": [{"criteria_id": "real-id"}]}}}
    synthesis = {"status": "ok", "data": {"summary": "Recorded model output", "model_id": "recorded-model"}}
    for case, stage in [("case 1", "c1_00"), ("case 1", "c1_01"), ("case 2", "c2_00")]:
        folder = tmp_path / "stages" / stage
        folder.mkdir(parents=True)
        (folder / "stage.json").write_text(json.dumps({"stage": stage, "case": case, "reviews": ["a", "b", "c"]}))
        (folder / "reads.json").write_text(json.dumps({"/criteria/测试.json": expected}))
        (folder / "synthesis_2.json").write_text(json.dumps(synthesis))
    files = tmp_path / "working/case"
    files.mkdir(parents=True)
    (files / "evidence.txt").write_text("Original evidence bytes")
    frontend = tmp_path / "frontend_dist"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text("Original frontend")
    (frontend / "corubric-icon.svg").write_text("Original icon bytes")
    (frontend / "assets/app.js").write_text("Original script bytes")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/").text == "Original frontend"
        assert client.get("/corubric-icon.svg").text == "Original icon bytes"
        assert client.get("/assets/app.js").text == "Original script bytes"
        response = client.get("/api/criteria/%E6%B5%8B%E8%AF%95.json")
        assert response.json() == expected
        assert response.headers["X-Case-Replay"] == "c1_00"
        assert client.post("/demo/select/c1_01", follow_redirects=False).status_code == 303
        assert client.get("/api/criteria/测试.json").headers["X-Case-Replay"] == "c1_01"
        assert client.post("/api/cases/case%202/cohort-synthesis", json={"criterion_id": "2"}).json() == synthesis
        assert client.get("/api/case-files/evidence.txt").text == "Original evidence bytes"
        assert client.post("/api/cases/case%202/cohort-synthesis", json={"criterion_id": "uncached"}).status_code == 409
        assert client.post("/api/cases/case%201/reports/test/grade", json={"criteria_score": 4}).status_code == 409
        assert len(client.get("/demo/status").json()["cache_misses"]) == 2
        assert client.post("/demo/select/nonexistent").status_code == 404
