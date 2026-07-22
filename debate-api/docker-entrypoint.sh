#!/bin/sh
# Container entrypoint: validate config, migrate, seed, then serve.
#
# SINGLE-REPLICA ASSUMPTION: migrations run here, on every container start.
# That is safe for one replica and for rolling restarts of one replica, but
# with >1 replica two containers can run `alembic upgrade head` concurrently.
# Before scaling out, move the migrate+seed lines to a Railway pre-deploy
# command and leave only the exec line here (see DEPLOY.md).
set -e

# Fail fast on production misconfiguration — before touching the database.
# configure_logging() first so the fatal lines are JSON like everything else.
python -c "
from app.logging_config import configure_logging
from app.config import get_settings
configure_logging()
get_settings().enforce_production_guards()
"

echo "running migrations..."
alembic upgrade head

echo "seeding topics..."
python scripts/seed.py

echo "starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
