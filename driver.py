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

    def _wrap_async_js(self, js_body: str) -> str:
        """Wrap an async JS body in the execute_async_script callback shim."""
        return (
            "var cb = arguments[arguments.length - 1];\n"
            "(async () => {\n"
            f"{js_body}\n"
            "})().catch(e => cb(JSON.stringify({error: e.message})));"
        )