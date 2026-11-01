#!/bin/sh
exec python -B "$(dirname "$0")/embedded-api.py" "$@"
