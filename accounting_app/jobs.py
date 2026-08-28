"""Background jobs for work too slow to hold a request open.

Reading a long scanned invoice is one model call per batch - potentially a
minute or more. Doing that inline ties up a request thread, and the browser
sits on a request that many proxies will time out before it finishes.

Job *state* lives in PostgreSQL (see database.app_state_db) so the browser can
poll for progress from whichever worker answers, and so a worker recycle no
longer loses a job that is still running elsewhere.

Job *execution* stays on a thread in the process that accepted the upload, and
the worker-slot count below stays local to that process. MAX_WORKERS exists to
stop uploads starving this process's request threads, so it must be counted per
process - a shared cap of 2 across the whole deployment would be far too tight.
A job whose own process dies is still lost; the user re-uploads.
"""
import threading
import traceback
import uuid
from datetime import datetime

MAX_WORKERS = 2         # per process: never let uploads starve request threads

_lock = threading.Lock()
_running = 0


def _now():
    return datetime.now().strftime("%H:%M:%S")


def create(description=""):
    """Register a job and return its id."""
    from database.app_state_db import job_create

    job_id = uuid.uuid4().hex
    job_create(job_id, description, _now())
    return job_id


def get(job_id):
    from database.app_state_db import job_get

    try:
        return job_get(job_id)
    except Exception:
        return None


def set_progress(job_id, message):
    from database.app_state_db import job_update

    try:
        job_update(job_id, progress=message)
    except Exception:
        # Progress is cosmetic - never let a failed status write kill the job
        # that is actually doing the work.
        pass


def busy():
    """True when every worker slot in this process is taken."""
    with _lock:
        return _running >= MAX_WORKERS


def run(job_id, fn, *args, **kwargs):
    """Run `fn` on a worker thread, recording its outcome against the job."""
    from database.app_state_db import job_update

    def target():
        global _running
        with _lock:
            _running += 1
        try:
            try:
                job_update(job_id, status="running", progress="Working...")
            except Exception:
                pass
            result = fn(*args, **kwargs)
            job_update(job_id, status="done", result=result,
                       progress="Finished", finished=_now())
        except Exception as exc:
            traceback.print_exc()
            try:
                job_update(job_id, status="failed", error=str(exc),
                           progress="Failed", finished=_now())
            except Exception:
                traceback.print_exc()
        finally:
            with _lock:
                _running -= 1

    thread = threading.Thread(target=target, name=f"job-{job_id[:8]}",
                              daemon=True)
    thread.start()
    return job_id
