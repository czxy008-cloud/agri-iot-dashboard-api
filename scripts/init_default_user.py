"""
初始化默认管理员用户脚本

用法:
    .\\venv\\Scripts\\python.exe scripts\\init_default_user.py

默认账号: admin / admin123
生产环境请立即修改默认密码！
"""

import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import select

from app.auth.jwt_middleware import get_password_hash
from app.database import async_session_factory
from app.models import User

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"


async def create_default_user():
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.username == DEFAULT_USERNAME))
        existing = result.scalar_one_or_none()

        if existing:
            print(f"用户 '{DEFAULT_USERNAME}' 已存在，跳过创建")
            return

        hashed_pwd = get_password_hash(DEFAULT_PASSWORD)
        user = User(
            username=DEFAULT_USERNAME,
            hashed_password=hashed_pwd,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        print(f"默认管理员用户创建成功！")
        print(f"  用户名: {DEFAULT_USERNAME}")
        print(f"  密码: {DEFAULT_PASSWORD}")
        print(f"  警告: 请在首次登录后立即修改默认密码！")


if __name__ == "__main__":
    asyncio.run(create_default_user())
