import os
from unittest.mock import patch, MagicMock
from driver import SeleniumDriverManager


def test_profile_dir_defaults_to_home():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("UDEMY_SCRAPER_PROFILE", None)
        mgr = SeleniumDriverManager()
        assert mgr.profile_dir == os.path.expanduser("~/.udemy-scraper-profile")


def test_profile_dir_respects_env_var(tmp_path):
    custom = str(tmp_path / "custom-profile")
    with patch.dict(os.environ, {"UDEMY_SCRAPER_PROFILE": custom}):
        mgr = SeleniumDriverManager()
        assert mgr.profile_dir == custom


def test_wrap_async_js_wraps_body_with_callback():
    mgr = SeleniumDriverManager()
    body = "(async () => { return JSON.stringify({a:1}); })()"
    wrapped = mgr._wrap_async_js(body)
    assert "var cb = arguments[arguments.length - 1];" in wrapped
    assert body in wrapped
    assert ".catch(e => cb(JSON.stringify({error: e.message})))" in wrapped


def test_wrap_async_js_handles_empty_body():
    mgr = SeleniumDriverManager()
    wrapped = mgr._wrap_async_js("")
    assert "cb(JSON.stringify(" in wrapped