"""Keeps a copy of every run in a Postgres database, so runs survive a host that wipes its files.

The agents still work in a job folder. This module copies each folder into the database and back again.
Without DATABASE_URL the web app does not use it, and the folders are the only copy.
"""

import gzip
import threading
from pathlib import Path

import psycopg

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_files (
    job_id   text        NOT NULL,
    name     text        NOT NULL,
    content  bytea       NOT NULL,  -- the file, gzip-compressed
    saved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, name)
)"""
UPSERT = """
INSERT INTO job_files (job_id, name, content) VALUES (%s, %s, %s)
ON CONFLICT (job_id, name) DO UPDATE SET content = EXCLUDED.content, saved_at = now()"""
# Enough to show a run in the list. The rest of its folder is fetched the first time someone opens it.
SUMMARY_FILES = ("meta.json", "state.json")


def _plain(name: str) -> bool:
    """A file or folder name with no path in it, so a row can never write outside the jobs folder."""
    return bool(name) and name not in (".", "..") and Path(name).name == name


class Store:
    def __init__(self, url: str, jobs: Path):
        self.url, self.jobs = url, jobs
        self._missing: set[str] = set()  # runs whose folder here lacks files the database has
        self._lock = threading.Lock()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.url, connect_timeout=15)

    def start(self) -> None:
        """Create the table, then bring back the summary of every run this machine has lost."""
        with self._connect() as conn:
            conn.execute(SCHEMA)
            names: dict[str, set[str]] = {}
            for job_id, name in conn.execute("SELECT job_id, name FROM job_files"):
                if _plain(job_id) and _plain(name):
                    names.setdefault(job_id, set()).add(name)
            self._missing = {job_id for job_id, files in names.items()
                             if not all((self.jobs / job_id / name).exists() for name in files)}
            if self._missing:
                rows = conn.execute("SELECT job_id, name, content FROM job_files WHERE job_id = ANY(%s) AND name = ANY(%s)",
                                    (list(self._missing), list(SUMMARY_FILES)))
                for job_id, name, content in rows:
                    self._write(job_id, name, content)

    def save(self, folder: Path) -> None:
        """Copy every file in a job folder to the database, replacing the copies saved before."""
        rows = [(folder.name, path.name, gzip.compress(path.read_bytes())) for path in sorted(folder.iterdir()) if path.is_file()]
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(UPSERT, rows)

    def fetch(self, job_id: str) -> None:
        """Before a lost run is opened, bring back the files its folder is missing. Does nothing for complete runs."""
        if job_id not in self._missing:
            return
        with self._lock:  # two requests for the same run must not both write its files
            if job_id not in self._missing:
                return
            with self._connect() as conn:
                for name, content in conn.execute("SELECT name, content FROM job_files WHERE job_id = %s", (job_id,)):
                    if _plain(name):
                        self._write(job_id, name, content)
            self._missing.discard(job_id)

    def _write(self, job_id: str, name: str, content: bytes) -> None:
        """Restore one file, unless this machine already has it."""
        path = self.jobs / job_id / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.decompress(content))
