"""后台管理API"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from pydantic import BaseModel
from datetime import datetime, timezone

from app.database import get_db
from app.models.user import User, UserRole, UserStatus
from app.models.permission import Permission, PermissionRequest, RequestStatus
from app.models.category import (
    Category, BusinessModule, Property, Symptom, Cause, Solution,
)
from app.schemas.category import (
    CategoryCreate, CategoryUpdate, CategoryOut,
    BusinessModuleCreate, BusinessModuleUpdate, BusinessModuleOut,
    GenericItemCreate, GenericItemUpdate, GenericItemOut,
)
from app.models.audit_log import AuditLog
from app.utils.auth import require_permission, get_current_user, generate_next_login_id, hash_password, _invalidate_perm_cache
from app.utils import escape_like

router = APIRouter(prefix="/api/admin", tags=["后台管理"])


async def _get_user_or_404(db: AsyncSession, user_id: int) -> User:
    """获取用户，不存在则抛 404"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


# ============ 用户管理 Schemas ============

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    department: Optional[str] = None


class UserStatusUpdate(BaseModel):
    status: str  # active / inactive


class AdminCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    login_id: Optional[str] = None  # 如果提供，表示从已有用户升级
    password: Optional[str] = None  # 从已有用户升级时不需要
    email: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = "admin"  # "admin" 或 "super_admin"


# ============ 用户管理 ============

@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: Optional[str] = None,
    role: Optional[str] = None,
    locked: Optional[bool] = None,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """用户列表（分页）

    默认只展示管理员和客服；有 keyword 时搜索全部用户（包括普通用户）。
    locked=true 时只返回被锁定的用户（所有角色）。
    """
    conditions = []
    if locked:
        # 筛选被锁定的用户（locked_until 不为空且未过期）
        conditions.append(User.locked_until.isnot(None))
    if keyword:
        safe_kw = escape_like(keyword)
        conditions.append(
            (User.name.like(f"%{safe_kw}%", escape="\\")) |
            (User.email.like(f"%{safe_kw}%", escape="\\")) |
            (User.login_id.like(f"%{safe_kw}%", escape="\\")) |
            (User.phone.like(f"%{safe_kw}%", escape="\\"))
        )
    if role:
        conditions.append(User.role == role)
    elif not keyword and not locked:
        # 无 keyword 且无 role 筛选且无 locked 筛选时，默认只返回管理员和客服
        conditions.append(User.role.in_([
            UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.AGENT
        ]))

    # 计数
    count_query = select(func.count(User.id))
    if conditions:
        count_query = count_query.where(*conditions)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 数据（JOIN Permission 表获取权限）
    query = (
        select(User, Permission)
        .outerjoin(Permission, User.id == Permission.user_id)
    )
    if conditions:
        query = query.where(*conditions)
    query = query.order_by(User.id)
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    rows = result.all()

    return {
        "total": total,
        "items": [
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "phone": u.phone,
                "login_id": u.login_id,
                "role": u.role.value,
                "department": u.department,
                "status": u.status.value,
                "is_online": u.is_online,
                "locked_until": u.locked_until.isoformat() if u.locked_until else None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "itsm_access": p.itsm_access if p else False,
                "ops_access": p.ops_access if p else False,
                "admin_access": p.admin_access if p else False,
            }
            for u, p in rows
        ],
    }


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    data: UserUpdate,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """更新用户信息"""
    user = await _get_user_or_404(db, user_id)

    # 普通管理员不能修改超级管理员的信息
    if user.role == UserRole.SUPER_ADMIN and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="无权修改超级管理员信息")

    # 检查手机号冲突
    if data.phone is not None and data.phone != user.phone:
        dup = await db.execute(select(User).where(User.phone == data.phone, User.id != user_id))
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="该手机号已被其他用户使用")
        user.phone = data.phone

    if data.name is not None:
        user.name = data.name
    if data.email is not None:
        user.email = data.email
    if data.department is not None:
        user.department = data.department

    user.updated_at = datetime.now(timezone.utc)
    db.add(AuditLog(
        operator_id=current_user.id,
        action="update",
        target_type="user",
        target_id=user_id,
        detail="修改用户信息",
    ))
    await db.commit()
    return {"success": True}


