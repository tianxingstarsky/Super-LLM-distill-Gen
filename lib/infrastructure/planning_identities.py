"""Exact task identities on disk, scoped to one planning attempt."""
import sqlite3


class PlanningIdentities:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.connection.execute('PRAGMA cache_size=-512')
        self.connection.execute('CREATE TABLE IF NOT EXISTS identities (identity TEXT PRIMARY KEY)')
        self.connection.execute('DELETE FROM identities')
        self.connection.commit()

    def __contains__(self, identity):
        return self.connection.execute('SELECT 1 FROM identities WHERE identity=?',(identity,)).fetchone() is not None

    def update(self, identities):
        self.connection.executemany('INSERT INTO identities VALUES (?)',((identity,) for identity in identities))
        self.connection.commit()

    def close(self):
        self.connection.close()
