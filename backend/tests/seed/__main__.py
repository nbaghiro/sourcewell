"""CLI: rebuild the demo dataset. Run with `make seed` or `python -m tests.seed`."""

import asyncio
import os

# Never hit a real SMTP server while seeding.
os.environ.setdefault("EMAIL_DRY_RUN", "1")

import app.models  # noqa: F401  (register every ORM table)
from app.core.db import SessionLocal
from tests.seed.builder import seed_demo


async def _run() -> None:
    async with SessionLocal() as session:
        summary = await seed_demo(session, reset=True)
        await session.commit()
    print("[seed] demo data rebuilt:", summary)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
