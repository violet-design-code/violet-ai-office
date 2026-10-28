"""Bounded background polling. Default DB settings stopped; only mock delivery exists."""
import threading
from .store import Store
from .office import Office


def start(db_path,interval=30):
    if not 0.05<=interval<=60:raise ValueError('Bounded polling interval required')
    stop=threading.Event()
    def run():
        while not stop.is_set():
            store=None
            try:
                store=Store(db_path);Office(store).schedule.tick()
            except Exception:
                # No raw exception, event content, identity or credentials in logs.
                if store:
                    try:store.db.execute("INSERT INTO sc_ticks(executed_at,prepared,processed,error_code) VALUES(datetime('now'),0,0,'worker_failure')")
                    except Exception:pass
            finally:
                if store:store.close()
            stop.wait(interval)
    thread=threading.Thread(target=run,name='violet-schedule-mock',daemon=True);thread.start();return stop,thread
