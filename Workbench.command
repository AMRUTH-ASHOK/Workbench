#!/bin/zsh
cd "${0:A:h}"
if ! curl --silent --fail http://127.0.0.1:4318/api/health > /dev/null; then
  .venv/bin/python -m workbench install-service || exit 1
fi
open http://127.0.0.1:4318
