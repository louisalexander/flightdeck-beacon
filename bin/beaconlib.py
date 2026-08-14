"""Shared helpers for flightdeck-beacon. Standard library only, Python 3.9 compatible.

3.9 is the floor because this code is reachable from launchd (fleet-reap ->
fleet-reconcile -> beacon-render), and launchd's PATH resolves CommandLineTools
python3, not Homebrew's.
"""

import json
import os
import tempfile
import time
from pathlib import Path

# --- paths -----------------------------------------------------------------

def beacon_home():
    return Path(os.environ.get("BEACON_HOME") or (Path.home() / ".beacon"))

def state_path():
    return beacon_home() / "last.json"

def log_path():
    return beacon_home() / "beacon.log"

def config_path():
    return beacon_home() / "config.json"

def curl_headers_path():
    return beacon_home() / "curl-headers"

# --- logging ---------------------------------------------------------------

def log(message):
    """Best-effort logging. Never raises -- callers may be inside a hook path."""
    try:
        beacon_home().mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(str(log_path()), "a", encoding="utf-8") as handle:
            handle.write("{} {}\n".format(stamp, message))
    except Exception:
        pass

# --- json io ---------------------------------------------------------------

def read_json(path, default=None):
    """Returns default on any failure, including a partially written file."""
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default

def write_json_atomic(path, obj):
    """Writes via a temp file in the same directory, then os.replace()."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".{}.".format(path.name))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, separators=(",", ":"))
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise

# --- config ----------------------------------------------------------------

def load_config():
    config = read_json(config_path(), {})
    return config if isinstance(config, dict) else {}
