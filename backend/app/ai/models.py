"""AI / RAG Pydantic Schema"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class AIChatRequest(BaseModel):
    """AI 聊天请求"""
    question: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    history: list[dict] = Field(default_factory=list, description="历史消息列表（兼容旧接口，session_id 优先）")
    stream: bool = Field(default=False, description="是否流式返回")
    session_id: str = Field(default="", description="会话ID（启用服务端记忆系统）")


class AIChatResponse(BaseModel):
    """AI 聊天响应"""
    answer: str = Field(..., description="AI 回答")
    thinking: Optional[str] = Field(default=None, description="AI 思考过程")
    sources: list[dict] = Field(default_factory=list, description="参考来源")
    has_relevant_docs: bool = Field(default=False, description="是否有相关文档")
    llm_provider: str = Field(default="", description="使用的 LLM 提供商")


class KnowledgeSyncRequest(BaseModel):
    """知识库同步请求"""
    sync_type: str = Field(default="all", description="同步类型: all / tickets / faq")
    force: bool = Field(default=False, description="是否强制全量重建")
    since: Optional[datetime] = Field(default=None, description="增量同步起始时间")


class KnowledgeSyncResponse(BaseModel):
    """知识库同步响应"""
    status: str = Field(..., description="状态: started / error")
    message: str = Field(..., description="提示信息")
    stats: dict = Field(default_factory=dict, description="同步统计")


class KnowledgeStatusResponse(BaseModel):
    """知识库状态响应"""
    total_documents: int = Field(default=0, description="文档总数")
    last_sync: Optional[datetime] = Field(default=None, description="最近同步时间")
    collections: list = Field(default_factory=list, description="集合列表")
