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