@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: int,
    data: UserStatusUpdate,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """启用/禁用用户"""
    user = await _get_user_or_404(db, user_id)

    # 不能禁用自己
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="不能修改自己的状态")

    # 不能修改超级管理员
    if user.role == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="不能修改超级管理员状态")

    if data.status not in ("active", "inactive"):
        raise HTTPException(status_code=400, detail="状态值无效，必须为 active 或 inactive")

    user.status = UserStatus(data.status)
    user.updated_at = datetime.now(timezone.utc)
    db.add(AuditLog(
        operator_id=current_user.id,
        action="update",
        target_type="user",
        target_id=user_id,
        detail=f"状态改为 {data.status}",
    ))
    await db.commit()
    return {"success": True, "status": user.status.value}


@router.put("/users/{user_id}/unlock")
async def unlock_user(
    user_id: int,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """手动解锁被锁定的账号"""
    user = await _get_user_or_404(db, user_id)

    if user.login_fail_count == 0 and not user.locked_until:
        raise HTTPException(status_code=400, detail="该账号未被锁定")

    user.login_fail_count = 0
    user.locked_until = None
    user.updated_at = datetime.now(timezone.utc)
    db.add(AuditLog(
        operator_id=current_user.id,
        action="unlock",
        target_type="user",
        target_id=user_id,
        detail="手动解锁账号",
    ))
    await db.commit()
    return {"success": True, "message": "账号已解锁"}


@router.post("/admins")
async def create_admin(
    data: AdminCreate,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """设置管理员（仅超级管理员可调用）

    - 提供 login_id：从已有用户升级为管理员（无需密码）
    - 不提供 login_id：创建新管理员账号（需要密码和手机号）
    """
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="只有超级管理员可以创建管理员账号")

    # 校验 role 参数
    if data.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=400, detail="role 必须为 admin 或 super_admin")

    target_role = UserRole.SUPER_ADMIN if data.role == "super_admin" else UserRole.ADMIN

    # 从已有用户升级
    if data.login_id:
        result = await db.execute(select(User).where(User.login_id == data.login_id))
        admin_user = result.scalar_one_or_none()
        if not admin_user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if admin_user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            raise HTTPException(status_code=400, detail="该用户已是管理员")
        if admin_user.name != data.name:
            raise HTTPException(status_code=400, detail="账号与姓名不匹配")

        # 升级为管理员
        admin_user.role = target_role
        admin_user.updated_at = datetime.now(timezone.utc)

        # 确保有权限记录并授予 admin_access
        perm_result = await db.execute(select(Permission).where(Permission.user_id == admin_user.id))
        perm = perm_result.scalar_one_or_none()
        if perm:
            perm.admin_access = True
            perm.itsm_access = True
            perm.ops_access = True
            perm.admin_approved_by = current_user.id
        else:
            db.add(Permission(
                user_id=admin_user.id,
                itsm_access=True, ops_access=True, admin_access=True,
                admin_approved_by=current_user.id,
            ))

        db.add(AuditLog(
            operator_id=current_user.id,
            action="upgrade_admin",
            target_type="admin",
            target_id=admin_user.id,
            detail=f"升级为{target_role.value}: {admin_user.name}",
        ))
        await db.commit()

        return {
            "success": True,
            "user": {
                "id": admin_user.id,
                "name": admin_user.name,
                "login_id": admin_user.login_id,
                "role": admin_user.role.value,
            },
        }

    # 创建新管理员（需要密码和手机号）
    if not data.phone or not data.password:
        raise HTTPException(status_code=400, detail="创建新管理员需要手机号和密码")

    # 检查手机号唯一性
    existing = await db.execute(select(User).where(User.phone == data.phone))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该手机号已被注册")

    login_id = await generate_next_login_id(db)

    admin_user = User(
        name=data.name,
        phone=data.phone,
        login_id=login_id,
        password_hash=hash_password(data.password),
        email=data.email,
        department=data.department,
        role=target_role,
        status=UserStatus.ACTIVE,
    )
    db.add(admin_user)
    await db.flush()

    perm = Permission(
        user_id=admin_user.id,
        itsm_access=True,
        ops_access=True,
        admin_access=True,
        admin_approved_by=current_user.id,
    )
    db.add(perm)
    db.add(AuditLog(
        operator_id=current_user.id,
        action="create",
        target_type="admin",
        target_id=admin_user.id,
        detail=f"创建管理员 {data.name}",
    ))
    await db.commit()

    return {
        "success": True,
        "user": {
            "id": admin_user.id,
            "name": admin_user.name,
            "phone": admin_user.phone,
            "login_id": admin_user.login_id,
            "email": admin_user.email,
            "department": admin_user.department,
            "role": admin_user.role.value,
            "status": admin_user.status.value,
        },
    }


