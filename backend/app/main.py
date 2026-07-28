"""公司桌面IT服务台 - FastAPI入口"""
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from collections import defaultdict
import logging
import logging.handlers
import os
import time
import asyncio

from app.config import get_settings
from app.database import init_db
from app.utils.redis import close_redis
from app.api.auth import router as auth_router
from app.api.itsm import router as itsm_router
from app.api.ops import router as ops_router
from app.api.chat import router as chat_router
from app.api.admin import (
    router as admin_router,
    category_router, business_module_router,
    property_router, symptom_router, cause_router, solution_router,
)
from app.api.upload import router as upload_router
from app.api.templates import router as template_router
from app.api.captcha import router as captcha_router
from app.api.ai_chat import router as ai_chat_router

settings = get_settings()

# 日志配置：控制台 + 文件（RotatingFileHandler）
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)

_file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "app.log"),
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(_log_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[_console_handler, _file_handler],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    logger.info("正在初始化数据库...")
    await init_db()
    logger.info("数据库初始化完成")

    from app.tasks.sla_checker import start_sla_checker
    start_sla_checker()

    logger.info(f"{settings.APP_NAME} v{settings.APP_VERSION} 已启动")
    yield

    await close_redis()
    logger.info("应用关闭")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# CORS - 从配置读取允许的前端域名
_cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
# 自动补充 127.0.0.1 变体（如果只配了 localhost）
_extra = []
for origin in _cors_origins:
    if "localhost" in origin:
        _extra.append(origin.replace("localhost", "127.0.0.1"))
_cors_origins.extend(_extra)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============ 限流 ============
# 内存 fallback 存储结构: {client_ip: {path_group: [timestamps]}}
_rate_limit_store: dict = defaultdict(lambda: defaultdict(list))
RATE_LIMIT_CLEANUP_INTERVAL = 60  # 每60秒清理一次过期记录
_last_cleanup = time.time()


def _get_client_ip(request: Request) -> str:
    """获取客户端真实IP"""
    if settings.TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _determine_rate_limit_group(path: str) -> str:
    """根据请求路径确定限流分组"""
    if "/auth/login" in path:
        return "login"
    elif "/auth/register" in path:
        return "register"
    elif "/auth/captcha" in path:
        return "captcha"
    elif "/ai/chat" in path:
        return "ai_chat"
    else:
        return "api"


def _check_rate_limit_memory(client_ip: str, path: str, limit: int, window: int = 60) -> bool:
    """内存限流实现（Redis 不可用时的 fallback）"""
    global _last_cleanup
    now = time.time()

    # 定期清理过期记录
    if now - _last_cleanup > RATE_LIMIT_CLEANUP_INTERVAL:
        _last_cleanup = now
        expired_ips = []
        for ip in list(_rate_limit_store.keys()):
            for group in list(_rate_limit_store[ip].keys()):
                _rate_limit_store[ip][group] = [
                    t for t in _rate_limit_store[ip][group] if now - t < window
                ]
                if not _rate_limit_store[ip][group]:
                    del _rate_limit_store[ip][group]
            if not _rate_limit_store[ip]:
                expired_ips.append(ip)
        for ip in expired_ips:
            del _rate_limit_store[ip]

    group = _determine_rate_limit_group(path)
    timestamps = _rate_limit_store[client_ip][group]
    # 清除窗口外的记录
    timestamps[:] = [t for t in timestamps if now - t < window]

    if len(timestamps) >= limit:
        return False

    timestamps.append(now)
    return True


async def _check_rate_limit(client_ip: str, path: str, limit: int, window: int = 60) -> bool:
    """检查是否超过限流，优先使用 Redis sorted set，失败时降级到内存。
    返回 True 表示允许，False 表示限流。
    """
    from app.utils.redis import get_redis

    r = await get_redis()
    if r is None:
        return _check_rate_limit_memory(client_ip, path, limit, window)

    try:
        group = _determine_rate_limit_group(path)
        key = f"ratelimit:{group}:{client_ip}"
        now = time.time()
        cutoff = now - window

        # 清理过期 + 计数
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zcard(key)
        results = await pipe.execute()
        count = results[1]

        if count >= limit:
            return False

        # 添加当前请求（使用纳秒时间戳作为唯一成员）
        pipe = r.pipeline()
        pipe.zadd(key, {str(time.time_ns()): now})
        pipe.expire(key, window)
        await pipe.execute()
        return True
    except Exception as e:
        logger.warning(f"Redis 限流操作失败，降级到内存: {e}")
        return _check_rate_limit_memory(client_ip, path, limit, window)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """限流中间件"""
    path = request.url.path

    # 跳过非API路径和WebSocket
    if path in ("/", "/health", "/docs", "/openapi.json", "/redoc"):
        return await call_next(request)
    if path.startswith("/api/chat/ws/"):
        return await call_next(request)
    if path == "/ws":
        return await call_next(request)
    # 批量操作接口免限流（已内置状态检查，不会滥用）
    if "/tickets/batch" in path:
        return await call_next(request)

    # 测试模式跳过限流（仅限 localhost）
    if request.headers.get("X-Test-Mode", "").lower() == "true":
        client_host = request.client.host if request.client else ""
        if client_host in ("127.0.0.1", "::1", "localhost"):
            return await call_next(request)

    client_ip = _get_client_ip(request)

    # 登录接口: 10次/分钟/IP（用户可能需要登录多个前端）
    if "/auth/login" in path:
        if not await _check_rate_limit(client_ip, path, limit=10, window=60):
            logger.warning(f"限流: {client_ip} 登录接口请求过于频繁")
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试"},
            )
    # 注册接口: 10次/小时/IP
    elif "/auth/register" in path:
        if not await _check_rate_limit(client_ip, path, limit=10, window=3600):
            logger.warning(f"限流: {client_ip} 注册接口请求过于频繁")
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试"},
            )
    # 验证码接口: 10次/分钟/IP
    elif "/auth/captcha" in path:
        if not await _check_rate_limit(client_ip, path, limit=10, window=60):
            logger.warning(f"限流: {client_ip} 验证码接口请求过于频繁")
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试"},
            )
    # AI 聊天接口: 按用户限流（已登录按 user_id，未登录按 IP）
    elif "/ai/chat" in path:
        rate_key = client_ip  # 默认按 IP
        try:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                from jose import jwt
                token = auth_header[7:]
                payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
                uid = payload.get("sub")
                if uid:
                    rate_key = f"user:{uid}"
        except Exception:
            pass  # token 无效则 fallback 到 IP 限流
        if not await _check_rate_limit(rate_key, path, limit=settings.AI_RATE_LIMIT_PER_MINUTE, window=60):
            logger.warning(f"限流: {rate_key} AI 聊天接口请求过于频繁")
            return JSONResponse(
                status_code=429,
                content={"detail": "AI 聊天请求过于频繁，请稍后再试"},
            )
    # 其他API: 按用户限流（已登录用户 300次/分钟，未登录 120次/分钟）
    elif path.startswith("/api/") or path.startswith("/admin/"):
        api_rate_key = client_ip
        api_limit = 120
        try:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                from jose import jwt
                token = auth_header[7:]
                payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
                uid = payload.get("sub")
                if uid:
                    api_rate_key = f"user:{uid}"
                    api_limit = 300  # 已登录用户更高限额
        except Exception:
            pass
        if not await _check_rate_limit(api_rate_key, path, limit=api_limit, window=60):
            logger.warning(f"限流: {api_rate_key} API请求过于频繁")
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试"},
            )

    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """请求日志"""
    start = time.time()
    response = await call_next(request)
    duration = round((time.time() - start) * 1000, 1)
    if request.url.path not in ("/health", "/"):
        logger.info(f"{request.method} {request.url.path} [{response.status_code}] {duration}ms")
    return response


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"未处理异常: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误，请稍后重试"})


# 注册路由
app.include_router(auth_router)
app.include_router(captcha_router)
app.include_router(itsm_router)
app.include_router(ops_router)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(category_router)
app.include_router(business_module_router)
app.include_router(property_router)
app.include_router(symptom_router)
app.include_router(cause_router)
app.include_router(solution_router)
app.include_router(upload_router)
app.include_router(template_router)
app.include_router(ai_chat_router)


@app.websocket("/ws")
async def websocket_notifications(websocket: WebSocket, token: str = ""):
    """全局通知 WebSocket"""
    if not token:
        await websocket.close(code=4001, reason="缺少token")
        return
    try:
        from app.utils.websocket import ws_manager
        from app.utils.auth import decode_token
        payload = decode_token(token)
        user_id = int(payload.get("user_id"))
    except Exception:
        await websocket.close(code=4001, reason="token验证失败")
        return

    await ws_manager.connect(websocket, user_id)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, user_id)


@app.get("/")
async def root():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
