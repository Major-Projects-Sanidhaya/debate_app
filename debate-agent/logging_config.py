import logging
import sys

import structlog


def configure_logging(configure_stdlib: bool = True) -> None:
    """Configure structlog (JSON to stdout) and, optionally, stdlib logging.

    Pass configure_stdlib=False when something else already owns the stdlib
    root logger — livekit-agents installs its own JSON handler in `start`
    mode, and adding a second handler prints every library record twice.
    """
    if configure_stdlib:
        logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
