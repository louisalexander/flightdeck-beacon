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

    `and base not in ("blocked", "failed")` is the agreed priority -- amber
    and red both outrank green. A landing elsewhere must not steal the lamp
    from an agent that is actually waiting on a human, nor from one that
    broke.
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

# --- home assistant transport ----------------------------------------------

SERVICE = "script/beacon_render"

# Fallbacks matching flightdeck's config/fleet.json, used only when the
# snapshot arrives without a states block. The palette's real home is
# fleet.json -- these exist so a malformed snapshot dims the lamp rather
# than blacking it out.
DEFAULT_COLORS = {
    "working": (18, 86, 163),     # #1256A3
    "blocked": (245, 166, 35),    # #F5A623
    "done": (35, 134, 54),        # #238636
    "failed": (180, 35, 24),      # #B42318
    "idle": (0, 0, 0),
}

def service_url(config):
    base = (config.get("ha_url") or "").rstrip("/")
    return "{}/api/services/{}".format(base, SERVICE)

def hex_to_rgb(value, default):
    """Parses '#RRGGBB' (or 'RRGGBB') into [r, g, b]. Returns default on anything else."""
    try:
        text = str(value).lstrip("#")
        if len(text) != 6:
            return default
        return [int(text[i:i + 2], 16) for i in (0, 2, 4)]
    except Exception:
        return default

def palette_rgb(states, name):
    """Resolves one state's colour out of the snapshot palette."""
    entry = states.get(name) if isinstance(states, dict) else None
    color = entry.get("color") if isinstance(entry, dict) else None
    return hex_to_rgb(color, list(DEFAULT_COLORS.get(name, (0, 0, 0))))

def service_body(base, landed, states):
    """Builds the script.beacon_render service payload.

    Colours ride along with the call so that flightdeck's config/fleet.json
    stays the single source of truth for the palette. The Home Assistant
    script keeps only policy -- brightness, effect and timing -- which is
    meant to differ between a backlit key and a lamp lighting a room.
    """
    return json.dumps({
        "base": base,
        "landed": bool(landed),
        "rgb": palette_rgb(states, base),
        "rgb_done": palette_rgb(states, "done"),
    }, separators=(",", ":"))

def ensure_headers_file(config):
    """Writes the curl header file at 0600 and returns its path.

    The token goes in a file rather than in argv because `ps` is world
    readable: `curl -H "Authorization: Bearer <token>"` would leak a
    long-lived Home Assistant token to every process on the machine.

    The write itself uses the same temp-file-then-os.replace() pattern as
    write_json_atomic(), rather than a plain truncating write, because
    multiple beacon-render processes can run concurrently (several Claude
    Code sessions ending at once each drive their own
    SessionEnd -> fleet-emit -> fleet-reconcile -> beacon-render). A
    non-atomic write leaves a window where a concurrent curl reader can open
    a truncated or partial header file, send no valid Authorization header,
    and silently drop that one Home Assistant dispatch.
    """
    path = curl_headers_path()
    wanted = "Authorization: Bearer {}\nContent-Type: application/json\n".format(
        config.get("token") or "")
    existing = None
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            existing = handle.read()
    except Exception:
        existing = None
    if existing != wanted:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".{}.".format(path.name))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(wanted)
            os.replace(tmp, str(path))
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
    os.chmod(str(path), 0o600)
    return path
