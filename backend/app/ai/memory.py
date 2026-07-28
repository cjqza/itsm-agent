"""AI 客服会话记忆管理器

三层记忆架构：
1. 滑动窗口（sliding_window）：最近 5 轮原始对话，精确回放
2. 会话摘要（summary）：旧对话的浓缩摘要，防止信息丢失
3. 会话元数据（metadata）：设备型号、操作系统、问题分类、场景描述

存储方案：Redis（TTL 30 分钟），Redis 不可用时 fallback 到内存 dict。
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# 滑动窗口最大消息数（5 轮 × 2 条/轮）
MAX_WINDOW_MESSAGES = 10
# 触发摘要的阈值（窗口消息数超过此值时触发摘要）
SUMMARIZE_THRESHOLD = 8
# 摘要最大字符数
MAX_SUMMARY_LENGTH = 500
# 会话 TTL（秒）
SESSION_TTL = 1800  # 30 分钟
# 元数据提取频率（每 N 轮触发一次）
METADATA_EXTRACT_INTERVAL = 3
# Redis key 前缀
REDIS_KEY_PREFIX = "ai:session:"


@dataclass
class SessionMemory:
    """单个会话的记忆"""
    session_id: str
    sliding_window: list = field(default_factory=list)  # [{role, content}]
    summary: str = ""                                    # 旧对话摘要
    metadata: dict = field(default_factory=dict)         # {device_model, os, issue_category, scenario}
    last_active: float = 0.0                             # 最后活跃时间戳
    turn_count: int = 0                                  # 对话轮数（用于控制元数据提取频率）
    needs_summarize: bool = False                        # 是否需要触发摘要


class SessionMemoryManager:
    """全局会话记忆管理器

    Redis 存储，fallback 内存 dict。
    每次读写自动续期 TTL。
    """

    def __init__(self):
        self._memory_sessions: dict[str, SessionMemory] = {}  # 内存 fallback
        self._redis = None
        self._redis_checked = False

    async def _get_redis(self):
        """获取 Redis 客户端（延迟检查）"""
        if not self._redis_checked:
            self._redis_checked = True
            try:
                from app.utils.redis import get_redis
                self._redis = await get_redis()
                if self._redis:
                    logger.info("SessionMemoryManager: 使用 Redis 存储会话记忆")
                else:
                    logger.info("SessionMemoryManager: Redis 不可用，使用内存存储")
            except Exception as e:
                logger.warning(f"SessionMemoryManager: Redis 连接失败，使用内存存储: {e}")
        return self._redis

    async def get_or_create(self, session_id: str) -> SessionMemory:
        """获取或创建会话记忆

        Args:
            session_id: 会话 ID

        Returns:
            SessionMemory 实例
        """
        if not session_id:
            # 无 session_id 时返回临时记忆（不持久化）
            return SessionMemory(session_id="", last_active=time.time())

        redis = await self._get_redis()

        if redis:
            try:
                key = f"{REDIS_KEY_PREFIX}{session_id}"
                data = await redis.get(key)
                if data:
                    memory = self._deserialize(session_id, json.loads(data))
                    memory.last_active = time.time()
                    await redis.expire(key, SESSION_TTL)
                    return memory
            except Exception as e:
                logger.warning(f"Redis 读取会话失败: {e}")

        # 内存 fallback
        memory = self._memory_sessions.get(session_id)
        if memory:
            memory.last_active = time.time()
            return memory

        # 创建新会话
        memory = SessionMemory(session_id=session_id, last_active=time.time())
        await self._save(memory)
        return memory

    async def add_message(self, session_id: str, role: str, content: str):
        """添加消息到滑动窗口

        Args:
            session_id: 会话 ID
            role: 消息角色（user / assistant）
            content: 消息内容
        """
        if not session_id or not content:
            return

        memory = await self.get_or_create(session_id)
        memory.sliding_window.append({"role": role, "content": content})
        memory.last_active = time.time()

        # 轮数计数（一问一答算一轮）
        if role == "user":
            memory.turn_count += 1

        # 窗口超过阈值时触发摘要
        if len(memory.sliding_window) > SUMMARIZE_THRESHOLD:
            memory.needs_summarize = True

        await self._save(memory)

    async def update_metadata(self, session_id: str, new_metadata: dict):
        """更新会话元数据（合并，新值覆盖旧值）

        Args:
            session_id: 会话 ID
            new_metadata: 新的元数据字典
        """
        if not session_id or not new_metadata:
            return

        memory = await self.get_or_create(session_id)
        memory.metadata.update({k: v for k, v in new_metadata.items() if v})
        memory.last_active = time.time()
        await self._save(memory)

    async def should_extract_metadata(self, session_id: str) -> bool:
        """判断是否应该触发元数据提取

        每 METADATA_EXTRACT_INTERVAL 轮触发一次。
        """
        if not session_id:
            return False
        memory = await self.get_or_create(session_id)
        return memory.turn_count > 0 and memory.turn_count % METADATA_EXTRACT_INTERVAL == 0

    async def apply_summary(self, session_id: str, summary_text: str):
        """应用摘要结果，将旧消息移出滑动窗口

        Args:
            session_id: 会话 ID
            summary_text: LLM 生成的摘要文本
        """
        if not session_id or not summary_text:
            return

        memory = await self.get_or_create(session_id)

        # 追加到现有摘要
        if memory.summary:
            memory.summary = f"{memory.summary}\n{summary_text}"
        else:
            memory.summary = summary_text

        # 截断摘要（保留末尾，因为越新的信息越重要）
        if len(memory.summary) > MAX_SUMMARY_LENGTH:
            memory.summary = memory.summary[-MAX_SUMMARY_LENGTH:]

        # 移除窗口前半部分（已摘要的旧消息）
        half = len(memory.sliding_window) // 2
        memory.sliding_window = memory.sliding_window[half:]
        memory.needs_summarize = False

        await self._save(memory)

    async def get_messages_to_summarize(self, session_id: str) -> list:
        """获取需要被摘要的旧消息（窗口前半部分）

        Returns:
            待摘要的消息列表
        """
        if not session_id:
            return []
        memory = await self.get_or_create(session_id)
        if len(memory.sliding_window) <= SUMMARIZE_THRESHOLD:
            return []
        half = len(memory.sliding_window) // 2
        return memory.sliding_window[:half]

    async def _save(self, memory: SessionMemory):
        """保存会话记忆到 Redis 或内存"""
        if not memory.session_id:
            return

        redis = await self._get_redis()

        if redis:
            try:
                key = f"{REDIS_KEY_PREFIX}{memory.session_id}"
                data = json.dumps(self._serialize(memory), ensure_ascii=False)
                await redis.setex(key, SESSION_TTL, data)
            except Exception as e:
                logger.warning(f"Redis 写入会话失败: {e}")
                # fallback 到内存
                self._memory_sessions[memory.session_id] = memory
        else:
            self._memory_sessions[memory.session_id] = memory

    async def delete_session(self, session_id: str):
        """删除会话记忆"""
        if not session_id:
            return

        redis = await self._get_redis()
        if redis:
            try:
                await redis.delete(f"{REDIS_KEY_PREFIX}{session_id}")
            except Exception:
                pass

        self._memory_sessions.pop(session_id, None)

    async def cleanup_expired(self):
        """清理过期的内存会话（Redis 自动过期，只需清理内存）"""
        now = time.time()
        expired = [
            sid for sid, m in self._memory_sessions.items()
            if now - m.last_active > SESSION_TTL
        ]
        for sid in expired:
            del self._memory_sessions[sid]
        if expired:
            logger.info(f"清理了 {len(expired)} 个过期的内存会话")

    @staticmethod
    def _serialize(memory: SessionMemory) -> dict:
        """序列化 SessionMemory 为 JSON 可序列化字典"""
        return {
            "sliding_window": memory.sliding_window,
            "summary": memory.summary,
            "metadata": memory.metadata,
            "last_active": memory.last_active,
            "turn_count": memory.turn_count,
            "needs_summarize": memory.needs_summarize,
        }

    @staticmethod
    def _deserialize(session_id: str, data: dict) -> SessionMemory:
        """从 JSON 字典反序列化为 SessionMemory"""
        return SessionMemory(
            session_id=session_id,
            sliding_window=data.get("sliding_window", []),
            summary=data.get("summary", ""),
            metadata=data.get("metadata", {}),
            last_active=data.get("last_active", 0.0),
            turn_count=data.get("turn_count", 0),
            needs_summarize=data.get("needs_summarize", False),
        )


# 全局单例
_memory_manager: Optional[SessionMemoryManager] = None


def get_memory_manager() -> SessionMemoryManager:
    """获取全局 SessionMemoryManager 单例"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = SessionMemoryManager()
    return _memory_manager
