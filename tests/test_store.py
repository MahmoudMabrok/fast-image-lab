"""Job store: queue ordering, TTL expiry and cleanup."""

from __future__ import annotations

import time

from gateway.store import Store


def make_job(job_id: str, created_at: float | None = None) -> dict:
    now = time.time() if created_at is None else created_at
    return {
        "id": job_id,
        "prompt": "a cat",
        "negative": "",
        "width": 512,
        "height": 512,
        "steps": 4,
        "cfg": 1.0,
        "seed": 1,
        "status": "queued",
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "expires_at": None,
        "progress": 0.0,
        "step": 0,
        "total_steps": 4,
        "error": None,
        "filename": None,
        "bytes": None,
        "prompt_id": None,
        "source": "test",
    }


def test_claim_next_is_fifo(tmp_path):
    store = Store(tmp_path / "jobs.sqlite3")
    store.create(make_job("img_a", created_at=100.0))
    store.create(make_job("img_b", created_at=200.0))

    first = store.claim_next()
    assert first is not None and first["id"] == "img_a"
    assert first["status"] == "running"
    assert first["started_at"] is not None

    second = store.claim_next()
    assert second is not None and second["id"] == "img_b"
    assert store.claim_next() is None
    store.close()


def test_queue_position_counts_the_running_job(tmp_path):
    store = Store(tmp_path / "jobs.sqlite3")
    store.create(make_job("img_a", created_at=100.0))
    store.create(make_job("img_b", created_at=200.0))
    store.claim_next()
    assert store.queue_position("img_b") == 1
    store.close()


def test_expire_due_only_touches_succeeded_jobs(tmp_path):
    store = Store(tmp_path / "jobs.sqlite3")
    store.create(make_job("img_a"))
    store.update(
        "img_a",
        status="succeeded",
        filename="img_a.png",
        bytes=1234,
        expires_at=time.time() - 1,
    )
    store.create(make_job("img_b"))
    store.update("img_b", status="failed", expires_at=time.time() - 1)

    due = store.expire_due()
    assert [job["id"] for job in due] == ["img_a"]
    expired = store.get("img_a")
    assert expired is not None
    assert expired["status"] == "expired"
    assert expired["filename"] is None
    assert store.get("img_b")["status"] == "failed"
    store.close()


def test_purge_keeps_recent_rows(tmp_path):
    store = Store(tmp_path / "jobs.sqlite3")
    store.create(make_job("old", created_at=100.0))
    store.create(make_job("new", created_at=time.time()))
    store.update("old", status="expired")

    removed = store.purge_older_than(time.time() - 3600)
    assert removed == 1
    assert store.get("old") is None
    assert store.get("new") is not None
    store.close()


def test_expires_in_is_reported(tmp_path):
    store = Store(tmp_path / "jobs.sqlite3")
    store.create(make_job("img_a"))
    store.update("img_a", status="succeeded", expires_at=time.time() + 300)
    job = store.get("img_a")
    assert job is not None
    assert 295 <= job["expires_in"] <= 300
    store.close()
