"""Short-lived count cache for the admin navigation badge.

Every full page render used to run an extra database COUNT. The badge can be
slightly delayed across server workers; local writes invalidate it at once.
"""

from threading import Lock
from time import monotonic
from weakref import WeakKeyDictionary
from flask import current_app


_lock = Lock()
_cache = WeakKeyDictionary()
_TTL_SECONDS = 10


def pending_deletion_count():
    app = current_app._get_current_object()
    now = monotonic()
    cached = _cache.get(app)
    if cached and now < cached[1]:
        return cached[0]
    with _lock:
        now = monotonic()
        cached = _cache.get(app)
        if cached and now < cached[1]:
            return cached[0]
        from models.deletion_request import DeletionRequest
        value = DeletionRequest.query.filter_by(status='Pending').count()
        _cache[app] = (value, monotonic() + _TTL_SECONDS)
        return value


def invalidate_pending_deletion_count():
    app = current_app._get_current_object()
    with _lock:
        _cache.pop(app, None)
