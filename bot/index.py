"""Disk-backed deduplication for tick histories larger than RAM."""

import sqlite3
import tempfile
from pathlib import Path


class TickIndex:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mnq-ticks-")
        self.connection = sqlite3.connect(Path(self.directory.name) / "ids.sqlite")
        self.connection.execute(
            "CREATE TABLE ticks (contract TEXT, id TEXT, PRIMARY KEY(contract,id)) WITHOUT ROWID"
        )

    def register(self, key):
        try:
            self.connection.execute("INSERT INTO ticks VALUES (?,?)", key)
            return True
        except sqlite3.IntegrityError:
            return False

    def close(self):
        self.connection.close()
        self.directory.cleanup()
