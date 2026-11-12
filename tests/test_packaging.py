"""What ships: the staging script decides what a test install and a release
zip contain."""
from __future__ import annotations

import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "docker" / "scripts" / "stage.py"
TOP_LEVEL = {"addon.xml", "default.py", "service.py", "context.py", "context_details.py",
             "LICENSE.txt", "changelog.txt", "resources", "icon.png", "fanart.jpg"}
# repository.rouzax/publish.py refuses a zip holding any of these.
BANNED = (".git", ".github/", ".claude/", "CLAUDE.md", "README.md", "tests/", "docs/",
          "_temp/", "__pycache__/", ".env", "conftest.py", "pyrightconfig.json")


def _run(*args: str) -> str:
    done = subprocess.run([sys.executable, str(STAGE), *args], capture_output=True,
                          text=True, check=True)
    return done.stdout.strip()


def _shipped(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


def test_a_staged_folder_holds_only_what_kodi_loads(tmp_path):
    _run("folder", str(tmp_path / "out"))
    shipped = _shipped(tmp_path / "out")
    assert {"addon.xml", "default.py", "service.py", "context.py", "context_details.py",
            "LICENSE.txt", "changelog.txt", "icon.png"} <= shipped
    assert "resources/settings.xml" in shipped
    assert "resources/lib/service/daemon.py" in shipped
    assert "resources/presets/AU.json" in shipped
    for path in shipped:
        assert path.split("/")[0] in TOP_LEVEL, path
        assert "__pycache__" not in path and not path.endswith(".pyc"), path


def test_restaging_removes_what_is_no_longer_shipped(tmp_path):
    out = tmp_path / "out"
    _run("folder", str(out))
    (out / "stale.txt").write_text("left over")
    _run("folder", str(out))
    assert not (out / "stale.txt").exists()


def test_a_folder_that_is_not_this_addon_is_never_replaced(tmp_path):
    out = tmp_path / "someone-elses"
    out.mkdir()
    (out / "keep.txt").write_text("not ours")
    done = subprocess.run([sys.executable, str(STAGE), "folder", str(out)],
                          check=False, capture_output=True, text=True)
    assert done.returncode != 0
    assert (out / "keep.txt").read_text() == "not ours"


def test_the_release_zip_holds_one_folder_named_after_the_addon(tmp_path):
    path = Path(_run("zip", str(tmp_path)))
    assert path.name.startswith("script.metamender-v") and "~" not in path.name
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    assert all(name.startswith("script.metamender/") for name in names)
    assert "script.metamender/addon.xml" in names
    for name in names:
        inner = name[len("script.metamender/"):]
        assert not any(inner.startswith(b) or ("/" + b) in inner for b in BANNED), name


def test_the_release_zip_is_reproducible_with_plain_permissions(tmp_path):
    """Same commit, same bytes: anyone can rebuild the published zip and compare it."""
    first = Path(_run("zip", str(tmp_path / "a")))
    second = Path(_run("zip", str(tmp_path / "b")))
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        for info in archive.infolist():
            mode = stat.S_IMODE(info.external_attr >> 16)
            assert mode == (0o755 if info.is_dir() else 0o644), info.filename
