#!/usr/bin/env bash
# Uloží zmeny v state/ späť do repozitára (s opakovaním pri súbehu).
set -euo pipefail
git add -A state/
if git diff --cached --quiet; then echo "Stav bez zmeny."; exit 0; fi
git commit -m "state: $1 [skip ci]"
for i in 1 2 3 4 5; do
  git pull --rebase origin "${GITHUB_REF_NAME:-main}" && git push && exit 0
  sleep $((i * 5))
done
echo "Push stavu zlyhal"; exit 1
