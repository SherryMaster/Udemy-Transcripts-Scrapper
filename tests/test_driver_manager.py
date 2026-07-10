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

def test_execute_async_js_calls_execute_async_script_and_returns_string():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.execute_async_script.return_value = '{"result": "ok"}'
    mgr._driver = fake_driver
    out = mgr.execute_async_js("some js here", timeout=45)
    fake_driver.set_script_timeout.assert_called_once_with(45)
    fake_driver.execute_async_script.assert_called_once()
    called_arg = fake_driver.execute_async_script.call_args[0][0]
    assert "var cb = arguments[arguments.length - 1];" in called_arg
    assert "some js here" in called_arg
    assert out == '{"result": "ok"}'


def test_execute_async_js_returns_error_string_on_js_error():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.execute_async_script.return_value = '{"error": "boom"}'
    mgr._driver = fake_driver
    out = mgr.execute_async_js("js", timeout=60)
    assert out == '{"error": "boom"}'
