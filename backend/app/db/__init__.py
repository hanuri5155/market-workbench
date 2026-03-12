# DB package (engine/session) for the Market Workbench FastAPI app
#
# Compatibility:
#   - old code used `from app.db import SessionLocal`
#   - this package re-exports engine/SessionLocal from .session

from .session import engine, SessionLocal  # noqa: F401
