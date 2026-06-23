"""Background worker: poll for due enrollments and tick them. Run with `make worker`."""

import asyncio
from datetime import UTC, datetime

import app.models  # noqa: F401  (register every ORM table so FK resolution works on flush)
from app.core.db import SessionLocal
from app.core.logging import configure_logging, logger
from app.runtime.engine import run_due

_POLL_SECONDS = 10


async def _loop() -> None:
    while True:
        async with SessionLocal() as session:
            result = await run_due(session, now=datetime.now(UTC))
            await session.commit()
        if result["processed"]:
            logger.info("worker ticked %s enrollment(s)", result["processed"])
        await asyncio.sleep(_POLL_SECONDS)


def main() -> None:
    configure_logging()
    logger.info("sourcewell runtime worker started")
    asyncio.run(_loop())


if __name__ == "__main__":
    main()
