"""用户资料路由 — 2026-06-25。

改昵称 + 改头像(配合作品广场的作者展示)。

端点:
  GET   /api/me/profile   当前用户资料(nickname / avatar_url / email)
  PATCH /api/me/profile   改昵称
  POST  /api/me/avatar    上传头像 → 落盘 + 写 users.avatar_url + 返回内部 URL

设计:
  - 头像复用 plaza._save_image(同款图片校验 + token 文件名 + 落盘)
  - 头像落 backend/data/avatars/{user_id}/{token}.{ext},mount /api/avatars 静态服务
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.db import execute, fetch_one
from app.deps import get_current_user, get_db
from app.models.user import User
from app.routers.plaza import _save_image
from app.services.project_service import iso_now

router = APIRouter()


class ProfileBody(BaseModel):
    nickname: str = Field(..., min_length=1, max_length=24, description="昵称(1-24 字)")


def _current_profile(conn: sqlite3.Connection, user_id: str) -> dict:
    row = fetch_one(
        conn,
        "SELECT id, email, nickname, avatar_url FROM users WHERE id=?",
        (user_id,),
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")
    keys = row.keys()
    return {
        "id": row["id"],
        "email": row["email"],
        "nickname": row["nickname"] if "nickname" in keys else None,
        "avatar_url": row["avatar_url"] if "avatar_url" in keys else None,
    }


@router.get("/me/profile")
def api_get_profile(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """当前用户资料。"""
    return _current_profile(conn, user.id)


@router.patch("/me/profile")
def api_update_profile(
    body: ProfileBody,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """改昵称。"""
    nickname = body.nickname.strip()
    if not nickname:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "NICKNAME_EMPTY", "message": "昵称不能为空"},
        )
    execute(
        conn,
        "UPDATE users SET nickname=?, updated_at=? WHERE id=?",
        (nickname, iso_now(), user.id),
    )
    conn.commit()
    return _current_profile(conn, user.id)


@router.post("/me/avatar")
async def api_upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """上传头像 → 落盘 + 写 users.avatar_url + 返回资料。"""
    url = await _save_image(file, user.id, "avatars", "/api/avatars")
    execute(
        conn,
        "UPDATE users SET avatar_url=?, updated_at=? WHERE id=?",
        (url, iso_now(), user.id),
    )
    conn.commit()
    return _current_profile(conn, user.id)
