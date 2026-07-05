import pytest
from scraper import extract_course_slug, sanitize_filename


@pytest.mark.parametrize("url,expected", [
    ("https://www.udemy.com/course/react-the-complete-guide/learn", "react-the-complete-guide"),
    ("https://www.udemy.com/course/python-ds/overview", "python-ds"),
    ("https://www.udemy.com/course/my-course/?ref=menu", "my-course"),
])
def test_extract_course_slug(url, expected):
    assert extract_course_slug(url) == expected


def test_extract_course_slug_invalid():
    with pytest.raises(ValueError):
        extract_course_slug("https://example.com/no/course/here")


def test_sanitize_filename_strips_dangerous_chars():
    assert sanitize_filename('hello/world:file*.txt') == "hello_world_file_.txt"


def test_sanitize_filename_truncates_long_names():
    assert sanitize_filename("x" * 300) == "x" * 200


def test_sanitize_filename_empty_returns_untitled():
    assert sanitize_filename("   ") == "untitled"
