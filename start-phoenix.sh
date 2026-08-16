#!/bin/sh

# Thin wrapper kept for muscle memory and for running the collector in the
# foreground. Everything about how Phoenix starts — release, port, directory,
# retention — lives in src/oris/phoenix.py, so this script has no settings of
# its own to drift from ORIS's. It used to restate ORIS_HOME's default in its
# own words, with a comment admitting the two had to be kept in step by hand.
#
# For a collector that survives this terminal, use:
#     uv run orisctl phoenix install

set -eu

project_root=$(CDPATH= cd "$(dirname "$0")" && pwd)
exec "$project_root/.venv/bin/oris-phoenix"
