#!/usr/bin/env bash
set -euo pipefail
printf 'PRE_APP_ALPHA\nPRE_APP_BETA\n'
exec bb repl "$@"
