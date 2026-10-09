"""Write one QR PNG per dining table to scripts/qr/.

Run from api/:  uv run python ../scripts/gen_qr.py [--base-url http://localhost:5173]
"""

import argparse
import asyncio
import sys
from pathlib import Path

import qrcode
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from app.db import make_engine  # noqa: E402
from app.models import DiningTable  # noqa: E402

OUT_DIR = Path(__file__).parent / "qr"


async def main(base_url: str) -> None:
    engine = make_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(DiningTable.label, DiningTable.qr_token).order_by(DiningTable.id)
            )
        ).all()
    await engine.dispose()
    OUT_DIR.mkdir(exist_ok=True)
    for label, token in rows:
        url = f"{base_url.rstrip('/')}/t/{token}"
        qrcode.make(url).save(OUT_DIR / f"{label}.png")
        sys.stdout.write(f"{label}: {url}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:5173")
    asyncio.run(main(ap.parse_args().base_url))
