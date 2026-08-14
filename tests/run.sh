#!/usr/bin/env bash
# Single entry point: compile-check Python, run unittest, then run bats.
#
# Two runners on purpose. unittest covers the pure layer (fold(), transport
# builders, atomic writes) where table-driven cases belong. bats covers the
# process boundary (exit codes, stdout silence, stdin piping, argv) which
# unittest could only reach through subprocess.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
rc=0

printf '== python syntax ==\n'
if python3 -m compileall -q "$ROOT/bin" >/dev/null 2>&1; then
  printf 'clean\n'
else
  python3 -m compileall -q "$ROOT/bin"
  rc=1
fi

printf '\n== unittest ==\n'
if ( cd "$ROOT" && python3 -m unittest discover -s tests -t . -p 'test_*.py' ); then
  :
else
  rc=1
fi

printf '\n== bats ==\n'
if compgen -G "$ROOT/tests/*.bats" > /dev/null; then
  bats "$ROOT"/tests/*.bats || rc=1
else
  printf 'no bats files yet\n'
fi

exit "$rc"
