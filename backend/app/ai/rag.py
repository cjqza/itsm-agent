"""AI / RAG 管道

协调 Embedding、VectorStore、LLM 完成检索增强生成。
集成会话记忆管理器，支持滑动窗口 + 摘要 + 元数据三层记忆。
全局单例通过 get_rag_pipeline() 获取，惰性初始化，线程安全。
"""
import asyncio
import json
import logging
import re
import threading
from typing import AsyncGenerator, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

# 全局单例
_rag_pipeline = None
_rag_lock = threading.Lock()

# 伪造对话轮次的检测模式
_FAKE_TURN_PATTERNS = [
    r'<\|user\|>',
    r'<\|assistant\|>',
    r'<\|system\|>',
    r'\bUser:\s',
    r'\bAssistant:\s',
    r'\b用户:\s',
    r'\b客服:\s',
]


class RAGPipeline:
    """RAG 管道：retrieve -> build_messages -> llm.generate/stream

    集成 SessionMemoryManager 实现三层记忆架构。
    """

    def __init__(self, embedding, vectorstore, llm, config=None):
        """
        Args:
            embedding: BaseEmbedding 实例
            vectorstore: VectorStoreManager 实例
            llm: BaseLLM 实例
            config: Settings 实例
        """
        self._embedding = embedding
        self._vectorstore = vectorstore
        self._llm = llm
        self._config = config or get_settings()

    @staticmethod
    def _sanitize_answer(answer: str) -> str:
        """过滤 LLM 输出中的伪造对话轮次。

        有些 LLM 会生成 <|user|>...<|assistant|>... 格式的多轮对话，
        我们只需要第一个 assistant 回复，截断后面的所有伪造内容。
        """
        if not answer:
            return answer

        # 截断第一个伪造角色标签之后的所有内容
        earliest_pos = len(answer)
        for pattern in _FAKE_TURN_PATTERNS:
            match = re.search(pattern, answer)
            if match and match.start() < earliest_pos:
                earliest_pos = match.start()

        if earliest_pos < len(answer):
            sanitized = answer[:earliest_pos].strip()
            if sanitized:
                logger.info(
                    f"过滤伪造对话轮次: 截断 {len(answer) - len(sanitized)} 字符"
                )
                return sanitized

        return answer.strip()

    async def _retrieve(self, query: str) -> list[dict]:
        """向量检索 + 过滤低分结果

        Args:
            query: 用户查询

        Returns:
            过滤后的检索结果列表
        """
        top_k = self._config.AI_RAG_TOP_K
        threshold = self._config.AI_RAG_SCORE_THRESHOLD

        try:
            results = await self._vectorstore.search(query, top_k=top_k)
            # 过滤低于阈值的结果
            filtered = [r for r in results if r.get("score", 0) >= threshold]
            logger.info(
                f"RAG 检索: 返回 {len(results)} 条，过滤后 {len(filtered)} 条 "
                f"(阈值={threshold})"
            )
            return filtered
        except Exception as e:
            logger.error(f"RAG 检索异常: {e}")
            return []

    def _dedup_docs_against_history(
        self, docs: list[dict], memory=None
    ) -> list[dict]:
        """去重：过滤掉与滑动窗口中上一轮回答内容高度重叠的文档。

        如果文档的关键信息已经在上一轮回答中出现过，就不再重复喂给 LLM，
        避免 LLM 生成重复的回答。
        """
        if not docs or not memory or not memory.sliding_window:
            return docs

        # 找到最后一条 assistant 消息
        last_assistant = ""
        for msg in reversed(memory.sliding_window):
            if msg.get("role") == "assistant":
                last_assistant = msg.get("content", "")
                break

        if not last_assistant or len(last_assistant) < 20:
            return docs

        # 用文档内容的前 100 字与上轮回答做简单重叠检测
        filtered = []
        last_lower = last_assistant.lower()
        for doc in docs:
            doc_preview = doc["content"][:100].lower()
            # 计算简单的字符重叠率
            overlap_chars = sum(1 for c in doc_preview if c in last_lower and c.strip())
            overlap_ratio = overlap_chars / max(len(doc_preview.strip()), 1)

            if overlap_ratio < 0.6:
                # 重叠率低，保留
                filtered.append(doc)
            else:
                logger.info(f"RAG 去重: 跳过与上轮回答重叠的文档 (overlap={overlap_ratio:.2f})")

        return filtered

    def _build_messages(
        self, question: str, docs: list[dict], memory=None
    ) -> list[dict]:
        """构建 LLM messages（集成记忆层）

        组装顺序：
        [system_prompt + memory_context] -> [sliding_window] -> [current_question + RAG]

        Args:
            question: 用户问题
            docs: 检索到的文档
            memory: SessionMemory 实例（可选）

        Returns:
            LLM messages 列表
        """
        from app.ai.prompts import (
            SYSTEM_PROMPT, RAG_PROMPT_TEMPLATE, FALLBACK_PROMPT,
            build_context, build_memory_context,
        )

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # 添加记忆上下文（元数据 + 摘要）
        if memory:
            memory_ctx = build_memory_context(memory)
            if memory_ctx:
                messages.append({"role": "system", "content": memory_ctx})

            # 添加滑动窗口历史消息
            if memory.sliding_window:
                messages.extend(memory.sliding_window)

        # RAG 文档去重：过滤与上轮回答重叠的文档
        docs = self._dedup_docs_against_history(docs, memory)

        # 构建用户问题（含 RAG 上下文或兜底提示）
        if docs:
            context = build_context(docs)
            user_content = RAG_PROMPT_TEMPLATE.format(
                context=context, question=question
            )
        else:
            user_content = FALLBACK_PROMPT.format(question=question)

        messages.append({"role": "user", "content": user_content})
        return messages

    async def _manage_memory(self, session_id: str, question: str, answer: str):
        """管理会话记忆：记录消息、提取元数据、生成摘要

        Args:
            session_id: 会话 ID
            question: 用户问题
            answer: AI 回答
        """
        if not session_id:
            return

        from app.ai.memory import get_memory_manager
        manager = get_memory_manager()

        # 记录用户消息
        await manager.add_message(session_id, "user", question)

        # 记录 AI 回复
        await manager.add_message(session_id, "assistant", answer)

        # 获取记忆
        memory = await manager.get_or_create(session_id)

        # 检查是否需要摘要
        if memory.needs_summarize:
            await self._summarize(memory, manager)

        # 检查是否需要提取元数据
        if await manager.should_extract_metadata(session_id):
            await self._extract_metadata(question, memory, manager)

    async def _extract_metadata(self, question: str, memory, manager):
        """从对话中提取元数据（调用 LLM）

        Args:
            question: 当前用户问题
            memory: SessionMemory 实例
            manager: SessionMemoryManager 实例
        """
        from app.ai.prompts import METADATA_EXTRACT_PROMPT

        # 构建最近对话文本（取最后 6 条消息）
        recent = memory.sliding_window[-6:] if memory.sliding_window else []
        conversation = "\n".join(
            f"{'用户' if m['role'] == 'user' else '客服'}: {m['content'][:200]}"
            for m in recent
        )

        if not conversation:
            return

        try:
            prompt = METADATA_EXTRACT_PROMPT.format(conversation=conversation)
            result = await self._llm.generate([{"role": "user", "content": prompt}])

            # 解析 JSON
            text = result.get("answer", "").strip()
            # 尝试提取 JSON（可能被包裹在 ```json ... ``` 中）
            if "```" in text:
                import re
                json_match = re.search(r'```(?:json)?\s*(.*?)```', text, re.DOTALL)
                if json_match:
                    text = json_match.group(1).strip()

            metadata = json.loads(text)
            if isinstance(metadata, dict):
                await manager.update_metadata(memory.session_id, metadata)
                logger.info(f"元数据提取成功: {metadata}")
        except Exception as e:
            logger.warning(f"元数据提取失败（不影响正常对话）: {e}")

    async def _summarize(self, memory, manager):
        """将旧消息浓缩为摘要（调用 LLM）

        Args:
            memory: SessionMemory 实例
            manager: SessionMemoryManager 实例
        """
        from app.ai.prompts import SUMMARIZE_PROMPT

        messages_to_summarize = await manager.get_messages_to_summarize(memory.session_id)
        if not messages_to_summarize:
            return

        conversation = "\n".join(
            f"{'用户' if m['role'] == 'user' else '客服'}: {m['content'][:300]}"
            for m in messages_to_summarize
        )

        try:
            prompt = SUMMARIZE_PROMPT.format(conversation=conversation)
            result = await self._llm.generate([{"role": "user", "content": prompt}])

            summary_text = result.get("answer", "").strip()
            if summary_text:
                await manager.apply_summary(memory.session_id, summary_text)
                logger.info(f"对话摘要生成成功（{len(summary_text)} 字）")
        except Exception as e:
            logger.warning(f"对话摘要生成失败: {e}")

    async def query(self, question: str, history: list[dict] = None,
                    session_id: str = None) -> dict:
        """同步查询：manage_memory -> retrieve -> build_messages -> llm.generate

        Args:
            question: 用户问题
            history: 历史消息列表（兼容旧接口，session_id 优先）
            session_id: 会话 ID（启用记忆系统）

        Returns:
            {answer, sources, has_relevant_docs, llm_provider}
        """
        from app.ai.memory import get_memory_manager

        memory = None
        if session_id:
            manager = get_memory_manager()
            # 记录用户消息
            await manager.add_message(session_id, "user", question)
            memory = await manager.get_or_create(session_id)
        else:
            # 兼容旧接口：无 session_id 时使用 history
            from app.ai.prompts import format_history
            history = history or []
            formatted = format_history(
                history, max_turns=self._config.AI_RAG_MAX_HISTORY_TURNS
            )
            # 创建临时记忆对象
            from app.ai.memory import SessionMemory
            memory = SessionMemory(session_id="", sliding_window=formatted)

        # 检索
        docs = await self._retrieve(question)

        # 构建消息
        messages = self._build_messages(question, docs, memory)

        # 生成
        result = await self._llm.generate(messages)
        answer = self._sanitize_answer(result["answer"])
        thinking = result.get("thinking")

        # 记录 AI 回复 + 后处理
        if session_id:
            await self._manage_memory_after(session_id, question, answer, memory, manager)

        # 构建来源信息
        sources = []
        for doc in docs:
            sources.append({
                "content": doc["content"][:200],
                "metadata": doc.get("metadata", {}),
                "score": round(doc.get("score", 0), 3),
            })

        return {
            "answer": answer,
            "thinking": thinking,
            "sources": sources,
            "has_relevant_docs": len(docs) > 0,
            "llm_provider": self._llm.provider_name,
        }

    async def _manage_memory_after(self, session_id: str, question: str,
                                    answer: str, memory, manager):
        """后处理：记录回复、检查摘要、提取元数据"""
        # 记录 AI 回复
        await manager.add_message(session_id, "assistant", answer)

        # 重新获取最新记忆
        memory = await manager.get_or_create(session_id)

        # 检查是否需要摘要
        if memory.needs_summarize:
            await self._summarize(memory, manager)

        # 检查是否需要提取元数据
        if await manager.should_extract_metadata(session_id):
            await self._extract_metadata(question, memory, manager)

    async def stream_query(
        self, question: str, history: list[dict] = None,
        session_id: str = None
    ) -> AsyncGenerator[str, None]:
        """流式查询：返回 SSE 格式的 async generator

        Args:
            question: 用户问题
            history: 历史消息列表（兼容旧接口）
            session_id: 会话 ID（启用记忆系统）

        Yields:
            SSE 格式字符串: "data: {...}\\n\\n"
        """
        from app.ai.memory import get_memory_manager

        memory = None
        manager = None

        if session_id:
            manager = get_memory_manager()
            # 记录用户消息
            await manager.add_message(session_id, "user", question)
            memory = await manager.get_or_create(session_id)
        else:
            # 兼容旧接口
            from app.ai.prompts import format_history
            history = history or []
            formatted = format_history(
                history, max_turns=self._config.AI_RAG_MAX_HISTORY_TURNS
            )
            from app.ai.memory import SessionMemory
            memory = SessionMemory(session_id="", sliding_window=formatted)

        # 检索
        docs = await self._retrieve(question)

        # 构建消息
        messages = self._build_messages(question, docs, memory)

        # 构建来源信息（先发送）
        sources = []
        for doc in docs:
            sources.append({
                "content": doc["content"][:200],
                "metadata": doc.get("metadata", {}),
                "score": round(doc.get("score", 0), 3),
            })

        meta_event = {
            "type": "sources",
            "sources": sources,
            "has_relevant_docs": len(docs) > 0,
            "llm_provider": self._llm.provider_name,
        }
        yield f"data: {json.dumps(meta_event, ensure_ascii=False)}\n\n"

        # 流式生成，同时收集完整回答 + 过滤伪造对话
        full_answer = ""
        fake_detected = False
        try:
            async for event in self._llm.stream(messages):
                if event.get("type") == "token" and not fake_detected:
                    full_answer += event.get("content", "")
                    # 实时检测伪造对话轮次
                    for pattern in _FAKE_TURN_PATTERNS:
                        if re.search(pattern, full_answer):
                            # 截断到伪造标签之前
                            match = re.search(pattern, full_answer)
                            full_answer = full_answer[:match.start()].strip()
                            fake_detected = True
                            logger.info("流式输出中检测到伪造对话轮次，已截断")
                            break
                    if fake_detected:
                        continue  # 丢弃后续所有 token
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                elif event.get("type") != "token":
                    # sources / thinking / done 等非 token 事件正常传递
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"RAG 流式生成异常: {e}")
            error_event = {
                "type": "error",
                "content": "AI 生成出现错误，请稍后重试",
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

        # 后处理记忆（记录回复、摘要、元数据）
        full_answer = self._sanitize_answer(full_answer)
        if session_id and manager and full_answer:
            await self._manage_memory_after(
                session_id, question, full_answer, memory, manager
            )

        # 结束事件
        done_event = {
            "type": "done",
            "has_relevant_docs": len(docs) > 0,
        }
        yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"


def get_rag_pipeline() -> Optional[RAGPipeline]:
    """获取 RAG 管道全局单例（惰性初始化，线程安全）

    Returns:
        RAGPipeline 实例，初始化失败时返回 None
    """
    global _rag_pipeline

    if _rag_pipeline is not None:
        return _rag_pipeline

    with _rag_lock:
        # 双重检查
        if _rag_pipeline is not None:
            return _rag_pipeline

        try:
            from app.ai.embeddings import create_embedding
            from app.ai.vectorstore import VectorStoreManager
            from app.ai.llm import create_llm

            config = get_settings()
            logger.info("正在初始化 RAG 管道...")

            embedding = create_embedding(config)
            vectorstore = VectorStoreManager(embedding, config)
            llm = create_llm(config)

            _rag_pipeline = RAGPipeline(embedding, vectorstore, llm, config)
            logger.info("RAG 管道初始化完成")
            return _rag_pipeline

        except ImportError as e:
            logger.warning(f"RAG 管道初始化失败（缺少依赖）: {e}")
            return None
        except Exception as e:
            logger.error(f"RAG 管道初始化异常: {e}", exc_info=True)
            return None
