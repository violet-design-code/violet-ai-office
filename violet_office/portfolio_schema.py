"""Transactional additive migration, no seeding or ownership of legacy data."""
from pathlib import Path
from .store import now

def migrate(store):
    if store.db.execute('SELECT 1 FROM office_migrations WHERE version=10').fetchone(): return
    sql=Path(__file__).resolve().parent.parent.joinpath('migrations/010_multi_business.sql').read_text()
    def op():
        for statement in sql.split(';'):
            if statement.strip(): store.db.execute(statement)
        store.db.execute('INSERT OR IGNORE INTO office_migrations VALUES(10,?)',(now().isoformat(),))
    store._transaction(op)
