from unittest.mock import MagicMock
from resources.lib.service import lock


def test_acquire_then_second_fails(monkeypatch):
    store = {}
    win = MagicMock()
    win.getProperty = lambda k: store.get(k, "")
    win.setProperty = lambda k, v: store.__setitem__(k, v)
    win.clearProperty = lambda k: store.pop(k, None)
    monkeypatch.setattr(lock, "_home_window", lambda: win)
    monkeypatch.setattr(lock, "_now", lambda: 1000.0)
    assert lock.acquire_scan_lock() is True
    assert lock.acquire_scan_lock() is False   # held
    lock.release_scan_lock()
    assert lock.acquire_scan_lock() is True     # released


def test_stale_lock_is_reclaimed(monkeypatch):
    store = {"metamender_scan_lock": "100.0"}  # very old
    win = MagicMock()
    win.getProperty = lambda k: store.get(k, "")
    win.setProperty = lambda k, v: store.__setitem__(k, v)
    win.clearProperty = lambda k: store.pop(k, None)
    monkeypatch.setattr(lock, "_home_window", lambda: win)
    monkeypatch.setattr(lock, "_now", lambda: 100000.0)  # far future
    assert lock.acquire_scan_lock() is True   # stale lock reclaimed


def test_release_does_not_clear_another_owners_lock(monkeypatch):
    """Release is a no-op unless this process holds the lock (fencing token)."""
    store = {"metamender_scan_lock": "999.0:otherproc"}  # fresh, owned elsewhere
    win = MagicMock()
    win.getProperty = lambda k: store.get(k, "")
    win.setProperty = lambda k, v: store.__setitem__(k, v)
    win.clearProperty = lambda k: store.pop(k, None)
    monkeypatch.setattr(lock, "_home_window", lambda: win)
    monkeypatch.setattr(lock, "_now", lambda: 1000.0)
    monkeypatch.setattr(lock, "_my_token", None)  # we never acquired it

    lock.release_scan_lock()
    assert store.get("metamender_scan_lock") == "999.0:otherproc"
