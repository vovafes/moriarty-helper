#!/bin/sh
# Run every test module: sh tests/run_all.sh
cd "$(dirname "$0")/.." || exit 1
PY=${PYTHON:-venv/bin/python}
fail=0
for f in tests/test_*.py; do
  name=$(basename "$f" .py)
  "$PY" -m "tests.$name" || { echo "FAILED: $name"; fail=1; }
done
exit $fail
