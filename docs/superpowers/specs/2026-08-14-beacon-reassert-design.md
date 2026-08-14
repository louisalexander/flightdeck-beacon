# Beacon — Re-assert on Demand — Design

**Date:** 2026-08-14
**Status:** Built as `4805279` in the `homeassistant` repo. All tiers pass. **Not deployed** —
the push restarts the live house.
**Extends:** `docs/superpowers/specs/2026-08-13-beacon-design.md`
**Repos touched:** `homeassistant` only. No change to `beacon` or `flightdeck`.

## Problem

Beacon is **edge-triggered and nothing else**. `bin/beacon-render:111` returns early on
`not changed`, so a call reaches Home Assistant only when the folded fleet state
*transitions*. There is no path anywhere in the system that says "paint the current state
now."

That was a deliberate v1 economy — it keeps the `SessionEnd` hook cheap and avoids a
call every 15s forever. But it means the lamp's correctness depends on nobody and nothing
disturbing it between transitions, and three ordinary events disturb it:

| Event | What happens today |
|---|---|
| You turn the lamp on by hand | Bulb comes up at its own default — **warm white** — and stays there indefinitely |
| HA restarts, updates, or reloads scripts | Lamp is blanked and stays dark until the fleet genuinely transitions |
| `sleeping` clears in the morning, or `beacon_enabled` is switched back on | Gate opens, but nothing repaints; lamp stays dark |

All three were observed live on 2026-08-13/14. The third is the one that actually cost a
morning: `input_boolean.sleeping` was still `on` at 10:53am, Beacon's first branch
suppressed everything, and the symptom was indistinguishable from "Beacon is broken."

### A false claim now in the deployed code

The `Beacon — Off on HA Start` automation shipped in `c647a76` justifies blanking the lamp
in its own comment:

> "if the fleet is actually busy, flightdeck's reaper tick re-asserts the correct state
> within 15s anyway, so there is no real downside to blanking it first."

**That is not true.** The reaper does invoke `fleet-reconcile` every 15s, and that does
reach `beacon-render` — but `beacon-render` computes `changed = landed or base != prev_base`
(`bin/beaconlib.py:131`) and returns without dispatching when it is false. A busy fleet
whose state has not changed produces no call. The lamp stays dark.

The automation is not wrong to fire; its *action* is wrong. Blanking was only ever a
stand-in for the re-assert this document specifies.

## Decision: Beacon is a strict indicator

Turning the lamp on by hand while the fleet is `idle` **snaps it back off**.

The alternative — leave a manually-lit lamp alone so it can serve as an ordinary room lamp —
was considered and rejected. `light.hue_color_lamp_1` is named "Beacon" and exists to answer
one question at a glance. A lamp that is sometimes an indicator and sometimes furniture
answers that question ambiguously, and the ambiguity lands exactly when you most need to
trust it: a lit lamp would no longer definitely mean "something is in flight."

The cost is real and accepted: **you cannot use this bulb as a room lamp while Beacon is
enabled.** The escape hatch is `input_boolean.beacon_enabled` → off, which `c647a76` already
made take effect immediately.

## Approach

The freshest fleet truth lives on the Mac, but nothing outside the Mac can ask it for that
truth — there is no inbound path, and adding a listener to a laptop that sleeps is a worse
problem than the one being solved. So Home Assistant keeps its own copy.

`script.beacon_render` already receives `base` and `rgb` on every push. Mirroring them into
two helpers costs one action and makes HA independently capable of repainting, with no
involvement from the Mac at paint time. **This is why the whole change is HA-side.**

### Rejected: periodic re-assert from the Mac

Dropping the `not changed` early-return, or re-dispatching on a throttle, would self-heal
every failure mode above without any HA changes. It was rejected on one specific behaviour:
it makes the lamp **fight you**. Turn it off because you want the room dark, and 15 seconds
later it comes back on. Re-asserting on an explicit *turn-on* respects the same signal a
periodic push would trample.

It also pushes a call every 15s forever over the Nabu Casa relay (see "Adjacent findings"),
which is the wrong direction on a path already budgeted in milliseconds.

