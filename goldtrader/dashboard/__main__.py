"""Entry point: ``python -m goldtrader.dashboard``.

Binds to 127.0.0.1 only. The supervisor's run scripts launch this as a detached
process so it survives a supervisor crash and can render the system as "down".
"""
from __future__ import annotations

from ..config import get_settings
from ..logging_setup import get_logger, setup_logging

log = get_logger("goldtrader.dashboard")


def main() -> None:
    setup_logging()
    s = get_settings()
    if not s.dashboard_enabled:
        log.warning("dashboard_disabled", hint="set DASHBOARD_ENABLED=true to enable")
        return

    import uvicorn

    from .server import create_app

    log.info("dashboard_starting", host=s.dashboard_host, port=s.dashboard_port)
    uvicorn.run(
        create_app(),
        host=s.dashboard_host,
        port=s.dashboard_port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
