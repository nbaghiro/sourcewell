"""Structured-ish logging setup. Single stdout handler; safe to call more than once."""

import logging
import sys

logger = logging.getLogger("sourcewell")


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s :: %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    root.addHandler(handler)
    root.setLevel(level)
