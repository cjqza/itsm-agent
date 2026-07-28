"""ITSM API - 工单管理"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel

from app.database import get_db
from app.models.user import User
from app.models.ticket import Ticket, TicketStatus, TicketLog
from app.schemas.ticket import (
    TicketCreate, TicketUpdate, TicketStatusUpdate,
    TicketRate, TicketRemark, TicketMessage,
)
from app.utils.auth import get_current_user, require_permission, has_permission
from app.services.ticket_service import ticket_service
from app.services.sla_service import sla_service
from app.models.category import Category, BusinessModule, Property, Symptom, Cause, Solution

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/itsm", tags=["ITSM"])


@router.get("/categories")
async def list_categories_public(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """公开的分类列表（无需 admin 权限，任何登录用户可访问）"""
    from app.utils.cache import cache_get, cache_set

    # 尝试缓存
    cached = await cache_get("categories:public", ttl=300)
    if cached is not None:
        return cached

    result = await db.execute(select(Category).order_by(Category.sort_order))
    categories = result.scalars().all()
    data = [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "sla_hours": c.sla_hours,
        }
        for c in categories
    ]

    await cache_set("categories:public", data, ttl=300)
    return data


@router.get("/business-modules")
async def list_business_modules(
    category_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """业务模块列表（按管理单元筛选）"""
    query = select(BusinessModule)
    if category_id:
        query = query.where(BusinessModule.category_id == category_id)
    query = query.order_by(BusinessModule.sort_order)
    result = await db.execute(query)
    return [{"id": m.id, "name": m.name, "category_id": m.category_id} for m in result.scalars().all()]


@router.get("/properties")
async def list_properties(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """性质列表"""
    result = await db.execute(select(Property).order_by(Property.id))
    return [{"id": p.id, "name": p.name} for p in result.scalars().all()]


@router.get("/symptoms")
async def list_symptoms(
    business_module_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """症状列表（按业务模块筛选）"""
    query = select(Symptom)
    if business_module_id:
        query = query.where(Symptom.business_module_id == business_module_id)
    query = query.order_by(Symptom.id)
    result = await db.execute(query)
    return [{"id": s.id, "name": s.name, "business_module_id": s.business_module_id} for s in result.scalars().all()]


@router.get("/causes")
async def list_causes(
    business_module_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """原因列表（按业务模块筛选）"""
    query = select(Cause)
    if business_module_id:
        query = query.where(Cause.business_module_id == business_module_id)
    query = query.order_by(Cause.id)
    result = await db.execute(query)
    return [{"id": c.id, "name": c.name, "business_module_id": c.business_module_id} for c in result.scalars().all()]


@router.get("/solutions")
async def list_solutions(
    business_module_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """解决方法列表（按业务模块筛选）"""
    query = select(Solution)
    if business_module_id:
        query = query.where(Solution.business_module_id == business_module_id)
    query = query.order_by(Solution.id)
    result = await db.execute(query)
    return [{"id": s.id, "name": s.name, "business_module_id": s.business_module_id} for s in result.scalars().all()]


async def _has_itsm_access(current_user: User) -> bool:
    """内联检查用户是否拥有 itsm_access 权限（复用缓存）"""
    return await has_permission(current_user, "itsm_access")


class TicketTransferRequest(BaseModel):
    assignee_id: int
    reason: str = ""


class TicketUrgeRequest(BaseModel):
    message: Optional[str] = None


@router.get("/dashboard")
async def dashboard(
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """首页仪表盘"""
    from sqlalchemy import func, case, and_
    from app.models.ticket import Ticket, TicketStatus

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # 合并为单条 SQL，减少数据库往返
    result = await db.execute(
        select(
            func.count(case((Ticket.created_at >= today, 1))).label("today_count"),
            func.count(case((Ticket.status == TicketStatus.PENDING, 1))).label("pending_count"),
            func.count(case(
                (and_(
                    Ticket.assignee_id == current_user.id,
                    Ticket.status.in_([TicketStatus.ACCEPTED, TicketStatus.PROCESSING]),
                ), 1),
            )).label("my_count"),
            func.count(case((Ticket.status == TicketStatus.RESOLVED_PENDING_REVIEW, 1))).label("review_count"),
            func.count(case((Ticket.status == TicketStatus.RESOLVED, 1))).label("resolved_count"),
        )
    )
    row = result.one()

    return {
        "today_count": row.today_count or 0,
        "pending_count": row.pending_count or 0,
        "my_count": row.my_count or 0,
        "review_count": row.review_count or 0,
        "resolved_count": row.resolved_count or 0,
    }


@router.post("/tickets")
async def create_ticket(
    data: TicketCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建工单"""
    creator_id = current_user.id
    ticket = await ticket_service.create_ticket(
        db=db,
        title=data.title,
        description=data.description or "",
        creator_id=creator_id,
        priority=data.priority,
        category_id=data.category_id,
    )
    await db.commit()

    # WebSocket通知：广播新工单给所有客服
    try:
        from app.utils.websocket import ws_manager
        logger.info(f"[WS] 广播新工单: ticket_id={ticket.id}, 在线用户: {list(ws_manager._connections.keys())}")
        await ws_manager.notify_new_ticket({
            "id": ticket.id,
            "ticket_no": ticket.ticket_no,
            "title": ticket.title,
            "status": ticket.status.value,
            "priority": ticket.priority.value if hasattr(ticket.priority, 'value') else (ticket.priority or None),
            "creator_name": current_user.name,
            "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        })
        logger.info(f"[WS] 广播完成: ticket_id={ticket.id}")
    except Exception as e:
        logger.warning(f"WebSocket新工单通知失败: {e}")

    return {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.title,
        "status": ticket.status.value,
    }


@router.get("/tickets")
async def list_tickets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    assignee_id: Optional[int] = None,
    creator_id: Optional[int] = None,
    category_id: Optional[int] = None,
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """工单列表"""
    # 权限隔离：无 itsm_access 的用户只能查看自己创建的工单
    if not await _has_itsm_access(current_user):
        creator_id = current_user.id

    return await ticket_service.list_tickets(
        db=db,
        page=page,
        page_size=page_size,
        status=status,
        assignee_id=assignee_id,
        creator_id=creator_id,
        category_id=category_id,
        keyword=keyword,
    )


@router.get("/tickets/search")
async def search_tickets(
    keyword: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """搜索工单"""
    # 权限隔离：无 itsm_access 的用户只能搜索自己创建的工单
    effective_creator_id = None
    if not await _has_itsm_access(current_user):
        effective_creator_id = current_user.id

    return await ticket_service.list_tickets(
        db=db,
        keyword=keyword,
        creator_id=effective_creator_id,
        page_size=50,
    )


@router.get("/tickets/sla-warnings")
async def get_sla_warning_tickets(
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """获取SLA预警工单"""
    from app.models.ticket import SLAStatus
    result = await db.execute(
        select(Ticket)
        .where(
            Ticket.sla_status.in_([SLAStatus.YELLOW, SLAStatus.RED]),
            Ticket.status.not_in([TicketStatus.RESOLVED, TicketStatus.RESOLVED_PENDING_REVIEW]),
        )
        .order_by(Ticket.sla_deadline)
        .limit(20)
    )
    tickets = result.scalars().all()
    return [ticket_service._ticket_to_dict(t) for t in tickets]


@router.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """工单详情"""
    try:
        ticket = await ticket_service.get_ticket(db, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="工单不存在")

        # 权限校验：仅创建者、被分配客服、或有 itsm_access 的用户可查看
        if not await _has_itsm_access(current_user):
            if ticket["creator_id"] != current_user.id and ticket["assignee_id"] != current_user.id:
                raise HTTPException(status_code=403, detail="无权查看此工单")

        return ticket
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_ticket error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取工单失败，请稍后重试")


@router.put("/tickets/{ticket_id}")
async def update_ticket(
    ticket_id: int,
    data: TicketUpdate,
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """更新工单信息"""
    update_data = data.model_dump(exclude_unset=True)
    ticket = await ticket_service.update_ticket(
        db=db,
        ticket_id=ticket_id,
        operator_id=current_user.id,
        **update_data,
    )
    await db.commit()
    return {"success": True, "ticket_no": ticket.ticket_no}


@router.put("/tickets/{ticket_id}/accept")
async def accept_ticket(
    ticket_id: int,
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """接单（客服手动接单）"""
    ticket = await ticket_service.accept_ticket(
        db=db,
        ticket_id=ticket_id,
        agent_id=current_user.id,
    )
    await db.commit()
    return {"success": True, "status": ticket.status.value}


@router.put("/tickets/{ticket_id}/status")
async def update_status(
    ticket_id: int,
    data: TicketStatusUpdate,
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """更改工单状态"""
    ticket = await ticket_service.update_status(
        db=db,
        ticket_id=ticket_id,
        new_status=data.status,
        operator_id=current_user.id,
        remark=data.remark,
    )
    await db.commit()
    return {"success": True, "status": ticket.status.value}


@router.put("/tickets/{ticket_id}/resolve")
async def resolve_ticket(
    ticket_id: int,
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """解决工单（需要先填写完整分类信息）"""
    # 获取工单
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket_obj = result.scalar_one_or_none()
    if not ticket_obj:
        raise HTTPException(status_code=404, detail="工单不存在")

    # 校验分类信息完整性
    missing = []
    if not ticket_obj.category_id:
        missing.append("管理单元")
    if not ticket_obj.business_module_id:
        missing.append("业务模块")
    if not ticket_obj.property_id:
        missing.append("性质")
    if not ticket_obj.symptom_id:
        missing.append("症状")
    if not ticket_obj.cause_id:
        missing.append("原因")
    if not ticket_obj.solution_id and not ticket_obj.solution_text:
        missing.append("解决方法")

    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"请先填写完整分类信息：{', '.join(missing)}"
        )

    ticket = await ticket_service.update_status(
        db=db,
        ticket_id=ticket_id,
        new_status=TicketStatus.RESOLVED_PENDING_REVIEW.value,
        operator_id=current_user.id,
    )
    await db.commit()
    return {"success": True, "status": ticket.status.value}


@router.put("/tickets/{ticket_id}/rate")
async def rate_ticket(
    ticket_id: int,
    data: TicketRate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """评价工单（仅工单创建者可评价）"""
    # 先校验工单归属
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket_obj = result.scalar_one_or_none()
    if not ticket_obj:
        raise HTTPException(status_code=404, detail="工单不存在")
    if ticket_obj.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="只有工单创建者可以评价")

    ticket = await ticket_service.rate_ticket(
        db=db,
        ticket_id=ticket_id,
        rating_attitude=data.rating_attitude,
        rating_solution=data.rating_solution,
        rating_time=data.rating_time,
        rating_overall=data.rating_overall,
        comment=data.rating_comment,
    )
    await db.commit()
    return {
        "success": True,
        "rating": ticket.rating,
        "rating_attitude": ticket.rating_attitude,
        "rating_solution": ticket.rating_solution,
        "rating_time": ticket.rating_time,
        "rating_overall": ticket.rating_overall,
    }


@router.put("/tickets/{ticket_id}/remark")
async def add_remark(
    ticket_id: int,
    data: TicketRemark,
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """添加备注"""
    ticket = await ticket_service.add_remark(
        db=db,
        ticket_id=ticket_id,
        operator_id=current_user.id,
        remark=data.remark,
        pause_sla=data.pause_ola,
    )
    await db.commit()
    return {"success": True}


@router.put("/tickets/{ticket_id}/pause-sla")
async def pause_sla(
    ticket_id: int,
    reason: str = "",
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """暂停SLA计时"""
    await sla_service.pause_sla(db, ticket_id, reason)
    return {"success": True}


@router.put("/tickets/{ticket_id}/resume-sla")
async def resume_sla(
    ticket_id: int,
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """恢复SLA计时"""
    await sla_service.resume_sla(db, ticket_id)
    return {"success": True}


@router.get("/tickets/{ticket_id}/logs")
async def get_ticket_logs(
    ticket_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """工单操作记录（仅创建者、被分配客服、或有 itsm_access 可查看）"""
    # 先校验工单归属
    ticket_result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket_obj = ticket_result.scalar_one_or_none()
    if not ticket_obj:
        raise HTTPException(status_code=404, detail="工单不存在")

    if not await _has_itsm_access(current_user):
        if ticket_obj.creator_id != current_user.id and ticket_obj.assignee_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权查看此工单记录")

    return await ticket_service.get_ticket_logs(db, ticket_id)


@router.put("/tickets/{ticket_id}/transfer")
async def transfer_ticket(
    ticket_id: int,
    data: TicketTransferRequest,
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """转派工单"""
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")

    # 检查目标客服是否存在
    target_result = await db.execute(select(User).where(User.id == data.assignee_id))
    target_user = target_result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="目标客服不存在")

    # 仅允许 pending/accepted 状态转派
    if ticket.status not in (TicketStatus.PENDING, TicketStatus.ACCEPTED):
        raise HTTPException(status_code=400, detail="当前状态不允许转派")

    old_assignee_id = ticket.assignee_id
    ticket.assignee_id = data.assignee_id
    ticket.status = TicketStatus.ACCEPTED

    # 记录日志
    log = TicketLog(
        ticket_id=ticket_id,
        operator_id=current_user.id,
        action="transfer",
        old_value=str(old_assignee_id),
        new_value=str(data.assignee_id),
        content=f"工单转派给{target_user.name}，原因：{data.reason}",
    )
    db.add(log)
    await db.commit()

    # 通知新客服
    try:
        from app.utils.websocket import ws_manager
        ticket_dict = ticket_service._ticket_to_dict(ticket)
        await ws_manager.notify_ticket_update(ticket_dict, [data.assignee_id])
    except Exception as e:
        logger.warning(f"WebSocket通知失败: {e}")

    return {"success": True, "assignee_name": target_user.name}


@router.put("/tickets/{ticket_id}/cancel")
async def cancel_ticket(
    ticket_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """取消工单（仅pending状态可取消）"""
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")

    # 只有创建者可以取消，且只能取消pending状态的工单
    if ticket.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="只有创建者可以取消工单")
    if ticket.status != TicketStatus.PENDING:
        raise HTTPException(status_code=400, detail="只有待接单状态的工单可以取消")

    old_status = ticket.status.value
    ticket.status = TicketStatus.RESOLVED
    ticket.resolved_at = datetime.now(timezone.utc)
    ticket.remark = "用户取消"

    log = TicketLog(
        ticket_id=ticket_id,
        operator_id=current_user.id,
        action="cancel",
        old_value=old_status,
        new_value="resolved",
        content="用户取消工单",
    )
    db.add(log)
    await db.commit()

    return {"success": True}


@router.put("/tickets/{ticket_id}/urge")
async def urge_ticket(
    ticket_id: int,
    data: TicketUrgeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """催办工单（仅创建者或被分配客服可催办）"""
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")

    # 权限校验：创建者或被分配客服
    if ticket.creator_id != current_user.id and ticket.assignee_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权催办此工单")

    if ticket.status in [TicketStatus.RESOLVED, TicketStatus.RESOLVED_PENDING_REVIEW]:
        raise HTTPException(status_code=400, detail="已解决的工单不能催办")

    # 记录催办日志
    urge_msg = data.message or "用户催办，请尽快处理"
    log = TicketLog(
        ticket_id=ticket_id,
        operator_id=current_user.id,
        action="urge",
        content=f"催办：{urge_msg}",
    )
    db.add(log)
    await db.commit()

    # 通知客服
    if ticket.assignee_id:
        try:
            from app.utils.websocket import ws_manager
            await ws_manager.send_to_user(ticket.assignee_id, {
                "type": "ticket_urge",
                "data": {
                    "ticket_id": ticket_id,
                    "ticket_no": ticket.ticket_no,
                    "message": urge_msg,
                },
            })
        except Exception as e:
            logger.warning(f"WebSocket催办通知失败: {e}")

    return {"success": True, "message": "催办已发送"}


# ============ 批量操作 ============

# 批量操作允许的状态映射
BATCH_VALID_STATES = {
    "accept": [TicketStatus.PENDING],
    "resolve": [TicketStatus.PROCESSING],
}

# 批量操作的目标状态
BATCH_TARGET_STATES = {
    "accept": TicketStatus.ACCEPTED,
    "resolve": TicketStatus.RESOLVED_PENDING_REVIEW,
}


class BatchStatusUpdate(BaseModel):
    """批量状态更新请求"""
    ticket_ids: list[int]
    action: str  # accept / resolve


@router.post("/tickets/batch")
async def batch_update_tickets(
    data: BatchStatusUpdate,
    current_user: User = Depends(require_permission("itsm_access")),
    db: AsyncSession = Depends(get_db),
):
    """批量处理工单（接单/解决）

    智能处理：预检查状态，跳过已处理的工单，只处理符合条件的。
    一次请求处理多张工单，减少批量操作时的 API 调用次数。
    """
    if not data.ticket_ids:
        raise HTTPException(status_code=400, detail="工单列表不能为空")
    if len(data.ticket_ids) > 100:
        raise HTTPException(status_code=400, detail="单次最多处理100张工单")
    if data.action not in ("accept", "resolve"):
        raise HTTPException(status_code=400, detail="action 必须是 accept 或 resolve")

    valid_states = BATCH_VALID_STATES[data.action]
    target_state = BATCH_TARGET_STATES[data.action]

    # 一次性查询所有工单
    result = await db.execute(
        select(Ticket).where(Ticket.id.in_(data.ticket_ids))
    )
    tickets = {t.id: t for t in result.scalars().all()}

    results = {"success": [], "skipped": [], "failed": []}

    for ticket_id in data.ticket_ids:
        ticket = tickets.get(ticket_id)
        if not ticket:
            results["failed"].append({"ticket_id": ticket_id, "error": "工单不存在"})
            continue

        # 跳过已处于目标状态的工单
        if ticket.status == target_state:
            results["skipped"].append({"ticket_id": ticket_id, "reason": f"已是{target_state.value}状态"})
            continue

        # 检查当前状态是否允许操作
        if ticket.status not in valid_states:
            results["skipped"].append({
                "ticket_id": ticket_id,
                "reason": f"当前状态{ticket.status.value}不允许{data.action}，需要: {', '.join(s.value for s in valid_states)}"
            })
            continue

        try:
            if data.action == "accept":
                await ticket_service.accept_ticket(db, ticket_id, current_user.id)
            elif data.action == "resolve":
                await ticket_service.resolve_ticket(db, ticket_id, current_user.id)
            results["success"].append(ticket_id)
        except Exception as e:
            results["failed"].append({"ticket_id": ticket_id, "error": str(e)})

    await db.commit()

    # 批量 WebSocket 通知（合并为一次广播）
    if results["success"]:
        try:
            from app.utils.websocket import ws_manager
            await ws_manager.notify_ticket_update({
                "action": f"batch_{data.action}",
                "ticket_ids": results["success"],
                "count": len(results["success"]),
            })
        except Exception as e:
            logger.warning(f"批量WebSocket通知失败: {e}")

    return {
        "success": True,
        "processed": len(results["success"]),
        "skipped": len(results["skipped"]),
        "failed": len(results["failed"]),
        "details": results,
    }
