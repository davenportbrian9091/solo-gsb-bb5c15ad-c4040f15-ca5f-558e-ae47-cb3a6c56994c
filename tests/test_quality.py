"""质量报告接口测试。"""
import time

from tests.conftest import make_word


def _submit_and_wait(client, words, page_width=1000, page_height=1400):
    resp = client.post("/api/tasks", json={
        "page_width": page_width,
        "page_height": page_height,
        "words": words,
    })
    assert resp.status_code == 201, resp.text
    task_id = resp.json()["task_id"]

    deadline = time.time() + 5.0
    while time.time() < deadline:
        body = client.get(f"/api/tasks/{task_id}").json()
        if body["status"] in ("done", "failed"):
            assert body["status"] == "done", body.get("error")
            return task_id
        time.sleep(0.02)
    raise AssertionError("task did not finish in time")


def _report(client, task_id):
    resp = client.post(f"/api/tasks/{task_id}/quality-report")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_clean_task_has_no_issues(client):
    words = [
        make_word(0, "alpha", 100, 100, 70, 30),
        make_word(1, "beta", 190, 100, 70, 30),
        make_word(2, "gamma", 280, 100, 70, 30),
        make_word(3, "delta", 370, 100, 70, 30),
        make_word(4, "second", 100, 170, 70, 30),
        make_word(5, "line", 190, 170, 70, 30),
    ]
    task_id = _submit_and_wait(client, words)
    rep = _report(client, task_id)
    assert rep["issues"] == []
    assert rep["summary"]["total_issues"] == 0
    assert rep["summary"]["scanned_words"] == 6
    assert rep["summary"]["scanned_lines"] == 2
    assert rep["rules_version"]


def test_low_confidence(client):
    words = [
        make_word(0, "ok", 100, 100, 70, 30, conf=0.95),
        make_word(1, "weak", 190, 100, 70, 30, conf=0.60),
        make_word(2, "bad", 280, 100, 70, 30, conf=0.30),
        make_word(3, "fine", 370, 100, 70, 30),  # 无置信度不应报告
    ]
    task_id = _submit_and_wait(client, words)
    rep = _report(client, task_id)
    kinds = [(i["word_id"], i["kind"], i["severity"]) for i in rep["issues"]]
    assert kinds == [
        (1, "low_confidence", "minor"),
        (2, "low_confidence", "major"),
    ]
    assert rep["summary"]["by_kind"]["low_confidence"] == 2
    assert rep["summary"]["words_with_issues"] == 2


def test_size_outlier(client):
    words = [
        make_word(0, "a", 100, 100, 70, 30),
        make_word(1, "b", 190, 100, 70, 30),
        make_word(2, "d", 280, 100, 70, 30),
        make_word(3, "big", 370, 100, 70, 60),  # 行尾，字高 2 倍
    ]
    task_id = _submit_and_wait(client, words)
    rep = _report(client, task_id)
    assert [i["word_id"] for i in rep["issues"]] == [3]
    issue = rep["issues"][0]
    assert issue["kind"] == "size_outlier"
    assert issue["severity"] == "major"
    assert issue["line_id"] == 0
    assert "中位数" in issue["reason"] and issue["suggestion"]


def test_overlap_flags_both_words(client):
    words = [
        make_word(0, "aa", 100, 100, 100, 30),
        make_word(1, "bb", 140, 100, 100, 30),  # 重叠 60/100
    ]
    task_id = _submit_and_wait(client, words)
    rep = _report(client, task_id)
    assert len(rep["issues"]) == 2
    assert {i["word_id"] for i in rep["issues"]} == {0, 1}
    assert all(i["kind"] == "overlap" and i["severity"] == "major"
               for i in rep["issues"])
    assert rep["summary"]["by_kind"]["overlap"] == 2


def test_gap_anomaly(client):
    words = [
        make_word(0, "a", 100, 100, 70, 30),
        make_word(1, "b", 190, 100, 70, 30),
        make_word(2, "c", 280, 100, 70, 30),
        make_word(3, "d", 405, 100, 70, 30),  # 间距 55，其余为 20
    ]
    task_id = _submit_and_wait(client, words)
    rep = _report(client, task_id)
    assert len(rep["issues"]) == 1
    issue = rep["issues"][0]
    assert issue["word_id"] == 2
    assert issue["kind"] == "gap_anomaly"
    assert issue["severity"] == "minor"


def test_issues_sorted_by_original_id(client):
    words = [
        make_word(9, "a", 100, 100, 70, 30, conf=0.2),
        make_word(2, "b", 190, 100, 70, 30, conf=0.2),
        make_word(1, "c", 280, 100, 70, 30, conf=0.2),
        make_word(0, "d", 370, 100, 70, 30, conf=0.2),
    ]
    task_id = _submit_and_wait(client, words)
    rep = _report(client, task_id)
    # 阅读顺序按位置，但 issues 必须按原始 id 排序
    assert [i["word_id"] for i in rep["issues"]] == [0, 1, 2, 9]


def test_report_is_readonly_and_deterministic(client):
    words = [
        make_word(3, "a", 100, 100, 70, 30, conf=0.1),
        make_word(1, "b", 190, 100, 70, 60, conf=0.1),
    ]
    task_id = _submit_and_wait(client, words)
    r1 = _report(client, task_id)
    r2 = _report(client, task_id)
    assert r1 == r2
    # 任务结果本身不受影响
    body = client.get(f"/api/tasks/{task_id}").json()
    assert body["status"] == "done"
    assert len(body["result"]["lines"]) == 1
    assert body["result"]["lines"][0]["words"][0]["id"] == 3


def test_unclassified_counted(client):
    words = [
        make_word(0, "normal", 100, 100, 70, 30),
        {"id": 1, "text": "", "confidence": 0.5,
         "quad": [{"x": 100, "y": 200}, {"x": 170, "y": 200},
                  {"x": 170, "y": 230}, {"x": 100, "y": 230}]},
    ]
    task_id = _submit_and_wait(client, words)
    rep = _report(client, task_id)
    assert rep["summary"]["unclassified_words"] == 1
    assert rep["summary"]["scanned_words"] == 1


def test_empty_task_report(client):
    task_id = _submit_and_wait(client, [])
    rep = _report(client, task_id)
    assert rep["issues"] == []
    assert rep["summary"]["scanned_words"] == 0
    assert rep["summary"]["scanned_lines"] == 0
    assert rep["summary"]["total_issues"] == 0


def test_task_not_found_uses_unified_error(client):
    resp = client.post("/api/tasks/nope/quality-report")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "task not found"}


def test_pending_task_conflicts(client):
    from app import db
    db.create_task("pending-id", '{"page_width": 1, "page_height": 1, "words": []}')
    resp = client.post("/api/tasks/pending-id/quality-report")
    assert resp.status_code == 409
    assert "not ready" in resp.json()["detail"]


def test_failed_task_conflicts(client):
    from datetime import datetime, timezone
    from app import db
    db.create_task("failed-id", '{"page_width": 1, "page_height": 1, "words": []}')
    db.mark_status("failed-id", "failed", error="boom",
                   finished_at=datetime.now(timezone.utc))
    resp = client.post("/api/tasks/failed-id/quality-report")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "task not ready (status=failed)"
