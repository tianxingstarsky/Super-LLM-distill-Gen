"""Argilla 原生自建：创建默认管理员与固定 API key（幂等）。

运行（需先启动 ES/Redis 并已迁移数据库）：
  ARGILLA_DATABASE_URL="sqlite:///…/argilla.db" python scripts/setup_argilla_user.py
账号：admin / distill123456，API key：distill.apikey
"""
from __future__ import annotations

import asyncio
import os
import sys

USERNAME = "admin"
PASSWORD = "distill123456"
API_KEY = "distill.apikey"
WORKSPACE = "admin"


async def main() -> None:
    from argilla_server.contexts import accounts
    from argilla_server.database import AsyncSessionLocal
    from argilla_server.enums import UserRole
    from argilla_server.models.database import Workspace

    async with AsyncSessionLocal() as db:
        if await Workspace.get_by(db, name=WORKSPACE) is None:
            await accounts.create_workspace(db, {"name": WORKSPACE})
            print(f"workspace {WORKSPACE} created")

        user = await accounts.get_user_by_username(db, USERNAME)
        if user is None:
            user = await accounts.create_user(
                db,
                {
                    "first_name": "Admin",
                    "last_name": "",
                    "username": USERNAME,
                    "role": UserRole.owner,
                    "password": PASSWORD,
                },
                workspaces=[WORKSPACE],
            )
            print(f"user {USERNAME} created")
        user.api_key = API_KEY
        await user.save(db, autocommit=True)
        print(f"user {USERNAME}: api_key={API_KEY}（幂等完成）")



def create_collaborator(username: str, role: str = "annotator") -> str:
    """创建协作者账号并返回其 API key（每人唯一，评审审计用）。"""
    import secrets

    async def _main() -> str:
        from argilla_server.contexts import accounts
        from argilla_server.database import AsyncSessionLocal
        from argilla_server.enums import UserRole
        from argilla_server.models.database import Workspace

        async with AsyncSessionLocal() as db:
            if await Workspace.get_by(db, name="admin") is None:
                await accounts.create_workspace(db, {"name": "admin"})
            user = await accounts.get_user_by_username(db, username)
            if user is None:
                user = await accounts.create_user(
                    db, {"first_name": username, "last_name": "", "username": username,
                         "role": UserRole[role] if role in ("owner", "admin", "annotator") else UserRole.annotator,
                         "password": secrets.token_urlsafe(12)},
                    workspaces=["admin"],
                )
            else:
                user.role = UserRole[role] if role in ("owner", "admin", "annotator") else UserRole.annotator
            api_key = "agent." + secrets.token_urlsafe(24)
            user.api_key = api_key
            await user.save(db, autocommit=True)
            return api_key

    import asyncio
    return asyncio.run(_main())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--create-user", metavar="USERNAME", help="创建协作者账号")
    parser.add_argument("--role", default="annotator", choices=["owner", "admin", "annotator"])
    args = parser.parse_args()
    if args.create_user:
        key = create_collaborator(args.create_user, args.role)
        print(f"协作者 {args.create_user}: api_key={key}")
    else:
        sys.exit(0 if asyncio.run(main()) is None else 1)