# ============ 权限管理 ============

@router.get("/permissions")
async def list_permissions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: Optional[str] = None,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """权限列表（分页）"""
    base_query = select(Permission, User).join(User, Permission.user_id == User.id)
    count_query = select(func.count(Permission.id)).join(User, Permission.user_id == User.id)

    if keyword:
        safe_kw = escape_like(keyword)
        keyword_condition = (
            (User.name.like(f"%{safe_kw}%", escape="\\")) |
            (User.email.like(f"%{safe_kw}%", escape="\\")) |
            (User.login_id.like(f"%{safe_kw}%", escape="\\")) |
            (User.phone.like(f"%{safe_kw}%", escape="\\"))
        )
        base_query = base_query.where(keyword_condition)
        count_query = count_query.where(keyword_condition)
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    query = base_query.order_by(User.id).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return {
        "total": total,
        "items": [
            {
                "id": perm.id,
                "user_id": perm.user_id,
                "user_name": user.name,
                "user_role": user.role.value,
                "login_id": user.login_id,
                "phone": user.phone,
                "email": user.email,
                "status": user.status.value,
                "login_fail_count": user.login_fail_count or 0,
                "locked_until": user.locked_until.isoformat() if user.locked_until else None,
                "itsm_access": perm.itsm_access,
                "ops_access": perm.ops_access,
                "admin_access": perm.admin_access,
            }
            for perm, user in result.all()
        ],
    }


@router.put("/permissions/{user_id}")
async def update_permission(
    user_id: int,
    itsm_access: Optional[bool] = None,
    ops_access: Optional[bool] = None,
    admin_access: Optional[bool] = None,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """更新用户权限"""
    # 检查目标用户是否存在
    target_user = await _get_user_or_404(db, user_id)

    # 管理员和超级管理员的权限不可修改
    if target_user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        raise HTTPException(status_code=400, detail="管理员权限不可修改")

    result = await db.execute(select(Permission).where(Permission.user_id == user_id))
    perm = result.scalar_one_or_none()

    if not perm:
        perm = Permission(user_id=user_id)
        db.add(perm)

    # 后台权限（admin_access）只能由 super_admin 修改
    if admin_access is not None and admin_access != bool(perm.admin_access):
        if current_user.role != UserRole.SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="后台权限只能由 admin 修改，请联系 admin")

    if itsm_access is not None:
        perm.itsm_access = itsm_access
    if ops_access is not None:
        perm.ops_access = ops_access
    if admin_access is not None:
        perm.admin_access = admin_access
        if admin_access:
            perm.admin_approved_by = current_user.id

    db.add(AuditLog(
        operator_id=current_user.id,
        action="update",
        target_type="permission",
        target_id=user_id,
        detail=f"itsm:{itsm_access}, ops:{ops_access}, admin:{admin_access}",
    ))
    await db.commit()
    await _invalidate_perm_cache(user_id)
    return {"success": True}


# ============ 权限申请 ============

