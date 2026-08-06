"""The M0 no-op task. Proves the broker round-trip, nothing more."""

from workers.app import app


@app.task(name="workers.tasks.health.ping")
def ping() -> str:
    return "pong"
