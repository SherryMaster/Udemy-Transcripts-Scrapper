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