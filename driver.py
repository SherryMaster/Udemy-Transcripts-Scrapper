"""
SeleniumDriverManager: persistent ChromeDriver lifecycle.
Uses regular Selenium with a manually-managed chromedriver binary.
On NixOS, the binary is patched via patchelf to embed the correct
RPATH and interpreter, so no LD_LIBRARY_PATH manipulation is needed.
"""
import io
import os
import stat
import subprocess
import sys
import time
import traceback
import logging
import shutil
import urllib.request
import zipfile

import undetected_chromedriver as uc

logger = logging.getLogger(__name__)

CHROMEDRIVER_DIR = os.path.expanduser("~/.local/share/udemy-chromedriver")
CHROMEDRIVER_PATH = os.path.join(CHROMEDRIVER_DIR, "chromedriver")


def _find_chrome_binary() -> str:
    """Find Chrome binary path."""
    for name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]:
        path = shutil.which(name)
        if path:
            logger.info("[Chrome] Found %s at %s", name, path)
            return path
    logger.warning("[Chrome] No Chrome binary found in PATH")
    return ""


def _patchelf_binary(binary_path: str):
    """Use patchelf to set the correct interpreter and RPATH on a binary.
    This makes the binary work on NixOS without any LD_LIBRARY_PATH."""
    logger.info("[Patchelf] Patching %s ...", binary_path)

    # Find the interpreter (dynamic linker)
    try:
        interp_result = subprocess.run(
            ["readelf", "-l", binary_path],
            capture_output=True, text=True, timeout=5,
        )
        for line in interp_result.stdout.splitlines():
            if "interpreter:" in line.lower():
                # Extract the current interpreter path
                current_interp = line.split("[")[-1].rstrip("]").strip()
                # Resolve it to the nix store path
                real_interp = os.path.realpath(current_interp)
                logger.info("[Patchelf] Interpreter: %s -> %s", current_interp, real_interp)
                break
        else:
            logger.warning("[Patchelf] Could not find interpreter in readelf output")
            return
    except Exception as e:
        logger.error("[Patchelf] readelf failed: %s", e)
        return

    # Build RPATH from ldd output — resolve ALL symlinks to their nix store targets
    rpath_dirs = set()
    try:
        result = subprocess.run(
            ["ldd", binary_path],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            for p in parts:
                if p.startswith("/") and os.path.exists(p):
                    rpath_dirs.add(os.path.dirname(p))
                    # Resolve symlink to actual nix store path
                    real = os.path.realpath(p)
                    rpath_dirs.add(os.path.dirname(real))
                    break
    except Exception as e:
        logger.error("[Patchelf] ldd failed: %s", e)

    # Also scan /lib and /lib64 for nix store symlinks (covers libs ldd might miss)
    for lib_dir in ["/lib64", "/lib"]:
        if not os.path.isdir(lib_dir):
            continue
        try:
            for entry in os.listdir(lib_dir):
                full = os.path.join(lib_dir, entry)
                if os.path.islink(full):
                    target = os.readlink(full)
                    if target.startswith("/nix/store/"):
                        rpath_dirs.add(os.path.dirname(target))
        except Exception:
            pass

    if not rpath_dirs:
        logger.error("[Patchelf] No RPATH dirs found")
        return

    rpath = ":".join(sorted(rpath_dirs))
    logger.info("[Patchelf] RPATH: %d dirs", len(rpath_dirs))

    # Apply patchelf via nix-shell
    patchelf_cmd = (
        f"patchelf --set-interpreter {real_interp} --set-rpath '{rpath}' {binary_path}"
    )
    logger.info("[Patchelf] Running: nix-shell -p patchelf --run '{patchelf_cmd}'")

    try:
        result = subprocess.run(
            ["nix-shell", "-p", "patchelf", "--run", patchelf_cmd],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error("[Patchelf] Failed: %s", result.stderr[:500])
        else:
            logger.info("[Patchelf] Success!")
    except FileNotFoundError:
        logger.error("[Patchelf] nix-shell not found — cannot patch binary")
    except subprocess.TimeoutExpired:
        logger.error("[Patchelf] nix-shell timed out")
    except Exception as e:
        logger.error("[Patchelf] Exception: %s", e)


def _ensure_chromedriver() -> str:
    """Download chromedriver if not present, patch it, and return path."""
    logger.info("[Chromedriver] Checking %s ...", CHROMEDRIVER_PATH)
    if os.path.isfile(CHROMEDRIVER_PATH):
        size = os.path.getsize(CHROMEDRIVER_PATH)
        logger.info("[Chromedriver] Found (%d bytes)", size)
        return CHROMEDRIVER_PATH

    logger.info("[Chromedriver] Not found — downloading...")

    chrome_binary = _find_chrome_binary()
    if not chrome_binary:
        raise RuntimeError(
            "Chrome not found. Install Google Chrome first."
        )

    result = subprocess.run([chrome_binary, "--version"], capture_output=True, text=True)
    chrome_version = result.stdout.strip().split()[-1]
    major = chrome_version.split(".")[0]
    logger.info("[Chromedriver] Chrome version: %s (major: %s)", chrome_version, major)

    dl_url = (
        f"https://storage.googleapis.com/chrome-for-testing-public/"
        f"{chrome_version}/linux64/chromedriver-linux64.zip"
    )
    logger.info("[Chromedriver] Downloading from: %s", dl_url)

    zip_data = urllib.request.urlopen(dl_url).read()
    logger.info("[Chromedriver] Downloaded %d bytes", len(zip_data))

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        for name in zf.namelist():
            if name.endswith("/chromedriver"):
                os.makedirs(CHROMEDRIVER_DIR, exist_ok=True)
                data = zf.read(name)
                with open(CHROMEDRIVER_PATH, "wb") as f:
                    f.write(data)
                os.chmod(
                    CHROMEDRIVER_PATH,
                    os.stat(CHROMEDRIVER_PATH).st_mode
                    | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH,
                )
                logger.info("[Chromedriver] Installed: %s (%d bytes)",
                            CHROMEDRIVER_PATH, os.path.getsize(CHROMEDRIVER_PATH))

                # Patch the binary for NixOS compatibility
                _patchelf_binary(CHROMEDRIVER_PATH)

                return CHROMEDRIVER_PATH

    raise RuntimeError(f"Could not find chromedriver in zip for Chrome {chrome_version}")


class SeleniumDriverManager:
    PROFILE_DIR_DEFAULT = "~/.udemy-scraper-profile"

    def __init__(self, profile_dir=None, version_main=None):
        if profile_dir is None:
            env = os.environ.get("UDEMY_SCRAPER_PROFILE")
            if env:
                profile_dir = env
                logger.info("[DriverManager] Profile from env: %s", profile_dir)
            else:
                profile_dir = os.path.expanduser(self.PROFILE_DIR_DEFAULT)
                logger.info("[DriverManager] Default profile: %s", profile_dir)
        else:
            logger.info("[DriverManager] Profile: %s", profile_dir)
        self.profile_dir = profile_dir
        self.version_main = version_main
        self._driver = None
        self._reconnecting = False
        self._last_error = None
        self._last_traceback = None

    def _wrap_async_js(self, js_body: str) -> str:
        return (
            "var cb = arguments[arguments.length - 1];\n"
            "(async () => {\n"
            f"{js_body}\n"
            "})().catch(e => cb(JSON.stringify({error: e.message})));"
        )

    def execute_async_js(self, js_body: str, timeout: int = 60) -> str:
        if self._driver is None:
            raise RuntimeError("Driver not connected. Call connect() first.")
        logger.debug("[DriverManager] execute_async_js: timeout=%d, js len=%d", timeout, len(js_body))
        self._driver.set_script_timeout(timeout)
        wrapped = self._wrap_async_js(js_body)
        t0 = time.time()
        try:
            result = self._driver.execute_async_script(wrapped)
            logger.debug("[DriverManager] execute_async_js done in %.2fs, result len=%d",
                         time.time() - t0, len(result) if result else 0)
            return result
        except Exception as e:
            logger.error("[DriverManager] execute_async_js FAILED after %.2fs: %s",
                         time.time() - t0, e)
            raise

    def _log_error(self, context: str, exc: Exception):
        self._last_error = exc
        self._last_traceback = traceback.format_exc()
        logger.error("[DriverManager] %s: %s: %s", context, type(exc).__name__, exc)
        logger.error("[DriverManager] Full traceback:\n%s", self._last_traceback)
        print(f"\n{'='*70}", file=sys.stderr)
        print(f"DRIVER ERROR ({context})", file=sys.stderr)
        print(f"  Type:    {type(exc).__name__}", file=sys.stderr)
        print(f"  Message: {exc}", file=sys.stderr)
        if hasattr(exc, "msg"):
            print(f"  Selenium msg: {exc.msg}", file=sys.stderr)
        print(f"  Profile: {self.profile_dir}", file=sys.stderr)
        print(f"  CWD: {os.getcwd()}", file=sys.stderr)
        print(f"{'='*70}\n", file=sys.stderr)
        sys.stderr.flush()

    def _make_driver(self):
        logger.info("[DriverManager] _make_driver() START")
        t0 = time.time()

        chromedriver_path = _ensure_chromedriver()
        chrome_binary = _find_chrome_binary()

        # Pre-validate
        logger.info("[DriverManager] Pre-validating chromedriver...")
        try:
            result = subprocess.run(
                [chromedriver_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            logger.info("[DriverManager] --version: rc=%d stdout=%r stderr=%r",
                        result.returncode, result.stdout.strip(), result.stderr.strip())
        except Exception as e:
            logger.error("[DriverManager] --version FAILED: %s", e)

        options = uc.ChromeOptions()
        if chrome_binary:
            options.binary_location = chrome_binary
        options.user_data_dir = self.profile_dir
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        # Anti-detection flags
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        logger.info("[DriverManager] Calling webdriver.Chrome()...")
        chrome_kwargs = {"options": options, "driver_executable_path": chromedriver_path}
        if self.version_main is not None:
            chrome_kwargs["version_main"] = self.version_main
        driver = uc.Chrome(**chrome_kwargs)
        logger.info("[DriverManager] Chrome launched in %.2fs — URL: %s",
                     time.time() - t0, driver.current_url)
        return driver

    def connect(self):
        logger.info("[DriverManager] connect()")
        if self._driver is not None:
            try:
                self._driver.current_url
                logger.info("[DriverManager] Existing driver alive")
                return self._driver
            except Exception as e:
                logger.warning("[DriverManager] Existing driver dead: %s", e)
                self._driver = None

        try:
            self._driver = self._make_driver()
            return self._driver
        except Exception as e:
            self._log_error("connect()", e)
            raise

    def is_logged_in(self) -> bool:
        if self._driver is None:
            return False
        url = self._driver.current_url or ""
        if "join/login" in url or "join/signup" in url:
            return False
        # Check for Cloudflare challenge page
        try:
            title = self._driver.title or ""
            page_source = self._driver.page_source or ""
            if "Just a moment" in title or "Verify you are human" in page_source:
                logger.info("[DriverManager] Cloudflare challenge detected — not logged in")
                return False
            if "Performing security verification" in page_source:
                logger.info("[DriverManager] Cloudflare security check in progress")
                return False
        except Exception:
            pass
        try:
            sign_in_count = self._driver.execute_script(
                "return document.querySelectorAll('a[href*=\"join/login\"], "
                "button[data-purpose*=\"sign-in\"], a[data-purpose*=\"sign-in\"]').length;"
            )
            return sign_in_count == 0
        except Exception:
            return False

    def ensure_logged_in(self) -> None:
        """Raise RuntimeError if not logged in. Waits briefly for Cloudflare."""
        import time
        # Give Cloudflare a moment to resolve
        time.sleep(3)
        if not self.is_logged_in():
            raise RuntimeError(
                "Not logged in. Log into Udemy in the Selenium window, "
                "solve any Cloudflare challenges, then retry."
            )

    def reconnect(self):
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
            self._driver = self._make_driver()
            return self._driver
        except Exception as e:
            self._log_error("reconnect()", e)
            raise
        finally:
            self._reconnecting = False

    def quit(self):
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None


def get_last_driver_error() -> tuple:
    return (shared_manager._last_error, shared_manager._last_traceback)


shared_manager = SeleniumDriverManager()
