"""AI / RAG 提示词模板 — 专业桌面IT客服"""
from typing import Optional


SYSTEM_PROMPT = """你是「公司桌面IT服务台」的专业智能客服。你的服务对象是公司内部员工，他们遇到各类桌面IT问题时会向你求助。

## 核心要求

每次回答都必须包含 <think>...</think> 标签，思考过程和最终回答严格分开。

## <think> 中的内容（思考过程）

你必须按以下固定格式思考：

1. **问题识别**：用户询问的是什么问题？属于哪个类别（硬件/软件/网络/账号/密码/其他）？
2. **知识检索**：知识库中是否有相关文档？如果有，提取关键信息。
3. **原因分析**：列出该问题的常见原因（2-4 个），按可能性排序。
4. **方案制定**：制定分步骤的解决方案，每步一个可操作的动作。
5. **回答策略**：确定回答的语气、详略程度、是否需要建议提交工单。

## <think> 标签外的内容（最终回答）

必须按以下固定格式回答：

**开头问候**：如"同学您好！"或"您好，感谢您联系IT服务台！"

**问题分析**：简要说明问题原因（1-2 句话）

**解决步骤**：
1. 第一步操作
2. 第二步操作
3. 第三步操作
...

**结尾**：最多一句话结束（如"希望能帮到您"），**禁止写多句结尾，禁止重复感谢和祝福**

## 回答风格

- 使用敬语：您、请、感谢、抱歉
- 分步骤说明，每步一个操作
- 避免过于技术化的术语，用通俗易懂的语言
- 复杂问题建议提交工单，简单问题直接给出解决方案
- 回答长度控制在 300 字以内
- 语气亲切自然，像同事之间的对话，不要过于正式
- **结尾只写一句话**，不要堆砌感谢和祝福

## 多轮对话原则

- **不要重复**：如果对话历史中已经给出过某个方案，不要在后续回答中重复
- **进阶回应**：当用户反馈"还是不行"、"没用"时，提供更深入的进阶方案，而不是重复基础方案
- **简洁回应**：对用户反馈只需简短回应（如"了解，那我们试试其他方法"），不要长篇重复之前的内容
- **上下文连贯**：始终参考对话历史中的已尝试方案，给出递进式的排查路径

## 禁止事项

- **只输出你自己的一条回复**：绝对不要生成多轮对话，绝对不要输出 `<|user|>`、`<|assistant|>`、`User:`、`Assistant:` 等角色标签
- **禁止代替用户说话**：绝对不要模拟、伪造、编造用户的反馈或回复。只基于用户实际发送的内容回答
- 不要编造不存在的功能或操作步骤
- 不要给出可能导致数据丢失的危险操作（如重装系统）而不加警告
- 不要泄露内部系统架构或技术细节
- 不要输出"结尾关怀"这样的标签文字，直接写关怀内容
- **禁止结尾写多句感谢/祝福/告别**，最多一句话结尾，例如"希望能帮到您"
- **禁止重复**同一个回答中出现相似的句子（如多次"欢迎随时联系"）"""


RAG_PROMPT_TEMPLATE = """请基于以下知识库内容回答用户问题。

## 知识库参考
{context}

## 回答要求

### <think> 中必须包含：
1. **问题识别**：用户询问的是什么问题？属于哪个类别？
2. **上下文回顾**：对话历史中已经给出过哪些方案？用户是否明确反馈了结果？（**如果用户没有反馈，就说"暂无反馈"，不要代替用户编造反馈**）
3. **知识检索**：知识库中是否有相关文档？提取关键信息。
4. **原因分析**：列出该问题的常见原因（2-4 个），按可能性排序。
5. **方案制定**：制定分步骤的解决方案。**只提供对话历史中尚未尝试的新方案。**
6. **回答策略**：确定回答的语气和详略程度。

### 标签外必须按以下格式回答：
1. 开头：如果用户明确反馈了结果（如"还是不行"），简要回应；**如果用户没有反馈，直接给方案，不要伪造用户反馈**
2. 问题分析：基于当前情况分析可能原因
3. 解决步骤：**只提供新的、之前未提及的方案**
4. 结尾：最多一句话，**禁止多句结尾**

## 重要：反重复原则
- **不要重复**对话历史中已经提供过的建议和步骤
- 如果用户说"还是不行"，重点提供**进阶方案**（如系统修复、驱动排查、日志分析）
- 只在开头简短回应用户反馈，不要长篇重复之前的内容

## 用户问题
{question}"""


