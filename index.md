---
layout: default
title: Beacon
description: >-
  An ambient lamp for your agent fleet: folds a flightdeck fleet snapshot into
  one light state — working, waiting on you, or just finished — and paints it
  onto a Philips Hue bulb through Home Assistant.
---

{% comment %}
The page body IS the README, pulled in at build time. One source of truth: edit
README.md and the site follows. Its H1 is dropped because the hero already is
the title. README.md carries no Liquid — keep it that way, or this include will
try to execute it.
{% endcomment %}
{% capture readme %}{% include_relative README.md %}{% endcapture %}
{{ readme | remove_first: "# flightdeck-beacon" | markdownify }}
