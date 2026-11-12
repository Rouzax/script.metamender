from resources.lib.constants import DEFAULT_REFETCH_SCHEDULE, parse_refetch_schedule


def test_parse_schedule_basic():
    assert parse_refetch_schedule("7,30,90") == (7, 30, 90)


def test_parse_schedule_strips_and_orders():
    assert parse_refetch_schedule(" 7 , 14 ,30 ") == (7, 14, 30)


def test_parse_schedule_garbage_falls_back():
    assert parse_refetch_schedule("abc") == DEFAULT_REFETCH_SCHEDULE


def test_parse_schedule_empty_falls_back():
    assert parse_refetch_schedule("") == DEFAULT_REFETCH_SCHEDULE


def test_parse_schedule_drops_nonpositive():
    assert parse_refetch_schedule("7,0,-5,30") == (7, 30)


def test_no_api_key_is_written_in_plain_text():
    """Kodi's repository reviewers ask for keys to be obfuscated in the source:
    no name for a key, token, secret or password is assigned a literal string
    in any shipped module."""
    import re
    from pathlib import Path
    literal = re.compile(r"^\s*\w*(KEY|TOKEN|SECRET|PASSWORD)\w*\s*(:\s*\w+\s*)?=\s*[rbu]?[\"'][\w.\-]{16,}[\"']",
                         re.IGNORECASE | re.MULTILINE)
    root = Path(__file__).resolve().parents[1] / "resources" / "lib"
    for path in root.rglob("*.py"):
        found = literal.search(path.read_text(encoding="utf-8"))
        assert not found, f"{path.relative_to(root)}: {found.group(0) if found else ''}"


def test_the_tvdb_key_decodes_to_a_project_key():
    import re
    from resources.lib.constants import TVDB_API_KEY
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                        TVDB_API_KEY)
