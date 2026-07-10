import os
from unittest.mock import patch, MagicMock, PropertyMock
from selenium.common.exceptions import WebDriverException
import pytest
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


@patch("driver.uc")
def test_connect_creates_driver_with_profile_and_version(mock_uc):
    mgr = SeleniumDriverManager(profile_dir="/tmp/fake-profile", version_main=148)
    fake_driver = MagicMock()
    mock_uc.Chrome.return_value = fake_driver
    result = mgr.connect()
    assert result is fake_driver
    mock_uc.Chrome.assert_called_once()
    kwargs = mock_uc.Chrome.call_args.kwargs
    assert kwargs["version_main"] == 148
    options = kwargs["options"]
    assert options.user_data_dir == "/tmp/fake-profile"


@patch("driver.uc")
def test_connect_reuses_existing_driver(mock_uc):
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    mgr._driver = fake_driver
    result = mgr.connect()
    assert result is fake_driver
    mock_uc.Chrome.assert_not_called()


@patch("driver.uc")
def test_connect_relaunches_when_existing_driver_is_dead(mock_uc):
    mgr = SeleniumDriverManager(profile_dir="/tmp/fake-profile")
    dead_driver = MagicMock()
    type(dead_driver).current_url = PropertyMock(side_effect=WebDriverException("session gone"))
    mgr._driver = dead_driver
    new_driver = MagicMock()
    mock_uc.Chrome.return_value = new_driver
    result = mgr.connect()
    assert result is new_driver
    mock_uc.Chrome.assert_called_once()
    assert mgr._driver is new_driver


def test_is_logged_in_true_when_no_login_button():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.current_url = "https://www.udemy.com/course/xyz/learn"
    fake_driver.execute_script.return_value = 0
    mgr._driver = fake_driver
    assert mgr.is_logged_in() is True


def test_is_logged_in_false_when_login_url():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.current_url = "https://www.udemy.com/join/login"
    mgr._driver = fake_driver
    assert mgr.is_logged_in() is False


def test_is_logged_in_false_when_signin_button_present():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.current_url = "https://www.udemy.com/course/xyz/learn"
    fake_driver.execute_script.return_value = 1
    mgr._driver = fake_driver
    assert mgr.is_logged_in() is False


def test_ensure_logged_in_raises_when_not_logged_in():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.current_url = "https://www.udemy.com/join/login"
    mgr._driver = fake_driver
    with pytest.raises(RuntimeError, match="Not logged in"):
        mgr.ensure_logged_in()


def test_ensure_logged_in_passes_when_logged_in():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.current_url = "https://www.udemy.com/course/xyz/learn"
    fake_driver.execute_script.return_value = 0
    mgr._driver = fake_driver
    mgr.ensure_logged_in()


@patch("driver.uc")
def test_reconnect_quits_old_and_relaunches(mock_uc):
    mgr = SeleniumDriverManager(profile_dir="/tmp/fake-profile")
    old_driver = MagicMock()
    mgr._driver = old_driver
    new_driver = MagicMock()
    mock_uc.Chrome.return_value = new_driver
    result = mgr.reconnect()
    old_driver.quit.assert_called_once()
    assert result is new_driver


@patch("driver.uc")
def test_reconnect_guarded_against_loops(mock_uc):
    mgr = SeleniumDriverManager()
    mgr._reconnecting = True
    old_driver = MagicMock()
    mgr._driver = old_driver
    mock_uc.Chrome.return_value = MagicMock()
    result = mgr.reconnect()
    assert result is old_driver
    mock_uc.Chrome.assert_not_called()


def test_quit_calls_driver_quit_and_clears():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    mgr._driver = fake_driver
    mgr.quit()
    fake_driver.quit.assert_called_once()
    assert mgr._driver is None


def test_quit_when_no_driver_is_noop():
    mgr = SeleniumDriverManager()
    mgr.quit()
    assert mgr._driver is None
