# Beacon — Ambient Agent-Fleet Light — Design

**Date:** 2026-08-13
**Status:** Approved design, ready for implementation planning
**Repos touched:** `beacon` (new), `flightdeck` (one seam), `homeassistant` (script + helper + fixture)

## Problem

flightdeck makes agent-fleet state readable **at the desk** — eight Stream Deck keys,
colour is state, one thumb to focus. Its design doc names the expensive failure directly:
an agent sitting blocked on a permission prompt while attention is elsewhere. The work is
done, the unblock is one keystroke, and the only thing missing is *knowing*.

But "attention is elsewhere" often means **not at the desk at all**. A Stream Deck key
cannot be seen from the next room. Beacon extends the same signal past the edge of the
desk: one Philips Hue lamp, glanceable from across the house, showing whether the fleet is
working, stuck on you, or finished.

Beacon is a *second renderer of existing state*, not a second state tracker. flightdeck
already owns session tracking, and Beacon must not duplicate it.

## Scope

**In:** one lamp, four states, driven off flightdeck's existing reconcile pass.

**Out (v1):** per-session colour (the lamp folds the whole fleet to one state); any input
capability (the lamp reports, it does not act); multi-lamp / room-aware output; a watchdog
for the laptop-sleep case (see Failure Modes).

## The light

| | |
|---|---|
| Entity | `light.hue_color_lamp_1` (friendly name **"Beacon"**) |
| Hardware | Signify LCT016, Hue White & Color A19 |
| Firmware | `1.88.1` at time of writing |
| `supported_features` | `40` = flash + transition — **no `effect` bit** |

The missing `effect` support is a **firmware** limitation, not a model limitation. The
identical LCT016 at `light.master_bedroom_sitting_room_lamp_bulb`, on firmware `1.116.12`,
reports `supported_features: 44` and `effect_list: [candle, fire, prism, sparkle, opal,
glisten]`. Same model, same bridge, same integration — only the firmware differs. A Hue
update is in progress; the design must not block on it (see §4).

Of the six effects, only `opal`, `glisten` and `sparkle` shimmer *on top of the current
colour*. `candle` and `fire` force a warm palette and `prism` crawls through hues — none of
those three can stay blue.

## States

Four states, folded from the whole fleet:

| State | Lamp | Meaning |
|---|---|---|
| `idle` | off | nothing in flight |
| `working` | dim blue, shimmering | at least one agent is working |
| `blocked` | solid amber, ~35% | at least one agent is waiting on you |
| landing | snap green → hold 15s → fade 15s | an agent just finished |

`blocked` is solid on purpose: nothing is actually moving, and a shimmer would imply
otherwise.

### Palette

Beacon does **not** define colours. It reads flightdeck's existing `states` block in
`config/fleet.json`, which already declares exactly this vocabulary:

```
blocked  #F5A623   working  #1256A3   done  #238636   idle  #25282D   failed  #B42318
```

One palette, two renderers — a key under your thumb and a lamp in the room can never drift
apart. Those hex values were chosen for a small backlit key against black; on a lamp
filling a room, **keep the hue identical and let brightness differ per renderer**. Do not
fork the colours.

`failed` (`#B42318`) is declared in `fleet.json` but never emitted — `fleet-emit` has no
`StopFailure` hook. Out of scope here; noted so a future turn-died-on-an-API-error state
has a colour waiting for it.

## Architecture

```
┌─ Claude Code agent ─┐
│  hooks fire         │
└──────────┬──────────┘
           │ bin/fleet-emit <event>
           ▼
  ~/.fleet/sessions/<session-id>.json      ← flightdeck owns this
           │
           ▼
  bin/fleet-reconcile ─┬─► ~/.fleet/slots.json      → Stream Deck
                       └─► beacon-render (stdin)    → script.beacon_render → the lamp
     ▲
     └── also invoked by bin/fleet-reap on its 15s launchd tick
```

Hanging Beacon off `fleet-reconcile` rather than off its own Claude Code hooks buys one
property worth naming: `fleet-reap` already calls reconcile on a 15s tick after removing
sessions whose PID is gone. So **killing a terminal mid-task turns the lamp off within 15
seconds, for free**. A standalone Beacon would have had to reimplement PID liveness.

## 1 — The renderer seam (flightdeck)

`config/fleet.json` gains one key:

```json
"renderers": ["/Users/pk/code/beacon/bin/beacon-render"]
```

At the end of `fleet-reconcile`, after `slots.json` is written, each configured renderer is
invoked with a **fleet snapshot piped on stdin**. Piping rather than letting the renderer
read `~/.fleet` itself makes the seam an explicit contract, and means `beacon` never
imports `fleetlib` across a repo boundary.

**Snapshot schema (the contract):**

```json
{
  "ts": 1755100000,
  "sessions": [
    {"session_id": "uuid", "state": "working", "repo": "beacon",
     "branch": "main", "cwd": "/Users/pk/code/beacon"}
  ],
  "states": {
    "working": {"color": "#1256A3", "glyph": "working", "...": "..."},
    "blocked": {"color": "#F5A623", "...": "..."}
  }
}
```

`states` is copied **verbatim** from `fleet.json` — including the Stream-Deck-only keys
(`glyph`, `glyphColor`, `textColor`), which Beacon simply ignores. Passing the whole block
rather than a filtered subset means a future palette key needs no change to the seam.
Beacon reads only `color`.

**Invocation follows existing repo conventions:** bounded `timeout`, `stdout`/`stderr` to
`DEVNULL`, wrapped so that a renderer which is missing, crashes, hangs past its timeout, or
emits garbage can never take down reconcile or the Stream Deck. flightdeck's exit-0
guarantee extends outward across this seam.

`renderers` absent or empty must behave exactly as today.

