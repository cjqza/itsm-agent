"""工单服务"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ticket import Ticket, TicketLog, TicketStatus, SLAStatus
from app.models.user import User, UserRole
from app.models.category import Category
from app.utils.ticket_no import generate_ticket_no
from app.utils import escape_like

logger = logging.getLogger(__name__)

# 合法的状态流转: {当前状态: [允许的目标状态]}
VALID_TRANSITIONS = {
    "pending": ["accepted"],
    "accepted": ["processing"],
    "processing": ["resolved_pending_review"],
    "resolved_pending_review": ["resolved"],
    "resolved": [],
}


class TicketService:

    async def create_ticket(
        self,
        db: AsyncSession,
        title: str,
        description: str,
        creator_id: int,
        priority: str = "P3",
        category_id: Optional[int] = None,
    ) -> Ticket:
        """创建工单"""
        ticket_no = await generate_ticket_no(db)

        sla_hours = 4
        if category_id:
            result = await db.execute(select(Category).where(Category.id == category_id))
            category = result.scalar_one_or_none()
            if category:
                sla_hours = category.sla_hours

        ticket = Ticket(
            ticket_no=ticket_no,
            title=title,
            description=description,
            status=TicketStatus.PENDING,
            priority=priority,
            category_id=category_id,
            creator_id=creator_id,
            sla_hours=sla_hours,
            sla_deadline=datetime.now(timezone.utc) + timedelta(hours=sla_hours),
            sla_status=SLAStatus.GREEN,
        )
        db.add(ticket)
        await db.flush()

        log = TicketLog(
            ticket_id=ticket.id,
            operator_id=creator_id,
            action="created",
            content=f"工单已创建: {title}",
        )
        db.add(log)
        await db.flush()

        return ticket

    async def accept_ticket(
        self,
        db: AsyncSession,
        ticket_id: int,
        agent_id: int,
    ) -> Ticket:
        """接单（客服手动接单，自动创建聊天室）"""
        result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
        ticket = result.scalar_one_or_none()
        if not ticket:
            raise ValueError("工单不存在")
        if ticket.status != TicketStatus.PENDING:
            raise ValueError("工单状态不允许接单")

        ticket.assignee_id = agent_id
        ticket.status = TicketStatus.ACCEPTED
        ticket.accepted_at = datetime.now(timezone.utc)

        # 获取客服名字
        agent_result = await db.execute(select(User).where(User.id == agent_id))
        agent = agent_result.scalar_one_or_none()

        log = TicketLog(
            ticket_id=ticket_id,
            operator_id=agent_id,
            action="accepted",
            content=f"{agent.name if agent else '客服'} 已接单",
        )
        db.add(log)

        # 自动创建聊天室（如果不存在）
        from app.models.chat import ChatRoom, ChatMessage, MessageType
        from sqlalchemy import select as sa_select
        existing_room = await db.execute(sa_select(ChatRoom).where(ChatRoom.ticket_id == ticket_id))
        room = existing_room.scalar_one_or_none()
        if not room:
            room = ChatRoom(ticket_id=ticket_id)
            db.add(room)
            await db.flush()

        # 系统消息
        sys_msg = ChatMessage(
            room_id=room.id,
            sender_id=None,
            content=f"客服 {agent.name if agent else ''} 已接单，开始处理您的问题",
            msg_type=MessageType.SYSTEM,
        )
        db.add(sys_msg)

        await db.flush()

        # WebSocket通知
        try:
            from app.utils.websocket import ws_manager
            ticket_dict = self._ticket_to_dict(ticket)
            await ws_manager.notify_ticket_update(ticket_dict, [ticket.creator_id])
        except Exception as e:
            logger.debug(f"WebSocket通知失败: {e}")

        return ticket

    async def get_ticket(self, db: AsyncSession, ticket_id: int) -> Optional[dict]:
        """获取工单详情"""
        result = await db.execute(
            select(Ticket)
            .options(
                selectinload(Ticket.creator),
                selectinload(Ticket.assignee),
                selectinload(Ticket.category),
                selectinload(Ticket.business_module),
                selectinload(Ticket.property),
                selectinload(Ticket.symptom),
                selectinload(Ticket.cause),
                selectinload(Ticket.solution),
            )
            .where(Ticket.id == ticket_id)
        )
        ticket = result.scalar_one_or_none()
        if not ticket:
            return None
        return self._ticket_to_dict(ticket)

    async def list_tickets(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        assignee_id: Optional[int] = None,
        creator_id: Optional[int] = None,
        category_id: Optional[int] = None,
        keyword: Optional[str] = None,
    ) -> dict:
        """工单列表"""
        # 构建基础查询条件
        conditions = []
        if status:
            conditions.append(Ticket.status == status)
        if assignee_id:
            conditions.append(Ticket.assignee_id == assignee_id)
        if creator_id:
            conditions.append(Ticket.creator_id == creator_id)
        if category_id:
            conditions.append(Ticket.category_id == category_id)
        if keyword:
            safe_kw = escape_like(keyword)
            conditions.append(
                or_(
                    Ticket.ticket_no.like(f"%{safe_kw}%", escape="\\"),
                    Ticket.title.like(f"%{safe_kw}%", escape="\\"),
                )
            )

        # 计数查询
        count_query = select(func.count(Ticket.id))
        if conditions:
            count_query = count_query.where(*conditions)
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        # 数据查询
        query = select(Ticket).options(
            selectinload(Ticket.creator),
            selectinload(Ticket.assignee),
            selectinload(Ticket.category),
            selectinload(Ticket.business_module),
            selectinload(Ticket.property),
            selectinload(Ticket.symptom),
            selectinload(Ticket.cause),
            selectinload(Ticket.solution),
        )
        if conditions:
            query = query.where(*conditions)

        query = query.order_by(Ticket.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(query)
        tickets = result.scalars().all()

        return {
            "total": total,
            "items": [self._ticket_to_dict(t) for t in tickets],
        }

    async def update_status(
        self,
        db: AsyncSession,
        ticket_id: int,
        new_status: str,
        operator_id: int,
        remark: Optional[str] = None,
    ) -> Ticket:
        """更新工单状态（含状态流转验证）"""
        result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
        ticket = result.scalar_one_or_none()
        if not ticket:
            raise ValueError("工单不存在")

        old_status = ticket.status.value if ticket.status else None

        # 验证状态流转合法性
        allowed = VALID_TRANSITIONS.get(old_status, [])
        if new_status not in allowed:
            raise ValueError(f"状态不允许从 {old_status} 变更为 {new_status}，允许的目标状态: {allowed}")

        try:
            ticket.status = TicketStatus(new_status)
        except ValueError:
            raise ValueError(f"无效的工单状态: {new_status}")

        now = datetime.now(timezone.utc)
        if new_status == TicketStatus.RESOLVED.value:
            ticket.resolved_at = now
        elif new_status == TicketStatus.RESOLVED_PENDING_REVIEW.value:
            if not ticket.resolved_at:
                ticket.resolved_at = now

        log = TicketLog(
            ticket_id=ticket_id,
            operator_id=operator_id,
            action="status_change",
            old_value=old_status,
            new_value=new_status,
            content=remark,
        )
        db.add(log)
        await db.flush()

        # WebSocket通知
        try:
            from app.utils.websocket import ws_manager
            ticket_dict = self._ticket_to_dict(ticket)
            target_ids = [ticket.creator_id]
            if ticket.assignee_id and ticket.assignee_id != ticket.creator_id:
                target_ids.append(ticket.assignee_id)
            await ws_manager.notify_ticket_update(ticket_dict, target_ids)
        except Exception as e:
            logger.debug(f"WebSocket通知失败: {e}")

        return ticket

    async def update_ticket(
        self,
        db: AsyncSession,
        ticket_id: int,
        operator_id: int,
        **kwargs,
    ) -> Ticket:
        """更新工单信息"""
        from app.models.category import Category

        result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
        ticket = result.scalar_one_or_none()
        if not ticket:
            raise ValueError("工单不存在")

        category_changed = False
        for key, value in kwargs.items():
            if value is not None and hasattr(ticket, key):
                old_val = getattr(ticket, key)
                setattr(ticket, key, value)
                if key == "category_id" and old_val != value:
                    category_changed = True
                log = TicketLog(
                    ticket_id=ticket_id,
                    operator_id=operator_id,
                    action="update",
                    old_value=str(old_val),
                    new_value=str(value),
                    content=f"更新 {key}",
                )
                db.add(log)

        # 管理单元变更时，同步更新 SLA 时间
        if category_changed and ticket.category_id:
            cat_result = await db.execute(
                select(Category).where(Category.id == ticket.category_id)
            )
            category = cat_result.scalar_one_or_none()
            if category and category.sla_hours:
                old_sla = ticket.sla_hours
                ticket.sla_hours = category.sla_hours
                # 重新计算 SLA 截止时间（从创建时间起算）
                ticket.sla_deadline = ticket.created_at + timedelta(hours=category.sla_hours)
                log = TicketLog(
                    ticket_id=ticket_id,
                    operator_id=operator_id,
                    action="update",
                    old_value=str(old_sla),
                    new_value=str(category.sla_hours),
                    content="SLA时间随管理单元变更",
                )
                db.add(log)

        await db.flush()
        return ticket

    async def add_remark(
        self,
        db: AsyncSession,
        ticket_id: int,
        operator_id: int,
        remark: str,
        pause_sla: bool = False,
    ) -> Ticket:
        """添加备注"""
        result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
        ticket = result.scalar_one_or_none()
        if not ticket:
            raise ValueError("工单不存在")

        ticket.remark = remark

        if pause_sla:
            ticket.is_sla_paused = True
            ticket.sla_paused_at = datetime.now(timezone.utc)
            ticket.sla_paused_reason = remark

        log = TicketLog(
            ticket_id=ticket_id,
            operator_id=operator_id,
            action="remark",
            content=remark,
        )
        db.add(log)
        await db.flush()
        return ticket

    async def rate_ticket(
        self,
        db: AsyncSession,
        ticket_id: int,
        rating_attitude: int,
        rating_solution: int,
        rating_time: int,
        rating_overall: int,
        comment: Optional[str] = None,
    ) -> Ticket:
        """评价工单（四维评分）"""
        result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
        ticket = result.scalar_one_or_none()
        if not ticket:
            raise ValueError("工单不存在")

        # 只有待评价状态的工单可以评价
        if ticket.status != TicketStatus.RESOLVED_PENDING_REVIEW:
            raise ValueError("只有待评价状态的工单可以评价")

        ticket.rating_attitude = rating_attitude
        ticket.rating_solution = rating_solution
        ticket.rating_time = rating_time
        ticket.rating_overall = rating_overall
        ticket.rating = rating_overall  # 兼容旧字段
        ticket.rating_comment = comment
        ticket.rated_at = datetime.now(timezone.utc)
        ticket.status = TicketStatus.RESOLVED

        # 关闭聊天室
        from app.models.chat import ChatRoom, RoomStatus, ChatMessage, MessageType
        room_result = await db.execute(
            select(ChatRoom).where(ChatRoom.ticket_id == ticket_id)
        )
        room = room_result.scalar_one_or_none()
        if room:
            room.status = RoomStatus.CLOSED
            room.closed_at = datetime.now(timezone.utc)
            sys_msg = ChatMessage(
                room_id=room.id,
                sender_id=None,
                content=f"工单已解决，评价：{'*' * rating_overall}",
                msg_type=MessageType.SYSTEM,
            )
            db.add(sys_msg)

        log = TicketLog(
            ticket_id=ticket_id,
            operator_id=ticket.creator_id,
            action="rated",
            new_value=str(rating_overall),
            content=comment,
        )
        db.add(log)
        await db.flush()
        return ticket

    async def get_ticket_logs(self, db: AsyncSession, ticket_id: int) -> List[dict]:
        """获取工单操作记录"""
        result = await db.execute(
            select(TicketLog)
            .options(selectinload(TicketLog.operator))
            .where(TicketLog.ticket_id == ticket_id)
            .order_by(TicketLog.created_at.desc())
        )
        logs = result.scalars().all()

        return [
            {
                "id": log.id,
                "ticket_id": log.ticket_id,
                "operator_id": log.operator_id,
                "operator_name": log.operator.name if log.operator else None,
                "action": log.action,
                "old_value": log.old_value,
                "new_value": log.new_value,
                "content": log.content,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ]

    def _ticket_to_dict(self, ticket: Ticket) -> dict:
        """将Ticket对象转换为字典"""

        def safe_rel_name(ticket_obj, rel_name):
            """安全获取关联对象名称"""
            try:
                # 直接在__dict__中检查已加载的关系
                if rel_name in ticket_obj.__dict__:
                    obj = ticket_obj.__dict__[rel_name]
                    return obj.name if obj else None
                return None
            except Exception:
                logger.debug(f"safe_rel_name({rel_name}) failed")
                return None

        return {
            "id": ticket.id,
            "ticket_no": ticket.ticket_no,
            "title": ticket.title,
            "description": ticket.description,
            "status": ticket.status.value if ticket.status else None,
            "priority": ticket.priority.value if ticket.priority else None,
            "category_id": ticket.category_id,
            "category_name": safe_rel_name(ticket, "category"),
            "business_module_id": ticket.business_module_id,
            "business_module_name": safe_rel_name(ticket, "business_module"),
            "property_id": ticket.property_id,
            "property_name": safe_rel_name(ticket, "property"),
            "symptom_id": ticket.symptom_id,
            "symptom_name": safe_rel_name(ticket, "symptom"),
            "cause_id": ticket.cause_id,
            "cause_name": safe_rel_name(ticket, "cause"),
            "solution_id": ticket.solution_id,
            "solution_name": safe_rel_name(ticket, "solution"),
            "solution_text": ticket.solution_text,
            "creator_id": ticket.creator_id,
            "creator_name": safe_rel_name(ticket, "creator"),
            "assignee_id": ticket.assignee_id,
            "assignee_name": safe_rel_name(ticket, "assignee"),
            "sla_hours": ticket.sla_hours,
            "sla_deadline": ticket.sla_deadline.isoformat() if ticket.sla_deadline else None,
            "sla_status": ticket.sla_status.value if ticket.sla_status else None,
            "is_sla_paused": ticket.is_sla_paused,
            "rating": ticket.rating,
            "rating_attitude": ticket.rating_attitude,
            "rating_solution": ticket.rating_solution,
            "rating_time": ticket.rating_time,
            "rating_overall": ticket.rating_overall,
            "rating_comment": ticket.rating_comment,
            "remark": ticket.remark,
            "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
            "accepted_at": ticket.accepted_at.isoformat() if ticket.accepted_at else None,
            "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        }


ticket_service = TicketService()
