# Brand

Beacon borrows flightdeck's **Vector Eight / Beacon** identity wholesale, from
`flightdeck/assets/brand/`. The mark is unchanged: a white airframe, a blue
command path, and an amber operator-attention node on a near-black console
field. The amber node *is* this repo — it is the lamp.

- `beacon-mark.svg` — the master mark, transparent (flightdeck's `flightdeck-mark.svg`)
- `beacon-favicon.svg` — the mark on a rounded near-black field (flightdeck's avatar geometry)

Upstream is the source of truth. If flightdeck's mark changes, re-copy rather
than editing here.

## Palette

| Token | Hex | Use |
|---|---|---|
| Night | `#020304` | page field |
| Mark interior | `#0C131D` | mark fill, code blocks |
| Airframe | `#EEF5FF` | primary text |
| Command | `#1256A3` | active command path, rules, `working` |
| Command light | `#4C9AE8` | link text — see below |
| Attention | `#F5A623` | operator attention, `blocked` |
| Secondary text | `#617083` | letterspaced labels |

Command `#1256A3` is 2.8:1 on Night — below AA for body text, so link text uses
the lighter tint and the true Command is kept for structure (rules, the mark,
the underline under a hovered link).

## Rules, inherited

Amber means operator attention; never decorative. Blue is the active command
path. The mark stays flat and geometric, and carries no glow — the brand allows
glow only as a temporary startup effect, which a static web page never is.