## 2 — The fold (beacon)

`bin/beacon-render` is a pure function wrapped in thin IO.

```
fold(sessions, prev) -> (base, landed, next_prev)

  blocked  = any(s.state == "blocked")
  working  = any(s.state == "working")
  done_set = {s.session_id for s in sessions if s.state == "done"}

  base   = "blocked" if blocked else "working" if working else "idle"
  landed = bool(done_set - prev.done_set) and base != "blocked"
```

Two decisions are encoded here and both matter:

**`landed` is edge-triggered off a set difference.** In `fleet-emit`, `Stop` writes state
`done` and that file *persists* as `done` until the session's next event. So `done` is a
**level, not an edge**, while the green landing is inherently an edge ("a window just
finished"). Testing `done_set` for non-emptiness would re-fire green on every 15s reap tick
forever. Only a session *entering* the done set counts.

**`and base != "blocked"`** implements the agreed priority — amber outranks green. When
some other agent is stuck on you, a landing elsewhere does not steal the lamp, because only
one of those two things needs your hands.

Green must still fire while another session is `working` — that was the chosen concurrency
semantic: the lamp returns to blue after the fade if anything is still in flight, and to
off if that was the last one. The HA script handles that decay, since `base` is passed
alongside `landed`.

**Change detection.** State persists to `~/.beacon/last.json` (atomic write via temp file +
`os.replace`, mirroring `fleetlib.write_json_atomic`). Home Assistant is called only when
`base` changed **or** `landed` is true. Both the per-tool-call event storm and the 15s reap
tick therefore collapse to zero network traffic when nothing has actually changed.

**The HA call is fully detached** — `subprocess.Popen(..., start_new_session=True)`, no
wait. This is load-bearing: the teardown chain is `SessionEnd → fleet-emit →
fleet-reconcile → beacon-render`, and **`SessionEnd` hooks share a 1.5-second budget**
across all hooks. Nothing on that path may block on the network.

`beacon-render` must exit 0 unconditionally, for the same reason `fleet-emit` does.

## 3 — The HA renderer

`script.beacon_render` in `scripts.yaml`, `mode: restart`, fields `base`
(`idle|working|blocked`) and `landed` (bool).

```
1. input_boolean.beacon_enabled off  OR  input_boolean.sleeping on
     → light.turn_off, stop.
2. landed
     → green, transition 0, full brightness
     → delay 15s
     → render base with transition 15
3. else render base with transition 2:
     blocked → amber,  ~35%, solid
     working → blue,   ~12%, shimmer (see §4)
     idle    → light.turn_off
```

`mode: restart` is the reason this timing lives in HA rather than in a backgrounded `sleep`
on the laptop: a new event arriving mid-fade cancels the pending fade cleanly and for free,
which is exactly the chosen semantic. It also survives the laptop closing.

`input_boolean.sleeping` already exists (set by Good Night, cleared by Good Morning). The
sleep gate means a late-night session cannot light the house.

`input_boolean.beacon_enabled` is **new** and must be added to `configuration.yaml`. Note
that unlike an `automations.yaml`-only change, this triggers a **full `ha core restart`** on
deploy.

## 4 — The shimmer, without blocking on firmware

While the bulb reports no `effect_list`, `working` renders as solid dim blue. A `choose`
branch in the script applies `effect: opal` **only when the bulb advertises it**:

```yaml
- conditions: "{{ 'opal' in (state_attr('light.hue_color_lamp_1','effect_list') or []) }}"
```

So the pending firmware update is a **live upgrade, not a code change** — the same deployed
script starts shimmering the moment Hue pushes it. Once it lands, compare `opal` /
`glisten` / `sparkle` on the actual lamp and pin the winner.

## 5 — Failure modes

| Failure | Behaviour | Verdict |
|---|---|---|
| HA unreachable | Detached curl fails silently; lamp goes stale; next state change re-syncs | Logged, never fatal |
| Terminal killed mid-task | `fleet-reap` → reconcile → beacon → off within 15s | Free, from hanging off reconcile |
| `beacon-render` crashes or hangs | Reconcile logs and continues; Stream Deck unaffected | Covered by §1 |
| Malformed snapshot on stdin | Renderer logs, exits 0, does not call HA | Covered |
| **Laptop sleeps mid-task** | Lamp strands blue — reap can't help, the process is alive | **Accepted in v1**, not fixed |

## 6 — Secrets

`~/.beacon/config.json`, mode `0600`, holding the HA base URL and a long-lived access
token. The repo ships only `config.example.json`. The token must never enter the repo, the
snapshot, or the log.

## 7 — Testing

**beacon** — table-driven tests over `fold()`. It is a pure function, so coverage is cheap
and total: no sessions; one working; blocked + working; two simultaneous landings; a
landing suppressed by a concurrent `blocked`; `done` → session reaped; repeat calls with no
change emitting no HA call; malformed/missing `prev`.

**flightdeck** — reconcile still succeeds when a renderer is missing, exits non-zero, hangs
past its timeout, or emits garbage; and `slots.json` is written *before* renderers run, so a
bad renderer cannot delay the Stream Deck.

**homeassistant** — `script.beacon_render` and `input_boolean.beacon_enabled` added.
**`scripts/refresh-entity-snapshot.sh` must be run first**: `light.hue_color_lamp_1` is
currently *absent* from `tests/fixtures/entities.txt`, so referencing it would fail
`test_entity_references` and block the deploy.

## Deferred

- `failed` / `StopFailure` → red. `fleet.json` already reserves the colour.
- A watchdog for the laptop-sleep case.
- Per-session or per-room output (more than one lamp).
- Distinguishing "Claude asked you a question and stopped" from "work landed" — both
  currently produce `Stop` → green.