## Components

### 1. The mirror — `configuration.yaml`, `scripts.yaml`

Two `input_text` helpers, following the pattern already established at
`configuration.yaml:369`:

| Helper | Holds | Example |
|---|---|---|
| `input_text.beacon_base` | last folded state | `working` |
| `input_text.beacon_rgb` | last base RGB, comma-separated | `18,86,163` |

`script.beacon_render` writes both as its **first** action, before the `choose:` block.

Two properties of that placement are load-bearing:

- **Before the `choose:`** means the mirror records what the *fleet* is doing, not what the
  *lamp* is doing. When `beacon_enabled` is off or the house is asleep, Beacon still pushes
  and the mirror still updates — so the moment the gate opens there is a correct state to
  paint. This is what fixes the stuck-`sleeping` morning.
- **`landed` and `rgb_done` are deliberately not mirrored.** A landing is an *edge*, not a
  state. Mirroring it would replay the green flash every time you touched the light switch,
  announcing an agent finishing that finished hours ago.

### 2. The re-assert — `automations.yaml`

One new automation, `Beacon — Re-assert From Mirror`, with four triggers:

| Trigger | Closes |
|---|---|
| `light.hue_color_lamp_1` `off` → `on`, manual only | Manual turn-on renders warm white |
| `homeassistant` `start` (after a settle delay) | Restart/update/`script.reload` strands the lamp |
| `input_boolean.beacon_enabled` → `on` | Re-enabling does not repaint |
| `input_boolean.sleeping` → `off` | Good Morning does not repaint |

Action for all four: call `script.beacon_paint` with `base` and `rgb` read back from the
mirror, plus `transition: 2`.

Because the strict-indicator decision means idle → off, `beacon_paint`'s existing `default:`
branch (`light.turn_off`) already does the right thing. **No new branch in `beacon_paint`.**

> **Corrected during implementation.** This section originally specified `landed: false`.
> `beacon_paint` has no `landed` field — its signature is `base` / `rgb` / `transition`.
> The landing lives entirely in `beacon_render`, which is the correct split: `beacon_paint`
> paints steady states only, so there is no edge for the re-assert to accidentally replay.

#### The automation must check the gates itself

`script.beacon_paint` has **no master-switch gate** — only `beacon_render` does. So the
automation conditions on `beacon_enabled` being `on` and `sleeping` being `off` before it
paints anything.

Without that check, turning the lamp on by hand while Beacon was disabled would repaint it,
which breaks the escape hatch the strict-indicator decision depends on. With it, a manual
turn-on while disabled leaves the lamp alone — it is an ordinary room lamp again, which is
exactly what "disabled" is supposed to mean.

Both gate checks sit in the **actions**, after the start delay, not in the `conditions:`
block. Conditions are evaluated before any action runs, so gating up there would read the
helpers mid-restore and fail on precisely the restart this is meant to recover from.

#### Loop guard

`beacon_paint` turns the light on. That is a light turning on, which is this automation's
own trigger. Two independent guards, either sufficient:

1. **Context.** Condition on `trigger.to_state.context.parent_id is none`. A human toggle —
   HA UI, the Hue app, a wall switch via the bridge — carries no parent context. Anything
   `beacon_paint` does carries a parent chaining back to the script.
2. **Transition.** Trigger on `from: "off"` `to: "on"`, not bare `to: "on"`. `beacon_paint`
   painting a lamp that is already on produces no `off` → `on` edge, and an attribute-only
   change (colour, brightness) cannot trigger it either.

Both are specified. Guard 1 is the correctness argument; guard 2 also stops attribute churn
from firing the automation needlessly.

#### The start delay

`homeassistant.start` fires before helper state is necessarily restored. The automation
waits 10 seconds for the helpers to settle before reading them, and aborts the re-assert if
`input_text.beacon_base` is still `unknown`/`unavailable` — painting from an unrestored
mirror is worse than leaving the lamp as-is for one more transition.

### 3. Retire `Beacon — Off on HA Start`

Superseded by the `homeassistant` `start` trigger above, which repaints the *correct* state
instead of blanking. Strictly better on the case it was written for: an interrupted landing
stranded at 100% green now resolves to the true base state rather than to off.

