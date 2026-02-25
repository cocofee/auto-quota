"""
角色权限依赖注入

提供 require_admin() 函数，管理员专属API路由使用。
复用已有的 get_current_user()，额外检查 is_admin 字段。

用法:
    @router.get("/admin/users")
    async def list_users(admin: User = Depends(require_admin)):
        ...
"""

from fastapi import Depends, HTTPException, status

from app.models.user import User
from app.auth.deps import get_current_user


async def require_admin(
    user: User = Depends(get_current_user),
) -> User:
    """要求当前用户必须是管理员

    非管理员访问时返回 403 错误。
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user
