#!/usr/bin/env bats

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  BIN="$ROOT/bin"
  export BEACON_HOME="$BATS_TEST_TMPDIR/beacon"
  export BEACON_DRY_RUN="$BATS_TEST_TMPDIR/dispatched.jsonl"
  mkdir -p "$BEACON_HOME"
  export -f snap dispatched last_dispatch
}

snap() {
  printf '{"ts":1,"sessions":%s,"states":{"working":{"color":"#1256A3"}}}' "$1"
}

dispatched() {
  [ -f "$BEACON_DRY_RUN" ] || { echo 0; return; }
  wc -l < "$BEACON_DRY_RUN" | tr -d ' '
}

last_dispatch() {
  tail -n 1 "$BEACON_DRY_RUN"
}

@test "a working session dispatches working" {
  run bash -c "snap '[{\"session_id\":\"A\",\"state\":\"working\"}]' | '$BIN/beacon-render'"
  [ "$status" -eq 0 ]
  [ "$(dispatched)" = "1" ]
  [[ "$(last_dispatch)" == *'"base":"working"'* ]]
  [[ "$(last_dispatch)" == *'"landed":false'* ]]
}

@test "NO-OP SUPPRESSION: an unchanged snapshot dispatches exactly once" {
  s='[{"session_id":"A","state":"working"}]'
  snap "$s" | "$BIN/beacon-render"
  snap "$s" | "$BIN/beacon-render"
  snap "$s" | "$BIN/beacon-render"
  [ "$(dispatched)" = "1" ]
}

@test "a landing dispatches landed=true exactly once" {
  s='[{"session_id":"A","state":"done"}]'
  snap "$s" | "$BIN/beacon-render"
  [ "$(dispatched)" = "1" ]
  [[ "$(last_dispatch)" == *'"landed":true'* ]]
  snap "$s" | "$BIN/beacon-render"
  [ "$(dispatched)" = "1" ]
}

@test "state persists to last.json between invocations" {
  snap '[{"session_id":"A","state":"done"}]' | "$BIN/beacon-render"
  run python3 -c "
import json
d = json.load(open('$BEACON_HOME/last.json'))
print(d['base'], d['done'])
"
  [ "$output" = "idle ['A']" ]
}

@test "EXIT 0 GUARANTEE: malformed stdin never fails and never dispatches" {
  run bash -c "printf 'not json{' | '$BIN/beacon-render'"
  [ "$status" -eq 0 ]
  [ "$(dispatched)" = "0" ]
}

@test "EXIT 0 GUARANTEE: empty stdin never fails and never dispatches" {
  run bash -c "printf '' | '$BIN/beacon-render'"
  [ "$status" -eq 0 ]
  [ "$(dispatched)" = "0" ]
}

@test "EXIT 0 GUARANTEE: a snapshot that is a list, not an object, never fails" {
  run bash -c "printf '[1,2,3]' | '$BIN/beacon-render'"
  [ "$status" -eq 0 ]
  [ "$(dispatched)" = "0" ]
}

@test "EXIT 0 GUARANTEE: an unwritable BEACON_HOME never fails" {
  export BEACON_HOME=/dev/null/nope
  run bash -c "snap '[{\"session_id\":\"A\",\"state\":\"working\"}]' | '$BIN/beacon-render'"
  [ "$status" -eq 0 ]
}

@test "SILENT ON STDOUT: it is on a hook path, so it must print nothing" {
  run bash -c "snap '[{\"session_id\":\"A\",\"state\":\"working\"}]' | '$BIN/beacon-render'"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a corrupt last.json is treated as no previous state, not a crash" {
  echo 'garbage{' > "$BEACON_HOME/last.json"
  run bash -c "snap '[{\"session_id\":\"A\",\"state\":\"working\"}]' | '$BIN/beacon-render'"
  [ "$status" -eq 0 ]
  [ "$(dispatched)" = "1" ]
}
