#!/usr/bin/env bash
# Plane Over Me: one-step start for Mac, Linux and Raspberry Pi.
# First run: creates a private Python environment, installs what's needed,
# asks for your location and downloads aircraft data and logos.
set -e
cd "$(dirname "$0")"
export PYTHONUNBUFFERED=1

PY=""
for cmd in python3 python; do
  if command -v "$cmd" >/dev/null 2>&1 && "$cmd" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    PY="$cmd"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 or newer is needed. Get it from https://www.python.org/downloads/"
  echo "(On a Mac, typing python3 in Terminal also offers to install it.)"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First start: setting things up (about a minute) ..."
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
exec .venv/bin/python -m planeoverme start "$@"
