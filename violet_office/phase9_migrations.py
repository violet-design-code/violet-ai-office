"""Versioned, additive schema changes, transactionally applied once."""
from pathlib import Path
from .store import now

def migrate(store):
    exists=store.db.execute("SELECT 1 FROM sqlite_master WHERE name='office_migrations'").fetchone()
    if exists and store.db.execute('SELECT 1 FROM office_migrations WHERE version=9').fetchone():
        return
    sql=Path(__file__).resolve().parent.parent.joinpath('migrations/009_selection_seminars.sql').read_text()
    def op():
        for statement in sql.split(';'):
            if statement.strip():
                store.db.execute(statement)
        store.db.execute('INSERT OR IGNORE INTO office_migrations VALUES(9,?)',(now().isoformat(),))
    store._transaction(op)
