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


def test_without_home_window_the_lock_is_a_file(monkeypatch, tmp_path):
    """Headless Kodi has no home window: the lock falls back to a file."""
    def no_window(_wid):
        raise RuntimeError("Window id does not exist")

    monkeypatch.setattr(lock.xbmcgui, "Window", no_window)
    monkeypatch.setattr(lock, "_headless", None)
    monkeypatch.setattr(lock, "_lock_file_path", lambda: str(tmp_path / "scan.lock"))
    monkeypatch.setattr(lock, "_now", lambda: 1000.0)
    monkeypatch.setattr(lock, "_my_token", None)

    assert lock.acquire_scan_lock() is True
    assert lock.acquire_scan_lock() is False   # held
    lock.release_scan_lock()
    assert not (tmp_path / "scan.lock").exists()
    assert lock.acquire_scan_lock() is True     # released
    lock.release_scan_lock()


def test_file_lock_stale_value_is_reclaimed(monkeypatch, tmp_path):
    def no_window(_wid):
        raise RuntimeError("Window id does not exist")

    path = tmp_path / "scan.lock"
    path.write_text("100.0:crashed")
    monkeypatch.setattr(lock.xbmcgui, "Window", no_window)
    monkeypatch.setattr(lock, "_headless", None)
    monkeypatch.setattr(lock, "_lock_file_path", lambda: str(path))
    monkeypatch.setattr(lock, "_now", lambda: 100000.0)
    monkeypatch.setattr(lock, "_my_token", None)

    assert lock.acquire_scan_lock() is True
    lock.release_scan_lock()


def test_headless_is_detected_once_per_interpreter(monkeypatch, tmp_path):
    """Each failed Window() lookup makes Kodi log an ERROR; ask only once."""
    calls = []

    def no_window(_wid):
        calls.append(1)
        raise RuntimeError("Window id does not exist")

    monkeypatch.setattr(lock.xbmcgui, "Window", no_window)
    monkeypatch.setattr(lock, "_headless", None)
    monkeypatch.setattr(lock, "_lock_file_path", lambda: str(tmp_path / "scan.lock"))
    monkeypatch.setattr(lock, "_now", lambda: 1000.0)
    monkeypatch.setattr(lock, "_my_token", None)

    assert lock.acquire_scan_lock() is True
    lock.release_scan_lock()
    assert lock.acquire_scan_lock() is True
    lock.release_scan_lock()
    assert len(calls) == 1


def test_clear_file_lock_removes_a_leftover_file(monkeypatch, tmp_path):
    path = tmp_path / "scan.lock"
    path.write_text("1000.0:killed")
    monkeypatch.setattr(lock, "_lock_file_path", lambda: str(path))

    lock.clear_file_lock()
    lock.clear_file_lock()  # nothing left: no error

    assert not path.exists()
