"""Moderation CLI. Run: python -m scripts.moderate <command> [args]

Commands:
  list-reports [--limit N]     recent user reports
  list-events [--limit N]      recent internal moderation events
  list-flagged                 users with flagged=true
  show-transcript <match_id>   transcript:{match_id} from Redis (agent mirror)
  ban <user_id> / unban <user_id>
"""

import argparse
import asyncio
import json
import sys

import redis.asyncio as aioredis
from sqlalchemy import select, update

from app.config import get_settings
from app.db import make_engine_and_sessionmaker
from app.models import ModerationEvent, Report, User


def fmt_ts(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "-"


async def list_reports(session, limit: int) -> None:
    rows = (
        await session.scalars(select(Report).order_by(Report.created_at.desc()).limit(limit))
    ).all()
    if not rows:
        print("no reports")
        return
    for r in rows:
        print(
            f"{fmt_ts(r.created_at)}  {r.reason:<16} match={r.match_id}\n"
            f"    reporter={r.reporter_id}  reported={r.reported_id}"
            + (f"\n    details: {r.details}" if r.details else "")
        )


async def list_events(session, limit: int) -> None:
    rows = (
        await session.scalars(
            select(ModerationEvent).order_by(ModerationEvent.created_at.desc()).limit(limit)
        )
    ).all()
    if not rows:
        print("no moderation events")
        return
    for e in rows:
        print(
            f"{fmt_ts(e.created_at)}  [{e.severity:<6}] {e.category:<16} "
            f"{e.source}/{e.stance}  match={e.match_id}\n"
            f"    user={e.user_id}" + (f"  excerpt: {e.excerpt!r}" if e.excerpt else "")
        )


async def list_flagged(session) -> None:
    rows = (await session.scalars(select(User).where(User.flagged.is_(True)))).all()
    if not rows:
        print("no flagged users")
        return
    for u in rows:
        print(f"{u.id}  device={u.device_id}  banned={u.banned}  created={fmt_ts(u.created_at)}")


async def show_transcript(match_id: str) -> None:
    redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        entries = await redis.lrange(f"transcript:{match_id}", 0, -1)
    finally:
        await redis.aclose()
    if not entries:
        print(f"no transcript for match {match_id} (expired, or agent not running?)")
        return
    for raw in entries:
        try:
            seg = json.loads(raw)
            print(f"[{seg.get('ts', 0):>12.2f}] {seg.get('stance', '?'):<4} {seg.get('text', '')}")
        except json.JSONDecodeError:
            print(f"(unparseable) {raw}")


async def set_banned(session, user_id: str, banned: bool) -> None:
    result = await session.execute(update(User).where(User.id == user_id).values(banned=banned))
    await session.commit()
    if result.rowcount:
        print(f"user {user_id} banned={banned}")
    else:
        sys.exit(f"user {user_id} not found")


async def main() -> None:
    parser = argparse.ArgumentParser(prog="moderate")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("list-reports", "list-events"):
        p = sub.add_parser(name)
        p.add_argument("--limit", type=int, default=20)
    sub.add_parser("list-flagged")
    sub.add_parser("show-transcript").add_argument("match_id")
    sub.add_parser("ban").add_argument("user_id")
    sub.add_parser("unban").add_argument("user_id")
    args = parser.parse_args()

    if args.command == "show-transcript":
        await show_transcript(args.match_id)
        return

    engine, sessionmaker = make_engine_and_sessionmaker(get_settings().postgres_url)
    try:
        async with sessionmaker() as session:
            if args.command == "list-reports":
                await list_reports(session, args.limit)
            elif args.command == "list-events":
                await list_events(session, args.limit)
            elif args.command == "list-flagged":
                await list_flagged(session)
            elif args.command == "ban":
                await set_banned(session, args.user_id, True)
            elif args.command == "unban":
                await set_banned(session, args.user_id, False)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
