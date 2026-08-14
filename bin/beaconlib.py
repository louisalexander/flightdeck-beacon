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

# --- the fold --------------------------------------------------------------

PRIORITY = ("failed", "blocked", "working")

def fold(sessions, prev):
    """Folds every live session into one lamp state.

    Returns (base, landed, next_prev, changed).

    Priority is failed > blocked > working > idle. "failed" is written by
    flightdeck's bin/fleet-fail (sticky red, set by hand from the Stream
    Deck) and MUST outrank everything: it is the only state that means
    something is broken rather than merely pending. Note what happens if it
    is left out of this tuple -- it falls through to "idle", which turns the
    lamp OFF and reads as "everything landed" at exactly the wrong moment.

    `landed` is edge-triggered off a SET DIFFERENCE, not off the done set
    being non-empty. This is load-bearing: fleet-emit writes state "done" on
    Stop and that file PERSISTS as done until the session's next event, so
    "done" is a level while the green landing is an edge. A non-emptiness
    test would re-fire green on every 15s reap tick, forever.

    `and base != "blocked"` is the agreed priority -- amber outranks green.
    A landing elsewhere must not steal the lamp from an agent that is
    actually waiting on a human.
    """
    prev = prev if isinstance(prev, dict) else {}
    prev_base = prev.get("base", "idle")
    prev_done = set(prev.get("done") or [])

    live = [s for s in sessions if isinstance(s, dict)]
    states = set()
    done_ids = set()
    for session in live:
        state = session.get("state", "idle")
        states.add(state)
        if state == "done" and session.get("session_id"):
            done_ids.add(session["session_id"])

    base = "idle"
    for candidate in PRIORITY:
        if candidate in states:
            base = candidate
            break

    landed = bool(done_ids - prev_done) and base not in ("blocked", "failed")
    changed = landed or base != prev_base
    return base, landed, {"base": base, "done": sorted(done_ids)}, changed
