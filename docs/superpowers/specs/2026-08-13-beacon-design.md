# Beacon — Ambient Agent-Fleet Light — Design

**Date:** 2026-08-13
**Status:** Built and deployed 2026-08-13. See `docs/superpowers/plans/2026-08-13-beacon-v1.md`
for the implementation and the record of what changed during it.
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
| Firmware | `1.116.12` (updated 2026-08-13, from `1.88.1`) |
| `supported_features` | `44` = flash + transition + **effect** |
| `effect_list` | `off, candle, fire, prism, sparkle, opal, glisten` |

Effects were initially absent. That was a **firmware** limitation, not a model limitation:
the identical LCT016 at `light.master_bedroom_sitting_room_lamp_bulb`, already on
`1.116.12`, reported `supported_features: 44` while Beacon on `1.88.1` reported `40`. Same
model, same bridge, same integration — only the firmware differed.

> **Gotcha worth keeping.** Updating the bulb firmware was **not sufficient**. Home
> Assistant caches device capabilities at integration setup, so Beacon still reported `40`
> and `effect_list: None` after the Hue update landed. It took
> `homeassistant.reload_config_entry` on the entity to re-read capabilities from the bridge.
> Expect the same after any future Hue firmware change.

Of the six effects, only `opal`, `glisten` and `sparkle` shimmer *on top of the current
colour*. `candle` and `fire` force a warm palette and `prism` crawls through hues — none of
those three can stay blue.

`fire` being unable to hold an arbitrary colour disqualifies it here but makes it precisely
right for the `failed` state, which wants a red flame.

> **Consequence found during live acceptance.** Under an effect, the bulb reports its own
> palette rather than the RGB you sent, so `light.hue_color_lamp_1`'s `rgb_color` does **not**
> read back the requested colour for `working` (`sparkle`) or `failed` (`fire`). This is bulb
> behaviour, not a pipeline fault — but it means API readback cannot verify the colour of an
> effect-driven state. Verify those by eye, or by asserting the `effect` attribute plus the
> service call that was sent.

## States

Four states, folded from the whole fleet:

| State | Lamp | Meaning |
|---|---|---|
| `idle` | off | nothing in flight |
| `working` | dim blue ~12%, `sparkle` | at least one agent is working |
| `blocked` | solid amber, ~35% | at least one agent is waiting on you |
| `failed` | red ~45%, `fire` | something broke; go restart it |
| landing | snap green → hold 15s → fade 15s | an agent just finished |

`blocked` is solid on purpose: nothing is actually moving, and a shimmer would imply
otherwise. `failed` moves again, deliberately — amber-solid and red-solid are weak
neighbours across a dim room, so red is distinguished by motion as well as hue. Priority is
`failed > blocked > working > idle`, and a landing is suppressed under both `failed` and
`blocked`: only one of those two things needs your hands.

`sparkle` was chosen over `opal` and `glisten` by comparing all three live on the lamp at
the real intended 12% brightness.

### Inherited limitation: `blocked` over-reports

flightdeck registers **exactly five hooks** and deliberately excludes `PreToolUse` /
`PostToolUse` ("they fire per tool call and would tax every agent action"). A consequence:
`Notification` sets a session to `blocked` when the permission prompt appears, but **no
event fires when you answer it**. The session's next event is `Stop`, at the end of the
turn. So a session that hit one permission prompt reads `blocked` for the entire remainder
of that turn, even while actively working.

This is flightdeck's behaviour, not Beacon's — the Stream Deck key over-reports amber in
exactly the same way. Beacon inherits it and **must not attempt to work around it** by
registering its own per-tool-call hooks; that would reintroduce the cost flightdeck
explicitly refused. If it proves annoying in use, the fix belongs upstream in flightdeck,
where both renderers would benefit.

Practical effect on the lamp: amber is a *floor*, not a precise signal. It means "at some
point this turn, an agent wanted you" — which is still actionable, and still better than
blue.

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

> **Changed during implementation.** `failed` (`#B42318`) was deferred when this was written,
> because emitting it needed a `StopFailure` hook probe in flightdeck. Mid-build, flightdeck
> shipped `bin/fleet-fail` (commit `661eb34`), which sets `state: "failed"` by hand from the
> Stream Deck and then calls `fleet-reconcile` — a path that reaches this renderer. That made
> the deferral actively harmful: the fold mapped unknown states to `idle`, so marking a session
> failed would have turned the lamp **off**, reading as "everything landed" at the moment
> something broke. `failed` was therefore built into v1 at the agreed treatment — red with
> Hue's native `fire` effect, priority `failed > blocked > working > idle`, landings suppressed
> under it. What remains deferred is only flightdeck detecting a died-on-an-API-error turn
> *automatically*; see "Deferred".

