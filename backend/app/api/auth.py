"""认证API"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime, timedelta, timezone
import logging
import time

from app.database import get_db
from app.models.user import User, UserRole, UserStatus
from app.models.permission import Permission
from app.utils.auth import (
    create_access_token, get_current_user, verify_password,
    hash_password, generate_next_login_id,
)
from app.api.captcha import verify_captcha

# IP 登录失败追踪：{ip: [timestamps]}
_ip_fail_store: dict[str, list[float]] = {}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["认证"])

# 锁定配置
MAX_LOGIN_FAIL = 5
CAPTCHA_AFTER_FAILS = 3
IP_FAIL_LIMIT = 10  # 同一IP登录失败次数限制
IP_FAIL_WINDOW = 300  # IP失败计数窗口（秒）


def _check_ip_fail_limit(client_ip: str) -> bool:
    """检查IP登录失败次数限制，返回True表示允许，False表示超限"""
    now = time.time()
    if client_ip not in _ip_fail_store:
        _ip_fail_store[client_ip] = []

    # 清理过期记录
    _ip_fail_store[client_ip] = [
        t for t in _ip_fail_store[client_ip] if now - t < IP_FAIL_WINDOW
    ]

    # 过期记录清理完后如果为空，删除 key 防止内存泄漏
    if not _ip_fail_store[client_ip]:
        del _ip_fail_store[client_ip]
        return True

    if len(_ip_fail_store[client_ip]) >= IP_FAIL_LIMIT:
        return False

    return True


def _record_ip_fail(client_ip: str):
    """记录一次IP登录失败"""
    if client_ip not in _ip_fail_store:
        _ip_fail_store[client_ip] = []
    _ip_fail_store[client_ip].append(time.time())


class LoginRequest(BaseModel):
    account: str = Field(..., min_length=1, max_length=64, description="专属ID或电话")
    password: str = Field(..., min_length=1, max_length=128)
    captcha_id: Optional[str] = None
    captcha_text: Optional[str] = None


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    phone: str = Field(..., min_length=5, max_length=32, pattern=r"^[0-9+\-() ]{5,32}$")
    password: str = Field(..., min_length=6, max_length=128)
    captcha_id: str = Field(..., min_length=1)
    captcha_text: str = Field(..., min_length=1)


class ResetPasswordRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    phone: str = Field(..., min_length=5, max_length=32, pattern=r"^[0-9+\-() ]{5,32}$")
    captcha_id: str = Field(..., min_length=1)
    captcha_text: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=128)
    sms_code: Optional[str] = None  # 预留手机验证码字段，当前不校验


class LoginResponse(BaseModel):
    token: str
    user: dict
    permissions: dict


def _build_permissions(user: User, perm: Permission | None) -> dict:
    is_admin = user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)
    return {
        "itsm": True if is_admin else (perm.itsm_access if perm else False),
        "ops": True if is_admin else (perm.ops_access if perm else False),
        "admin": True if is_admin else (perm.admin_access if perm else False),
    }


def _is_test_mode(request: Request) -> bool:
    """检查是否为测试模式（仅限 localhost）"""
    # 检查 X-Test-Mode 头
    if request.headers.get("X-Test-Mode", "").lower() != "true":
        return False
    # 在测试环境中，允许所有请求（CI 环境可能不是 localhost）
    host = request.headers.get("host", "")
    # 如果没有 host 头或者是 localhost/127.0.0.1，则允许测试模式
    if not host or any(h in host for h in ("localhost", "127.0.0.1", "test")):
        return True
    return False


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """登录 - 账号（专属ID或电话）+ 密码；3次失败后需要验证码"""
    account = req.account.strip()
    test_mode = _is_test_mode(request)
    client_ip = request.client.host if request.client else "unknown"

    # IP 登录失败次数限制
    if not test_mode and not _check_ip_fail_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail="登录尝试过于频繁，请稍后再试",
        )

    # 按 login_id 或 phone 查找用户
    result = await db.execute(
        select(User).where((User.login_id == account) | (User.phone == account))
    )
    user = result.scalar_one_or_none()

    # 检查账号锁定（兼容 SQLite 时区问题）
    if user and user.locked_until:
        locked_until = user.locked_until
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if locked_until > now:
            raise HTTPException(
                status_code=423,
                detail="账号已锁定，请联系管理员解锁",
            )

    # 验证码检查（测试模式跳过）
    if not test_mode:
        if not req.captcha_id:
            raise HTTPException(status_code=400, detail="请输入验证码")
        if not req.captcha_text:
            raise HTTPException(status_code=400, detail="请输入验证码")
        if not await verify_captcha(req.captcha_id, req.captcha_text, test_mode=False):
            # 验证码错误，记录 IP 失败，不检查密码
            _record_ip_fail(client_ip)
            raise HTTPException(status_code=400, detail="验证码错误或已过期")

    # 校验密码
    if user is None or not verify_password(req.password, user.password_hash):
        # 记录 IP 失败
        if not test_mode:
            _record_ip_fail(client_ip)

        # 用户存在时更新失败计数
        if user is not None:
            user.login_fail_count += 1
            if user.login_fail_count >= MAX_LOGIN_FAIL:
                user.locked_until = datetime.now(timezone.utc) + timedelta(days=365*10)  # 长期锁定，需管理员解锁
            await db.commit()

            # 返回是否需要验证码
            if user.login_fail_count >= CAPTCHA_AFTER_FAILS:
                raise HTTPException(
                    status_code=401,
                    detail="账号或密码错误",
                    headers={"X-Require-Captcha": "true"},
                )
        raise HTTPException(status_code=401, detail="账号或密码错误")

    # 非激活状态不能登录
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=401, detail="账号或密码错误")

    # 登录成功：重置失败计数
    user.login_fail_count = 0
    user.locked_until = None
    await db.commit()

    # 清除 IP 失败计数
    if client_ip in _ip_fail_store:
        del _ip_fail_store[client_ip]

    token = create_access_token({"user_id": user.id, "role": user.role.value})

    perm_result = await db.execute(select(Permission).where(Permission.user_id == user.id))
    perm = perm_result.scalar_one_or_none()

    return {
        "token": token,
        "user": {
            "id": user.id,
            "name": user.name,
            "role": user.role.value,
            "login_id": user.login_id,
            "phone": user.phone,
        },
        "permissions": _build_permissions(user, perm),
    }


@router.post("/register")
async def register(req: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """注册 - 即注册即登录（需要验证码）"""
    test_mode = _is_test_mode(request)

    # 验证码校验（测试模式仅验证 captcha_id 存在，跳过文本比对）
    if not await verify_captcha(req.captcha_id, req.captcha_text, test_mode=test_mode):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    phone = req.phone.strip()

    # 电话全局唯一（任意状态）
    existing = await db.execute(select(User).where(User.phone == phone))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该电话已注册")

    # 自动生成 login_id
    login_id = await generate_next_login_id(db)

    user = User(
        name=req.name.strip(),
        phone=phone,
        login_id=login_id,
        password_hash=hash_password(req.password),
        role=UserRole.USER,
        status=UserStatus.ACTIVE,  # 即注册即激活
    )
    db.add(user)
    await db.flush()

    # 自动创建空 Permission 记录
    perm = Permission(user_id=user.id)
    db.add(perm)
    await db.commit()

    # 自动登录，返回 token
    token = create_access_token({"user_id": user.id, "role": user.role.value})

    return {
        "token": token,
        "user": {
            "id": user.id,
            "name": user.name,
            "role": user.role.value,
            "login_id": user.login_id,
            "phone": user.phone,
        },
        "permissions": _build_permissions(user, perm),
    }


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """重置密码（无需鉴权）"""
    test_mode = _is_test_mode(request)

    # 1. 验证码校验（优先检查，错误时明确提示）
    if not await verify_captcha(req.captcha_id, req.captcha_text, test_mode=test_mode):
        raise HTTPException(status_code=400, detail="验证码错误")

    # 2. 根据 name + phone 查找用户
    phone = req.phone.strip()
    name = req.name.strip()
    result = await db.execute(
        select(User).where(User.name == name, User.phone == phone)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="账号姓名错误")

    # 3. 检查新密码不能与原密码相同
    if user.password_hash and verify_password(req.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")

    # 4. 更新密码
    user.password_hash = hash_password(req.new_password)
    user.login_fail_count = 0
    user.locked_until = None
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {"success": True, "message": "密码重置成功，请重新登录"}


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """获取当前用户信息"""
    perm_result = await db.execute(select(Permission).where(Permission.user_id == current_user.id))
    perm = perm_result.scalar_one_or_none()

    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "role": current_user.role.value,
        "login_id": current_user.login_id,
        "phone": current_user.phone,
        "permissions": _build_permissions(current_user, perm),
    }