`Beacon — Off on Disable or Sleep` **stays** — it handles the off direction, and the new
automation handles the on direction. Together they make both switches immediate in both
directions.

### 4. Bundled: green landing 70% — `scripts.yaml`

Unrelated to re-assert, bundled because every deploy costs a full `ha core restart`.

`script.beacon_render`'s `landed` branch drops `brightness_pct` from `100` to `70`. 100%
was chosen so the landing would be unmissable across the house; that reasoning holds, but
the level is too high for a lamp at desk distance. 70% keeps it emphatically the loudest
thing the system does while staying clear of `failed` at 45%, so the landing still reads as
a distinct event rather than a brighter red.

## Testing

The `homeassistant` repo's existing tiers, all of which must pass: `yamllint`, `pytest`
(168 at time of writing), and containerized `check_config`.

`input_text.beacon_base` and `input_text.beacon_rgb` do not exist on the live instance at
deploy time, so `test_entity_references` would fail on the references from `automations.yaml`.
They go in `tests/fixtures/entities_allow.txt` as forward references, with a `make snapshot`
after deploy to move them into the real snapshot — the mechanism the v1 build used for
`input_boolean.beacon_enabled`, and the same pass that will finally drop the stale
`scene.beacon_restore` line.

### Live acceptance

Automated tiers cannot prove any of this; every case ends in "look at the lamp."

1. Fleet working, lamp off → turn on by hand → **pale blue-lavender, opal**, not warm white.
2. Fleet idle, lamp off → turn on by hand → **snaps back off** within ~1s.
3. Fleet working → `beacon_enabled` off (lamp goes dark) → back on → **blue returns immediately.**
4. Fleet working → `sleeping` on (dark) → `sleeping` off → **blue returns immediately.**
5. Fleet working → restart HA → **blue returns**, not darkness.
6. Land an agent → confirm 70% green still reads as unmistakably an event.
7. Watch for 2 minutes with the fleet steady → **the lamp does not flicker or re-paint**. This
   is the loop-guard test; a context-guard failure shows up here as a pulsing lamp.

## Failure modes

Extends §5 of the v1 spec.

| Failure | Behaviour | Verdict |
|---|---|---|
| Mirror stale from laptop sleep | Lamp paints the last known state, which may be hours old | **Inherited, not new** — same root cause as "laptop sleeps mid-task" in v1 |
| Mirror unrestored at HA start | Re-assert aborts; lamp waits for the next real transition | Designed for; fails toward inaction |
| Loop between automation and `beacon_paint` | Prevented by two independent guards | Covered by acceptance case 7 |
| Manual turn-on while `beacon_enabled` off | Automation does not fire; the lamp stays on as an ordinary room lamp | **Intended** — this *is* the escape hatch |

The mirror is worth stating plainly: it is only ever as fresh as Beacon's last push. It
introduces no new staleness, because Beacon pushes on every transition — but it faithfully
reproduces any staleness that already exists.

## Adjacent findings — not in scope

Recorded here because both were found while diagnosing this and neither has a ticket.

**`input_boolean.sleeping` was stuck `on` at 10:53am on 2026-08-14.** Beacon's first branch
gates on it, so the lamp was suppressed all morning and looked broken. The re-assert design
makes the *recovery* automatic once the flag clears, but it does not address why Good Morning
failed to clear it. That belongs in the `homeassistant` repo as its own investigation.

**`~/.beacon/config.json` points at the Nabu Casa cloud relay**
(`https://…ui.nabu.casa`), not the LAN address. Every lamp update round-trips the public
internet on a path budgeted at 1.5s for `SessionEnd`. `~/.beacon/beacon.log` holds six
failures from 2026-08-13 — four 5-second timeouts and two `Empty reply from server`. Pointing
at the local address would be faster and would not fail when the relay hiccups. One-line
config change, no code, no deploy.

## Deploy

One `ha core restart`, because `scripts.yaml` and `configuration.yaml` are both outside the
hot-reload path. Push is a stop-and-ask: it restarts the live house.
