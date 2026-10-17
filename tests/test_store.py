"""Runs survive a host that wipes its files: the web app keeps a copy of each run in Postgres and brings it back.

Most of these need a Postgres database they may empty: set TEST_DATABASE_URL to run them. They are skipped otherwise.
"""

import os
import shutil

import psycopg
import pytest
from fastapi.testclient import TestClient

import web.app as web
from web.store import SCHEMA, Store

from test_agents_offline import BOOK
from test_web import client, scripted_app, start, wait_for  # noqa: F401  (client and scripted_app are fixtures)

URL = os.environ.get("TEST_DATABASE_URL", "")
needs_database = pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to a Postgres database to run this")


@pytest.fixture
def database():
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute(SCHEMA)
        conn.execute("TRUNCATE job_files")
    return URL


def boot(monkeypatch, jobs):
    """Start the server afresh, as the host does after a restart. Leaving the `with` block shuts it down."""
    monkeypatch.setattr(web, "STORE", Store(URL, jobs))
    monkeypatch.setattr(web, "RUNS", {})
    return TestClient(web.app)


def wipe(jobs):
    for folder in jobs.iterdir():
        shutil.rmtree(folder)


def files(folder):
    return {path.name: path.read_bytes() for path in folder.iterdir()}


@needs_database
def test_a_finished_run_comes_back_after_the_files_are_wiped(scripted_app, database, monkeypatch, tmp_path):
    with boot(monkeypatch, tmp_path) as client:
        run_id = start(client, ask_approval=False)
        assert wait_for(client, run_id, {"done", "failed"})["phase"] == "done"
        card = client.get("/api/runs").json()[0]
        result = client.get(f"/api/runs/{run_id}/result").json()
        csv_file = client.get(f"/api/runs/{run_id}/download/csv").text
    before = files(tmp_path / run_id)
    assert {"input.md", "meta.json", "state.json", "output.json", "output.csv", "trace.jsonl"} <= before.keys()

    wipe(tmp_path)
    with boot(monkeypatch, tmp_path) as client:
        assert client.get("/api/runs").json() == [card]
        assert sorted(files(tmp_path / run_id)) == ["meta.json", "state.json"]  # the rest waits until the run is opened
        assert client.get(f"/api/runs/{run_id}/result").json() == result
        assert client.get(f"/api/runs/{run_id}/download/csv").text == csv_file
        assert client.get(f"/api/runs/{run_id}/input").json()["input"] == BOOK
    assert files(tmp_path / run_id) == before


@needs_database
def test_a_run_cut_off_by_a_shutdown_comes_back_as_stopped(scripted_app, database, monkeypatch, tmp_path):
    with boot(monkeypatch, tmp_path) as client:
        run_id = start(client)
        wait_for(client, run_id, {"awaiting_approval"})
    # Leaving the block shut the server down while the run waited for approval.
    wipe(tmp_path)
    with boot(monkeypatch, tmp_path) as client:
        status = client.get(f"/api/runs/{run_id}").json()
        assert status["phase"] == "stopped" and not status["has_output"]
        assert client.get(f"/api/runs/{run_id}/input").json() | {"examples": ""} == {
            "name": "Tiny Book", "input": BOOK, "request": "one row per mantra", "examples": ""}


@needs_database
def test_a_restart_without_a_wipe_changes_nothing_and_rows_cannot_escape_the_jobs_folder(scripted_app, database, monkeypatch, tmp_path):
    jobs = tmp_path / "jobs"
    monkeypatch.setattr(web, "JOBS", jobs)
    with boot(monkeypatch, jobs) as client:
        run_id = start(client, ask_approval=False)
        wait_for(client, run_id, {"done", "failed"})
    before = files(jobs / run_id)
    with psycopg.connect(URL) as conn:
        conn.execute("INSERT INTO job_files (job_id, name, content) VALUES ('..', 'x', ''), (%s, '../escaped', '')", (run_id,))

    with boot(monkeypatch, jobs) as client:
        assert client.get(f"/api/runs/{run_id}/result").status_code == 200
    assert files(jobs / run_id) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["jobs"]


def test_a_database_failure_is_reported_but_the_run_still_finishes(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "STORE", Store("postgresql://nobody@127.0.0.1:1/none", tmp_path))
    run_id = start(client, ask_approval=False)
    status = wait_for(client, run_id, {"done", "failed"})
    assert status["phase"] == "done" and status["has_output"]
    assert any(line.startswith("Could not save this run to the database") for line in client.get(f"/api/runs/{run_id}").json()["log"])