@router.post("/permission-requests")
async def create_permission_request(
    request_type: str,
    reason: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户提交权限申请（无需admin权限）"""
    # 检查是否已有待审批的同类申请
    existing = await db.execute(
        select(PermissionRequest).where(
            PermissionRequest.user_id == current_user.id,
            PermissionRequest.request_type == request_type,
            PermissionRequest.status == RequestStatus.PENDING,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="已有待审批的同类申请")

    req = PermissionRequest(
        user_id=current_user.id,
        request_type=request_type,
        reason=reason,
    )
    db.add(req)
    await db.commit()
    return {"success": True, "message": "权限申请已提交"}


@router.get("/permission-requests")
async def list_permission_requests(
    status: Optional[str] = None,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """权限申请列表"""
    query = (
        select(PermissionRequest, User)
        .join(User, PermissionRequest.user_id == User.id)
    )
    if status:
        query = query.where(PermissionRequest.status == status)
    query = query.order_by(PermissionRequest.created_at.desc())

    result = await db.execute(query)
    return [
        {
            "id": req.id,
            "user_id": req.user_id,
            "user_name": user.name,
            "request_type": req.request_type,
            "status": req.status.value,
            "reason": req.reason,
            "created_at": req.created_at.isoformat() if req.created_at else None,
        }
        for req, user in result.all()
    ]


@router.put("/permission-requests/{request_id}")
async def review_permission_request(
    request_id: int,
    action: str,  # approved, rejected
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """审批权限申请"""
    if action not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="action 必须为 approved 或 rejected")

    result = await db.execute(
        select(PermissionRequest).where(PermissionRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="申请不存在")

    req.status = action
    req.reviewed_by = current_user.id
    req.reviewed_at = datetime.now(timezone.utc)

    # 如果批准，自动开通权限
    if action == "approved":
        perm_result = await db.execute(select(Permission).where(Permission.user_id == req.user_id))
        perm = perm_result.scalar_one_or_none()
        if not perm:
            perm = Permission(user_id=req.user_id)
            db.add(perm)

        if req.request_type == "itsm":
            perm.itsm_access = True
        elif req.request_type == "ops":
            perm.ops_access = True
        elif req.request_type == "admin":
            perm.admin_access = True
            perm.admin_approved_by = current_user.id

    db.add(AuditLog(
        operator_id=current_user.id,
        action=action,
        target_type="permission_request",
        target_id=request_id,
        detail=f"审批权限申请 {req.request_type}",
    ))
    await db.commit()
    if action == "approved":
        await _invalidate_perm_cache(req.user_id)
    return {"success": True}


# ============ 账号申请审批 ============

@router.get("/account-requests")
async def list_account_requests(
    status: str = Query("pending"),
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """账号申请列表（默认列出待审批账号）"""
    try:
        target_status = UserStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail="状态值无效")

    result = await db.execute(
        select(User).where(User.status == target_status).order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    return [
        {
            "id": u.id,
            "name": u.name,
            "phone": u.phone,
            "login_id": u.login_id,
            "role": u.role.value,
            "status": u.status.value,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.put("/account-requests/{user_id}")
async def review_account_request(
    user_id: int,
    action: str = Query(..., description="approve 或 reject"),
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """审批账号申请：approve 分配 login_id 并激活；reject 置为 inactive"""
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action 必须为 approve 或 reject")

    user = await _get_user_or_404(db, user_id)

    if user.status != UserStatus.PENDING:
        raise HTTPException(status_code=400, detail="该账号不在待审批状态")

    if action == "approve":
        # 生成专属ID号（若已有则保留）
        if not user.login_id:
            user.login_id = await generate_next_login_id(db)
        user.status = UserStatus.ACTIVE
        user.updated_at = datetime.now(timezone.utc)

        # 建立空权限记录
        perm_result = await db.execute(select(Permission).where(Permission.user_id == user.id))
        if not perm_result.scalar_one_or_none():
            db.add(Permission(user_id=user.id))

        await db.commit()
        await _invalidate_perm_cache(user.id)
        return {"success": True, "action": "approve", "login_id": user.login_id}
    else:
        user.status = UserStatus.INACTIVE
        user.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return {"success": True, "action": "reject"}


# ============ 分类管理 CRUD ============

def make_crud_router(
    model, create_schema, update_schema, out_schema,
    name: str, name_zh: str, prefix: str,
    cache_keys: list[str] = None,
):
    """生成CRUD路由的工厂函数

    Args:
        cache_keys: 数据变更时需要清除的缓存键列表
    """
    crud_router = APIRouter(prefix=f"/api/admin{prefix}", tags=[f"后台管理-{name_zh}"])

    async def _invalidate_cache():
        if cache_keys:
            from app.utils.cache import cache_invalidate
            for key in cache_keys:
                await cache_invalidate(key)

    @crud_router.get("/")
    async def list_items(
        current_user: User = Depends(require_permission("admin_access")),
        db: AsyncSession = Depends(get_db),
    ):
        result = await db.execute(select(model).order_by(model.sort_order, model.id))
        items = result.scalars().all()
        return [
            {k: v for k, v in item.__dict__.items() if not k.startswith("_")}
            for item in items
        ]

    @crud_router.post("/")
    async def create_item(
        data: create_schema,
        current_user: User = Depends(require_permission("admin_access")),
        db: AsyncSession = Depends(get_db),
    ):
        dump = {k: v for k, v in data.model_dump().items() if hasattr(model, k)}
        item = model(**dump, created_by=current_user.id)
        db.add(item)
        await db.commit()
        await _invalidate_cache()
        return {"success": True, "id": item.id}

    @crud_router.get("/{item_id}")
    async def get_item(
        item_id: int,
        current_user: User = Depends(require_permission("admin_access")),
        db: AsyncSession = Depends(get_db),
    ):
        result = await db.execute(select(model).where(model.id == item_id))
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail=f"{name_zh}不存在")
        return {k: v for k, v in item.__dict__.items() if not k.startswith("_")}

    @crud_router.put("/{item_id}")
    async def update_item(
        item_id: int,
        data: update_schema,
        current_user: User = Depends(require_permission("admin_access")),
        db: AsyncSession = Depends(get_db),
    ):
        result = await db.execute(select(model).where(model.id == item_id))
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail=f"{name_zh}不存在")

        for key, value in data.model_dump(exclude_unset=True).items():
            if hasattr(item, key):
                setattr(item, key, value)

        await db.commit()
        await _invalidate_cache()
        return {"success": True}

    @crud_router.delete("/{item_id}")
    async def delete_item(
        item_id: int,
        current_user: User = Depends(require_permission("admin_access")),
        db: AsyncSession = Depends(get_db),
    ):
        result = await db.execute(select(model).where(model.id == item_id))
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail=f"{name_zh}不存在")

        await db.delete(item)
        await db.commit()
        await _invalidate_cache()
        return {"success": True}

    return crud_router


# 注册各分类的CRUD路由
category_router = make_crud_router(Category, CategoryCreate, CategoryUpdate, CategoryOut, "category", "管理单元", "/categories", cache_keys=["categories:public"])
business_module_router = make_crud_router(BusinessModule, BusinessModuleCreate, BusinessModuleUpdate, BusinessModuleOut, "business_module", "业务模块", "/business-modules")
property_router = make_crud_router(Property, GenericItemCreate, GenericItemUpdate, GenericItemOut, "property", "性质", "/properties")
symptom_router = make_crud_router(Symptom, GenericItemCreate, GenericItemUpdate, GenericItemOut, "symptom", "症状", "/symptoms")
cause_router = make_crud_router(Cause, GenericItemCreate, GenericItemUpdate, GenericItemOut, "cause", "原因", "/causes")
solution_router = make_crud_router(Solution, GenericItemCreate, GenericItemUpdate, GenericItemOut, "solution", "解决方法", "/solutions")


# ============ 审计日志 ============

@router.get("/audit-logs")
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: Optional[str] = None,
    target_type: Optional[str] = None,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """审计日志列表（分页）"""
    conditions = []
    if action:
        conditions.append(AuditLog.action == action)
    if target_type:
        conditions.append(AuditLog.target_type == target_type)

    # 计数
    count_query = select(func.count(AuditLog.id))
    if conditions:
        count_query = count_query.where(*conditions)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 数据：关联查询操作人姓名
    query = (
        select(AuditLog, User)
        .join(User, AuditLog.operator_id == User.id)
    )
    if conditions:
        query = query.where(*conditions)
    query = query.order_by(AuditLog.id.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    rows = result.all()

    return {
        "total": total,
        "items": [
            {
                "id": log.id,
                "operator_name": user.name,
                "action": log.action,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "detail": log.detail,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log, user in rows
        ],
    }


# ============ 客服管理 ============

@router.get("/agents")
async def list_agents(
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """客服列表"""
    result = await db.execute(
        select(User).where(User.role == UserRole.AGENT).order_by(User.id)
    )
    agents = result.scalars().all()
    return [
        {
            "id": a.id,
            "name": a.name,
            "email": a.email,
            "is_online": a.is_online,
            "status": a.status.value,
        }
        for a in agents
    ]


# ============ 客服增删改查 Schemas ============

class AgentCreate(BaseModel):
    name: str
    phone: str
    password: str
    email: Optional[str] = None
    department: Optional[str] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    department: Optional[str] = None


# ============ 客服增删改查 ============

@router.post("/agents/upgrade")
async def upgrade_to_agent(
    user_id: int,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """将已有用户升级为客服"""
    user = await _get_user_or_404(db, user_id)

    if user.role == UserRole.AGENT:
        raise HTTPException(status_code=400, detail="该用户已是客服")

    if user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        raise HTTPException(status_code=400, detail="不能将管理员降级为客服")

    # 升级为客服
    user.role = UserRole.AGENT
    user.is_online = True
    user.updated_at = datetime.now(timezone.utc)

    # 确保有权限记录
    perm_result = await db.execute(select(Permission).where(Permission.user_id == user.id))
    perm = perm_result.scalar_one_or_none()
    if perm:
        perm.itsm_access = True
        perm.ops_access = True
    else:
        db.add(Permission(user_id=user.id, itsm_access=True, ops_access=True))

    db.add(AuditLog(
        operator_id=current_user.id,
        action="upgrade",
        target_type="agent",
        target_id=user.id,
        detail=f"升级为客服: {user.name}",
    ))
    await db.commit()

    return {
        "success": True,
        "user": {
            "id": user.id,
            "name": user.name,
            "phone": user.phone,
            "login_id": user.login_id,
            "role": user.role.value,
        },
    }


@router.post("/agents/downgrade")
async def downgrade_to_user(
    user_id: int,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """将客服降级为普通用户"""
    user = await _get_user_or_404(db, user_id)

    if user.role != UserRole.AGENT:
        raise HTTPException(status_code=400, detail="该用户不是客服")

    # 降级为普通用户
    user.role = UserRole.USER
    user.is_online = False
    user.updated_at = datetime.now(timezone.utc)

    # 移除 itsm/ops 权限
    perm_result = await db.execute(select(Permission).where(Permission.user_id == user.id))
    perm = perm_result.scalar_one_or_none()
    if perm:
        perm.itsm_access = False
        perm.ops_access = False

    db.add(AuditLog(
        operator_id=current_user.id,
        action="downgrade",
        target_type="agent",
        target_id=user.id,
        detail=f"降级为普通用户: {user.name}",
    ))
    await db.commit()

    return {
        "success": True,
        "user": {
            "id": user.id,
            "name": user.name,
            "phone": user.phone,
            "login_id": user.login_id,
            "role": user.role.value,
        },
    }


@router.post("/agents")
async def create_agent(
    data: AgentCreate,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """新增客服（直接创建新账号）"""
    # 检查手机号是否已存在
    existing = await db.execute(select(User).where(User.phone == data.phone))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该手机号已被注册")

    login_id = await generate_next_login_id(db)

    agent = User(
        name=data.name,
        phone=data.phone,
        login_id=login_id,
        password_hash=hash_password(data.password),
        email=data.email,
        department=data.department,
        role=UserRole.AGENT,
        status=UserStatus.ACTIVE,
    )
    db.add(agent)
    await db.flush()  # 获取 agent.id

    # 创建默认权限
    perm = Permission(
        user_id=agent.id,
        itsm_access=True,
        ops_access=True,
    )
    db.add(perm)
    db.add(AuditLog(
        operator_id=current_user.id,
        action="create",
        target_type="agent",
        target_id=agent.id,
        detail=f"创建客服 {data.name}",
    ))
    await db.commit()

    return {
        "success": True,
        "user": {
            "id": agent.id,
            "name": agent.name,
            "phone": agent.phone,
            "login_id": agent.login_id,
            "email": agent.email,
            "department": agent.department,
            "role": agent.role.value,
            "status": agent.status.value,
        },
    }


@router.put("/agents/{user_id}")
async def update_agent(
    user_id: int,
    data: AgentUpdate,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """更新客服信息"""
    agent = await _get_user_or_404(db, user_id)
    if agent.role != UserRole.AGENT:
        raise HTTPException(status_code=400, detail="只能修改客服角色的用户")

    # 检查手机号冲突
    if data.phone and data.phone != agent.phone:
        dup = await db.execute(select(User).where(User.phone == data.phone, User.id != user_id))
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="该手机号已被其他用户使用")
        agent.phone = data.phone

    if data.name is not None:
        agent.name = data.name
    if data.email is not None:
        agent.email = data.email
    if data.department is not None:
        agent.department = data.department

    agent.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True}


@router.delete("/agents/{user_id}")
async def delete_agent(
    user_id: int,
    current_user: User = Depends(require_permission("admin_access")),
    db: AsyncSession = Depends(get_db),
):
    """禁用客服（软删除）"""
    agent = await _get_user_or_404(db, user_id)
    if agent.role == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="不能禁用超级管理员")
    if agent.role != UserRole.AGENT:
        raise HTTPException(status_code=400, detail="只能禁用客服角色的用户")

    agent.status = UserStatus.INACTIVE
    agent.updated_at = datetime.now(timezone.utc)
    db.add(AuditLog(
        operator_id=current_user.id,
        action="delete",
        target_type="agent",
        target_id=user_id,
        detail="禁用客服",
    ))
    await db.commit()
    return {"success": True, "status": "inactive"}
