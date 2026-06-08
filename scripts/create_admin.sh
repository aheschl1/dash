#!/bin/bash
# Create or update the admindash admin user (stored hashed in Postgres).
# The admindash container must be running.
#
# Usage:
#   bash scripts/create_admin.sh                          # interactive, hidden password
#   ADMIN_USER=name ADMIN_PASS=secret bash scripts/create_admin.sh
#
# Re-running with an existing username updates that user's password.

set -euo pipefail

USER_NAME="${ADMIN_USER:-}"
PASS="${ADMIN_PASS:-}"

if [[ -z "$USER_NAME" ]]; then
    read -rp "Username: " USER_NAME
fi
if [[ -z "$PASS" ]]; then
    read -rsp "Password: " PASS; echo
    read -rsp "Confirm:  " PASS2; echo
    [[ "$PASS" == "$PASS2" ]] || { echo "Passwords do not match" >&2; exit 1; }
fi

[[ -n "$USER_NAME" && -n "$PASS" ]] || { echo "username and password required" >&2; exit 1; }
[[ ${#PASS} -ge 8 ]] || { echo "password must be at least 8 characters" >&2; exit 1; }

docker exec -e ADMIN_U="$USER_NAME" -e ADMIN_P="$PASS" -w /app/backend admindash \
    /app/backend/.venv/bin/python -c '
import os, db
db.create_user(os.environ["ADMIN_U"], os.environ["ADMIN_P"], "admin")
print("admin user", repr(os.environ["ADMIN_U"]), "created/updated")
'