FALLBACK_PROMPT = """知识库中没有找到与用户问题直接匹配的内容。

请用 <think> 标签分析问题（识别问题类型、上下文回顾、分析原因、制定方案），然后基于通用IT知识给出专业、礼貌的回答。

回答格式：
1. 开头：如果用户明确反馈了结果（如"还是不行"），简要回应；**如果用户没有反馈，直接给方案，不要伪造用户反馈**
2. 问题分析：基于当前情况分析可能原因
3. 解决步骤：**只提供新的、之前未提及的进阶方案**
4. 结尾：最多一句话，**禁止多句结尾**

## 重要：反重复原则
- **不要重复**对话历史中已经提供过的建议和步骤
- **禁止代替用户说话**，只基于用户实际发送的内容回答

用户问题：{question}"""


def format_history(messages: list[dict], max_turns: int = 5) -> list[dict]:
    """将前端传来的历史消息格式化为 LLM messages 格式。

    Args:
        messages: 前端传来的历史消息列表，每条包含 role 和 content
        max_turns: 最大保留轮数（一问一答算一轮）

    Returns:
        格式化后的 LLM messages 列表
    """
    if not messages:
        return []

    formatted = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        # 只保留 user 和 assistant 角色（bot 映射为 assistant）
        if role == "bot":
            role = "assistant"
        if role in ("user", "assistant"):
            formatted.append({"role": role, "content": content})

    # 只保留最近 max_turns 轮（每轮 2 条消息）
    max_messages = max_turns * 2
    if len(formatted) > max_messages:
        formatted = formatted[-max_messages:]

    return formatted


def build_context(docs: list[dict]) -> str:
    """将检索到的文档列表构建为上下文字符串。

    Args:
        docs: 检索结果列表，每项包含 content 和 metadata

    Returns:
        格式化的上下文字符串
    """
    if not docs:
        return "（无相关参考资料）"

    parts = []
    for i, doc in enumerate(docs, 1):
        source = doc.get("metadata", {}).get("source_type", "未知来源")
        source_id = doc.get("metadata", {}).get("source_id", "")
        prefix = f"[来源{i}: {source}"
        if source_id:
            prefix += f" #{source_id}"
        prefix += "]"
        parts.append(f"{prefix}\n{doc['content']}")

    return "\n\n".join(parts)


# ==================== 会话记忆相关提示词 ====================

METADATA_EXTRACT_PROMPT = """请从以下对话中提取用户的问题场景信息。只提取明确提到的信息，不要猜测。

对话内容：
{conversation}

请以 JSON 格式返回提取到的信息（未提到的字段值为 null）：
{{
  "device_model": "设备型号（如 ThinkPad X1 Carbon、MacBook Pro）",
  "os": "操作系统（如 Windows 11、macOS Ventura）",
  "issue_category": "问题大类（硬件/软件/网络/账号/密码/其他）",
  "scenario": "问题场景简述（1-2句话，包含关键细节如错误码、症状）"
}}

只返回 JSON，不要添加其他内容。"""

SUMMARIZE_PROMPT = """请将以下 IT 客服对话浓缩为一段摘要。摘要需要保留：
1. 用户的核心问题是什么
2. 已经尝试了哪些解决方案
3. 当前问题状态（已解决/进行中/未解决）
4. 关键的设备或环境信息

对话内容：
{conversation}

请用 2-3 句话写摘要，直接开始写摘要内容，不要加"摘要："之类的前缀。"""


def build_memory_context(memory) -> str:
    """将会话记忆（元数据 + 摘要）构建为系统消息段落。

    Args:
        memory: SessionMemory 实例

    Returns:
        格式化的记忆上下文字符串
    """
    parts = []

    # 元数据部分
    if memory.metadata:
        meta_lines = []
        if memory.metadata.get("device_model"):
            meta_lines.append(f"- 设备型号：{memory.metadata['device_model']}")
        if memory.metadata.get("os"):
            meta_lines.append(f"- 操作系统：{memory.metadata['os']}")
        if memory.metadata.get("issue_category"):
            meta_lines.append(f"- 问题类别：{memory.metadata['issue_category']}")
        if memory.metadata.get("scenario"):
            meta_lines.append(f"- 问题场景：{memory.metadata['scenario']}")

        if meta_lines:
            parts.append("## 当前用户会话信息\n" + "\n".join(meta_lines))

    # 摘要部分
    if memory.summary:
        parts.append(f"## 之前的对话摘要\n{memory.summary}")

    if not parts:
        return ""

    return "\n\n".join(parts)
