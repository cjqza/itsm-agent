"""AI Chat API - 智能客服聊天与知识库管理"""
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse

from app.models.user import User
from app.utils.auth import get_current_user, require_permission
from app.ai.models import (
    AIChatRequest, AIChatResponse,
    KnowledgeSyncRequest, KnowledgeSyncResponse,
    KnowledgeStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["AI"])


@router.post("/chat", response_model=AIChatResponse)
async def ai_chat(
    req: AIChatRequest,
    current_user: User = Depends(get_current_user),
):
    """AI 智能客服聊天

    支持普通模式和流式模式（SSE）。
    """
    from app.ai.rag import get_rag_pipeline

    pipeline = get_rag_pipeline()
    if pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="AI 服务未就绪，请检查 AI 依赖是否安装或配置是否正确",
        )

    try:
        if req.stream:
            # 流式返回 SSE
            return StreamingResponse(
                pipeline.stream_query(
                    req.question, req.history,
                    session_id=req.session_id,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            # 普通模式
            result = await pipeline.query(
                req.question, req.history,
                session_id=req.session_id,
            )
            return AIChatResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI 聊天异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="AI 服务处理异常，请稍后重试")


@router.post("/knowledge/sync", response_model=KnowledgeSyncResponse)
async def sync_knowledge(
    req: KnowledgeSyncRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_permission("admin_access")),
):
    """同步知识库（后台执行）

    需要 admin_access 权限。同步任务在后台执行，接口立即返回。
    """
    from app.ai.rag import get_rag_pipeline
    from app.ai.knowledge import KnowledgeBuilder

    pipeline = get_rag_pipeline()
    if pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="AI 服务未就绪，请检查 AI 依赖是否安装或配置是否正确",
        )

    builder = KnowledgeBuilder(pipeline._vectorstore, pipeline._config)

    # 在后台执行同步
    async def _do_sync():
        try:
            stats = await builder.sync_all(force=req.force, since=req.since)
            logger.info(f"知识库后台同步完成: {stats}")
        except Exception as e:
            logger.error(f"知识库后台同步失败: {e}", exc_info=True)

    background_tasks.add_task(_do_sync)

    return KnowledgeSyncResponse(
        status="started",
        message="知识库同步任务已在后台启动",
        stats={},
    )


@router.get("/knowledge/status", response_model=KnowledgeStatusResponse)
async def knowledge_status(
    current_user: User = Depends(require_permission("admin_access")),
):
    """获取知识库状态

    需要 admin_access 权限。
    """
    from app.ai.rag import get_rag_pipeline

    pipeline = get_rag_pipeline()
    if pipeline is None:
        return KnowledgeStatusResponse(
            total_documents=0,
            last_sync=None,
            collections=[],
        )

    try:
        stats = await pipeline._vectorstore.get_stats()
        return KnowledgeStatusResponse(
            total_documents=stats.get("total_documents", 0),
            last_sync=None,
            collections=stats.get("collections", []),
        )
    except Exception as e:
        logger.error(f"获取知识库状态异常: {e}", exc_info=True)
        return KnowledgeStatusResponse(
            total_documents=0,
            last_sync=None,
            collections=[],
        )
