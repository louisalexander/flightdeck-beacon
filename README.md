# flightdeck-beacon

Paints [flightdeck](https://github.com/louisalexander/flightdeck) agent-fleet state onto a
Philips Hue lamp through Home Assistant.

| Lamp | Meaning |
|---|---|
| off | nothing in flight |
| dim blue, sparkling | at least one agent is working |
| solid amber | at least one agent is waiting on you |
| green, then a slow fade | an agent just finished |

Beacon tracks nothing itself. It is a renderer: `fleet-reconcile` pipes a fleet snapshot to
`bin/beacon-render` on stdin, which folds it to one state and calls a Home Assistant script.

## Install

1. `cp config.example.json ~/.beacon/config.json`, fill in your Home Assistant URL and a
   long-lived access token, then `chmod 600 ~/.beacon/config.json`.
2. Add this repo's renderer to flightdeck's `config/fleet.json`:
   `"renderers": ["/Users/pk/code/beacon/bin/beacon-render"]`
3. Deploy `script.beacon_render` and `input_boolean.beacon_enabled` from the
   `homeassistant` repo.

## Test

`./tests/run.sh` runs everything: `compileall`, `unittest` (the pure layer — `fold()`,
transport builders, atomic writes), then `bats` (the process boundary — exit codes, stdout
silence, argv inspection). Needs `bats`; everything else is stdlib. No network, no Home
Assistant, and no live agents required.

## Design

`docs/superpowers/specs/2026-08-13-beacon-design.md`
