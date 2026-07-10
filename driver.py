"""
SeleniumDriverManager: persistent undetected-chromedriver lifecycle.
Held as a single shared instance so the driver survives across
ScraperSession instances (needed for the first-run login flow).
"""
import os


class SeleniumDriverManager:
    PROFILE_DIR_DEFAULT = "~/.udemy-scraper-profile"

    def __init__(self, profile_dir=None, version_main=148):
        if profile_dir is None:
            env = os.environ.get("UDEMY_SCRAPER_PROFILE")
            if env:
                profile_dir = env
            else:
                profile_dir = os.path.expanduser(self.PROFILE_DIR_DEFAULT)
        self.profile_dir = profile_dir
        self.version_main = version_main
        self._driver = None
        self._reconnecting = False