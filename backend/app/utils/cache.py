"""通用缓存工具 — Redis 双写 + 内存 fallback。

用于缓存变化频率低的数据（分类列表、配置项等）。
管理员修改数据时调用 invalidate() 清除对应缓存。
"""
import asyncio
import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 内存 fallback 存储
_memory_cache: dict[str, tuple[float, Any]] = {}


async def cache_get(key: str, ttl: int = 300) -> Optional[Any]:
    """获取缓存值。

    Args:
        key: 缓存键
        ttl: 过期时间（秒）

    Returns:
        缓存值，过期或不存在返回 None
    """
    # 尝试 Redis
    try:
        from app.utils.redis import get_redis
        redis = await get_redis()
        if redis:
            data = await redis.get(f"cache:{key}")
            if data:
                return json.loads(data)
    except Exception:
        pass

    # 内存 fallback
    entry = _memory_cache.get(key)
    if entry:
        ts, value = entry
        if time.time() - ts < ttl:
            return value
        else:
            del _memory_cache[key]

    return None


async def cache_set(key: str, value: Any, ttl: int = 300):
    """设置缓存值。

    Args:
        key: 缓存键
        value: 缓存值（必须 JSON 可序列化）
        ttl: 过期时间（秒）
    """
    # 写入 Redis
    try:
        from app.utils.redis import get_redis
        redis = await get_redis()
        if redis:
            await redis.setex(f"cache:{key}", ttl, json.dumps(value, ensure_ascii=False))
    except Exception:
        pass

    # 同时写入内存
    _memory_cache[key] = (time.time(), value)


async def cache_invalidate(key: str):
    """清除指定缓存。"""
    # Redis
    try:
        from app.utils.redis import get_redis
        redis = await get_redis()
        if redis:
            await redis.delete(f"cache:{key}")
    except Exception:
        pass

    # 内存
    _memory_cache.pop(key, None)


async def cache_invalidate_pattern(pattern: str):
    """清除匹配模式的缓存（如 "categories*"）。"""
    # Redis
    try:
        from app.utils.redis import get_redis
        redis = await get_redis()
        if redis:
            keys = []
            async for key in redis.scan_iter(match=f"cache:{pattern}"):
                keys.append(key)
            if keys:
                await redis.delete(*keys)
    except Exception:
        pass

    # 内存
    to_delete = [k for k in _memory_cache if k.startswith(pattern.rstrip("*"))]
    for k in to_delete:
        del _memory_cache[k]


async def cached(key: str, ttl: int = 300):
    """缓存装饰器（用于异步函数）。

    用法:
        @cached("categories", ttl=300)
        async def get_categories():
            ...
    """
    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            cached_val = await cache_get(key, ttl)
            if cached_val is not None:
                return cached_val
            result = await func(*args, **kwargs)
            if result is not None:
                await cache_set(key, result, ttl)
            return result
        return wrapper
    return decorator
