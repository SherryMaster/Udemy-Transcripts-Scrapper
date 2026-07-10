"""
SeleniumDriverManager: persistent undetected-chromedriver lifecycle.
Held as a single shared instance so the driver survives across
ScraperSession instances (needed for the first-run login flow).
"""
import os

import undetected_chromedriver as uc


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

    def execute_async_js(self, js_body: str, timeout: int = 60) -> str:
        """Execute an async JS body via execute_async_script. Returns raw string."""
        if self._driver is None:
            raise RuntimeError("Driver not connected. Call connect() first.")
        self._driver.set_script_timeout(timeout)
        wrapped = self._wrap_async_js(js_body)
        return self._driver.execute_async_script(wrapped)

    def connect(self):
        """Return the existing driver if alive, else launch a new one."""
        if self._driver is not None:
            try:
                self._driver.current_url
                return self._driver
            except Exception:
                self._driver = None
        options = uc.ChromeOptions()
        options.user_data_dir = self.profile_dir
        self._driver = uc.Chrome(options=options, version_main=self.version_main)
        return self._driver

    def is_logged_in(self) -> bool:
        """Check whether the current page is past Udemy's login wall."""
        if self._driver is None:
            return False
        url = self._driver.current_url or ""
        if "join/login" in url or "join/signup" in url:
            return False
        sign_in_count = self._driver.execute_script(
            "return document.querySelectorAll('a[href*=\"join/login\"], "
            "button[data-purpose*=\"sign-in\"], a[data-purpose*=\"sign-in\"]').length;"
        )
        return sign_in_count == 0

    def ensure_logged_in(self) -> None:
        """Raise RuntimeError if not logged in."""
        if not self.is_logged_in():
            raise RuntimeError(
                "Not logged in. Launch the scraper, log into Udemy once in "
                "the Selenium window, then retry."
            )

    def reconnect(self):
        """Quit the dead driver and relaunch on the same profile. One-shot guard."""
        if self._reconnecting:
            return self._driver
        self._reconnecting = True
        try:
            if self._driver is not None:
                try:
                    self._driver.quit()
                except Exception:
                    pass
                self._driver = None
            options = uc.ChromeOptions()
            options.user_data_dir = self.profile_dir
            self._driver = uc.Chrome(options=options, version_main=self.version_main)
            return self._driver
        finally:
            self._reconnecting = False

    def quit(self):
        """Quit the driver and clear the reference."""
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None


# Single shared instance — survives across ScraperSession instances
# so the driver stays alive on the login-wall path (first-run setup).
shared_manager = SeleniumDriverManager()