## Constraints inherited from flightdeck

`beacon-render` runs on flightdeck's execution paths, so flightdeck's global constraints
apply to it verbatim:

- **Python 3.9 syntax, standard library only.** No third-party packages, no venv. 3.9 is
  the floor because of `beacon-render` itself, not `fleet-reap`: `fleet-reap`'s own launchd
  plist explicitly invokes Homebrew's `python3.13`, so checking the plist and seeing 3.13
  will (wrongly) suggest this constraint can be relaxed. The real cause is one hop further
  down the chain — `bin/beacon-render`'s own `#!/usr/bin/env python3` shebang, resolved
  fresh when it is spawned as a subprocess of `fleet-reconcile`. Under launchd's inherited
  `PATH` (unlike an interactive shell's), `env python3` resolves to CommandLineTools'
  `/usr/bin/python3` (3.9.6), not Homebrew's 3.13. Avoid `match` and `X | Y` annotations.
- **Never write to stdout, always exit 0.** `beacon-render` is reachable from a Claude Code
  hook. A bug in it must never break a real agent. Errors go to a log file only.
- **All writes atomic** — temp file in the same directory, then `os.replace()`.
- **`subprocess` always takes a list, never `shell=True`.**

## Prerequisite: flightdeck must be installed

~~flightdeck is **built but not wired**~~ — **resolved during implementation.** When this was
written, `~/.fleet/` held only `fleet.log`, the launchd reaper was not loaded, and
`fleet-emit` was registered in no `settings.json`; installation was flightdeck's own Task 12
(`install.sh`, `fleet-doctor`), then unbuilt. That landed mid-build, so Beacon's live
acceptance was able to run.

The property that made the ordering not matter is worth keeping: because the seam is **JSON
on stdin**, every fold behaviour is reachable with a synthetic snapshot and no live agent at
all. Tasks 1-6 were built and fully tested before flightdeck was ever installed; only the
final acceptance depended on it.

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
fold(sessions, prev) -> (base, landed, next_prev, changed)

  failed   = any(s.state == "failed")
  blocked  = any(s.state == "blocked")
  working  = any(s.state == "working")
  done_set = {s.session_id for s in sessions if s.state == "done"}

  base    = "failed" if failed else "blocked" if blocked else "working" if working else "idle"
  landed  = bool(done_set - prev.done_set) and base not in ("blocked", "failed")
  changed = landed or base != prev.base
```

Two decisions are encoded here and both matter:

**`landed` is edge-triggered off a set difference.** In `fleet-emit`, `Stop` writes state
`done` and that file *persists* as `done` until the session's next event. So `done` is a
**level, not an edge**, while the green landing is inherently an edge ("a window just
finished"). Testing `done_set` for non-emptiness would re-fire green on every 15s reap tick
forever. Only a session *entering* the done set counts.

**`and base not in ("blocked", "failed")`** implements the agreed priority — amber and red
both outrank green. When some other agent is stuck on you, or something has broken, a
landing elsewhere does not steal the lamp, because those are the only two things that need
your hands.

Green must still fire while another session is `working` — that was the chosen concurrency
semantic: the lamp returns to blue after the fade if anything is still in flight, and to
off if that was the last one. The HA script handles that decay, since `base` is passed
alongside `landed`.

**Change detection.** State persists to `~/.beacon/last.json` (atomic write via temp file +
`os.replace`, mirroring `fleetlib.write_json_atomic`). Home Assistant is called only when
`base` changed **or** `landed` is true, so the 15s reap tick collapses to zero network
traffic whenever nothing has actually changed.

**The HA call is fully detached** — `subprocess.Popen(..., start_new_session=True)`, no
wait. This is load-bearing: the teardown chain is `SessionEnd → fleet-emit →
fleet-reconcile → beacon-render`, and **`SessionEnd` hooks share a 1.5-second budget**
across all hooks. Nothing on that path may block on the network.

`beacon-render` must exit 0 unconditionally, for the same reason `fleet-emit` does.

## 3 — The HA renderer

Two scripts in `scripts.yaml`, both `mode: restart`.

`script.beacon_render` is the entry point `beacon-render` calls. Fields: `base`
(`idle|working|blocked|failed`), `landed` (bool), `rgb` (the base state's colour) and
`rgb_done` (the landing flash's colour) — the last two are how the snapshot's palette rides
along on the service call rather than being hardcoded in HA.

```
1. input_boolean.beacon_enabled off  OR  input_boolean.sleeping on
     → light.turn_off, stop.
2. landed
     → green (rgb_done), transition 0, full brightness
     → delay 15s
     → script.beacon_paint(base, rgb, transition 15)
3. else script.beacon_paint(base, rgb, transition 2):
     failed  → red,    ~45%, `fire` effect
     blocked → amber,  ~35%, solid
     working → blue,   ~12%, shimmer (see §4)
     idle    → light.turn_off
```

`script.beacon_paint` is the shared base-state renderer, split out of `beacon_render`
because the landing branch has to fall through to *exactly* this logic after its 15s hold —
one renderer, two entry points, so the post-landing render can never drift from the
non-landing one.

`mode: restart` is the reason this timing lives in HA rather than in a backgrounded `sleep`
on the laptop: a new event arriving mid-fade cancels the pending fade cleanly and for free,
which is exactly the chosen semantic. It also survives the laptop closing.

`input_boolean.sleeping` already exists (set by Good Night, cleared by Good Morning). The
sleep gate means a late-night session cannot light the house.

`input_boolean.beacon_enabled` is **new** and must be added to `configuration.yaml`. Note
that unlike an `automations.yaml`-only change, this triggers a **full `ha core restart`** on
deploy.

## 4 — The shimmer

Native, no loop: `working` renders as dim blue with a Hue `effect` applied, and the bulb
holds the shimmer in hardware until told otherwise. This is why `working` needs no
`repeat/while` automation — unlike HA §51's red breathe, which predates effect support on
those bulbs and drives the pulse from HA with two commands per cycle.

`opal`, `glisten` and `sparkle` were compared live on the lamp at the real intended
brightness (12%), and **`sparkle` is the pinned choice**. `candle`, `fire` and `prism` are
unusable here — the first two force a warm palette, the third crawls through hues, and none
can stay blue.

Because the capability is now confirmed present, the script applies the effect directly
rather than guarding it behind a `choose` on `effect_list`. The one caveat is the reload
gotcha recorded under "The light": if a future firmware change makes HA drop the `effect`
bit again, `light.turn_on` will reject the effect and that single call fails. The renderer
should therefore mark the effect call `continue_on_error` so a rejected effect degrades to
solid colour rather than aborting the render.

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
`light.hue_color_lamp_1` is now present in `tests/fixtures/entities.txt` (it had to be
added via `scripts/refresh-entity-snapshot.sh` before `test_entity_references` would accept
it), so this is no longer a deploy blocker.

## 8 — Live acceptance results (Task 7, 2026-08-13)

`~/code/flightdeck/config/fleet.local.json` now carries `"renderers":
["/Users/pk/code/beacon/bin/beacon-render"]` and `~/.beacon/config.json` (mode `0600`) holds
the real `ha_url`/token. The merge was verified via `fleetlib.load_config()`.

**§2's `SessionEnd` budget claim, measured.** `time (cat sessionend.json | fleet-emit
SessionEnd)` end-to-end (`fleet-emit → fleet-reconcile → beacon-render`, dispatch itself
still detached) across 10 runs: **min 0.215s, max 0.438s, mean ≈0.32s**, against the shared
1.5s budget — roughly 3-4x headroom even at the slowest observed run. The concern in §2 does
not materialize in practice; three Python interpreter startups on this hardware cost well
under half the budget.

**Effect-driven states (`working`/`sparkle`, `failed`/`fire`) do not read back the requested
RGB.** Confirmed live: once the Hue effect is active, `rgb_color`/`brightness` reported by
`light.hue_color_lamp_1` reflect the *effect's own* palette, not the values the service call
passed — stable and repeatable for `sparkle`, mildly flickering (215-255) for `fire`. This
matches scripts.yaml's own comment on `fire` ("forces its own warm palette and cannot hold
an arbitrary colour"), so it is bulb behaviour, not a pipeline defect — but it means
`rgb_color` is not a meaningful assertion for those two states; `effect` is the reliable
signal. Static states (`blocked`, the green landing, `idle`/off) reported colour close to but
not pixel-identical to the requested value (Hue's RGB→xy→RGB round trip through the bridge),
which is expected gamut rounding.

**Confound discovered during testing, worth flagging for any future live-poke session:**
this task's own Claude Code session, and flightdeck's install, mean *this very session* is
itself one of the live sessions flightdeck tracks. Once the renderer was registered (§3
above), any real `SessionStart`/`UserPromptSubmit`/`Notification`/`Stop`/`SessionEnd` firing
on this session or on any other live session races with and can overwrite synthetic
stdin-piped tests against `beacon-render`, because both paths share `~/.beacon/last.json` and
the same lamp. Worked around by temporarily setting `"renderers": []` while driving the
Step-4 synthetic snapshots directly through `bin/beacon-render` (which bypasses
`fleet-reconcile` entirely and needs no such isolation), then restoring the real renderer
list before the Step-6 timing measurement, which specifically needed the full live chain.

**Separate concern, NOT caused by this task and NOT resolved by it:** during Step 6,
`~/.fleet/sessions/P1.json` .. `P6.json` appeared (repos `alpha`..`foxtrot`, one `state:
"failed"`) — clearly a manual/concurrent probe of the Stream Deck overflow indicator by
someone else, not a bats fixture (no isolation leak; `reconcile.bats`/`emit.bats` properly
scope `FLEET_HOME` to a tmpdir) and not created by this task. All six carry `"pid": 0`, and
`fleet-reap` is deliberately conservative about pid 0 ("unknown is never reaped" —
`bin/fleet-reap`), so **these will not self-clean** and will keep folding the real fleet to
`base: "failed"` for as long as they exist. They did not light the lamp during this task only
because `input_boolean.beacon_enabled` was off at the time they were folded in. The moment
someone flips that toggle on, the lamp will show red/fire immediately and misleadingly until
`~/.fleet/sessions/P{1..6}.json` are removed. Left untouched here deliberately — they look
like another session's in-progress work, not abandoned junk, so deleting them was judged
out of scope for this task. Flagged for the human to clear before relying on the `failed`
state reading true. **Update:** by the end of this task the six files were gone again
(`~/.fleet/sessions/` back to just the two real sessions) — whoever created them cleaned up
after themselves, confirming this was transient concurrent activity rather than abandoned
state. No action was needed after all, but the race window above (a stray `failed`/`blocked`
session able to light the real lamp the instant `beacon_enabled` goes on) is real and worth
remembering for next time.

## Deferred

### `failed` — red flame (built)

A fifth state meaning **"come back to the terminal and restart something."** Agreed in
design, originally deliberately out of v1 so it did not gate the first working lamp — but
**`failed` WAS built into v1** once `bin/fleet-fail` shipped mid-build (see "Changed during
implementation" under Palette, above). Only *automatic* detection of a died-on-an-API-error
turn (`StopFailure`) remains deferred; see "Known gap" below. What follows is the treatment
as built, kept here rather than moved up because the surrounding narrative (why it was
agreed, why the deferral briefly existed) is still useful context.

**Treatment: red flame.** Hue's native `fire` effect on red. `fire` was disqualified for
`working` precisely because it forces its own warm palette and cannot hold blue — which is
exactly what makes it right here. The bulb renders a real flicker in hardware, so no loop
is needed. **Confirmed available** on Beacon as of the 2026-08-13 firmware update, and
previewed live on the lamp, so this state carries no firmware prerequisite — only the
flightdeck work below.

Distinguishing it from `blocked` by **motion** as well as hue is the point — amber solid
and red solid are weak neighbours across a dim room. There is house precedent for red
motion meaning trouble: HA §51 "Night Lights Red Beacon" breathes slow red while the alarm
is triggered. A flame is distinct from that breathe, so the two do not collide.

**Semantics (as built):**

- A **level, not an edge** — unlike the green landing. Red persists until dealt with,
  cleared by the next `UserPromptSubmit` (→ `working`) or `SessionEnd` (→ gone).
- Priority becomes `failed > blocked > working > idle`. A landing is suppressed under red
  exactly as it is under amber: `landed = ... and base not in ("blocked", "failed")`.
- **Respects the sleep gate like everything else.** `input_boolean.sleeping` on → lamp off,
  no exceptions. A turn that died at 3am is still dead at 7am, and will show red the moment
  Good Morning clears the flag. Nothing wakes the house.

**What's still deferred: automatic detection.** `bin/fleet-fail` sets `state: "failed"` by
hand, from the Stream Deck. Nothing yet sets it *automatically* when a turn dies on an API
error. Prerequisites for that, both outside this repo:

1. **flightdeck must emit it.** `fleet-emit`'s `EVENT_STATES` maps five events;
   `StopFailure` is not one of them. Adding it would also give the Stream Deck red keys
   automatically, using the `#B42318` already sitting unused in `fleet.json`.
2. **`StopFailure` must be verified empirically first.** `docs/hook-contract.md` confirmed
   five events against CLI v2.1.232; `StopFailure` was not among them. The published docs
   describe it, but those same docs disagree with the observed payloads on two field names
   (`start_reason` vs the observed `source`; `end_reason` vs the observed `reason`). Probe
   it under the existing methodology before depending on it.

**Known gap.** `StopFailure` catches a turn dying on an API error, not a Claude Code
process that crashes outright — `fleet-reap` removes that session and the lamp goes quietly
off. That is arguably the case most deserving of "restart something", but surfacing it
means revisiting flightdeck's reap philosophy (a dead session currently vanishes), so it is
left alone here and recorded as an open question.

### Other

- A watchdog for the laptop-sleep case.
- Per-session or per-room output (more than one lamp).
- Distinguishing "Claude asked you a question and stopped" from "work landed" — both
  currently produce `Stop` → green.
