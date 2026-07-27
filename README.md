# 公司桌面IT服务台 - AI智能版

公司IT服务台管理系统，集成**AI智能客服**，包含用户服务台、ITSM客服端、OPS统计和后台管理四大模块。

## 核心亮点：AI智能客服

### RAG检索增强生成
- 基于已解决工单和FAQ文档知识库
- 智能检索相关解决方案
- 自动生成专业回答

### 三层会话记忆
```
┌─────────────────────────────────────┐
│  滑动窗口（最近5轮原始对话）          │
│  会话摘要（旧对话自动浓缩）           │
│  会话元数据（设备/系统/问题场景）      │
└─────────────────────────────────────┘
```

### 思考过程显示
- AI回答前展示推理过程（`<think>`标签解析）
- 可折叠的思考区域
- 流式输出逐字显示

### 知识库管理
- 自动同步已解决工单
- 手动FAQ文档导入
- 管理员可手动同步知识库

## 快速开始

### 一键启动

```bash
start.bat    # 启动所有服务
stop.bat     # 停止所有服务
```

### 单独启动

```bash
cd backend && python run.py                       # 后端 :8000
cd frontend-client && npm install && npm run dev  # 用户服务台 :5173
cd frontend-agent && npm install && npm run dev   # ITSM客服端 :5174
cd frontend && npm run dev -- --port 5175         # 后台管理 :5175
cd frontend-ops && npm install && npm run dev     # OPS统计 :5176
```

### 重置数据库

```bash
rm backend/it_ops.db && cd backend && python seed_data.py
```

## 默认账号

| 角色 | login_id | 密码 | 说明 |
|------|----------|------|------|
| **超级管理员** | `admin` | `admin123` | 拥有所有权限 |
| 客服 | `U00001` | `123456` | 张三 |
| 客服 | `U00002` | `123456` | 李四 |
| 客服 | `U00003` | `123456` | 王五 |
| 客服 | `U00004` | `123456` | 赵六 |
| 客服 | `U00005` | `123456` | 钱七 |
| 用户 | `U00006` | `123456` | 刘一 |
| 用户 | `U00007` | `123456` | 陈二 |

## 功能模块

### 🖥️ 用户服务台 (frontend-client :5173)
- **AI智能客服**（RAG检索+流式输出+思考过程）
- 首页机器人对话（关键词识别、自动回复）
- 一键转人工创建工单
- 工单列表与详情查看
- 实时聊天（WebSocket）
- 工单评价

### 📋 ITSM客服端 (frontend-agent :5174)
- 仪表盘（今日工单、待处理、我的工单、预警）
- 工单池（手动接单）
- 工单详情（状态流转、分类、备注）
- 实时聊天（WebSocket）
- 工单转派
- SLA暂停/恢复

### 📊 OPS统计 (frontend-ops :5176)
- 总览统计（工单数、评分、SLA达标率）
- 工单趋势图（ECharts）
- 按管理单元/客服/状态统计
- 历史工单查询
- 报表导出（Excel）

### ⚙️ 后台管理 (frontend :5175)
- 用户管理（列表、启用/禁用）
- 权限管理（ITSM/OPS/后台三套权限）
- 分类配置CRUD（管理单元、业务模块、性质、症状、原因、解决方法）
- 客服管理
- **知识库管理**（AI知识库同步和状态查询）

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12+ / FastAPI |
| 数据库 | SQLite (开发) / MySQL (生产) |
| ORM | SQLAlchemy (异步) |
| AI | RAG + ChromaDB + BGE + LLM |
| 前端 | Vue 3 / Element Plus / ECharts |
| 认证 | JWT |
| 实时通信 | WebSocket |

## AI配置

在 `backend/.env` 中配置AI相关参数：

```env
# LLM配置
AI_LLM_PROVIDER=deepseek          # 或 transformers/gguf
AI_LLM_MODEL_NAME=deepseek-chat
AI_LLM_API_KEY=sk-xxx             # DeepSeek API密钥

# Embedding配置
AI_EMBEDDING_PROVIDER=bge
AI_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5

# RAG配置
AI_VECTORSTORE_PATH=./chroma_db
AI_RAG_TOP_K=5
AI_RAG_SCORE_THRESHOLD=0.5
AI_RAG_MAX_HISTORY_TURNS=5
```

## 使用流程

1. **启动系统**：运行 `start.bat` 或分别启动后端和前端服务
2. **登录**：访问对应端口的前端页面，使用默认账号登录
3. **AI客服**：用户在服务台首页与AI客服对话，获取问题解决方案
4. **转人工**：AI无法解决时，一键转人工创建工单
5. **客服接单**：客服在ITSM端查看工单池，点击接单
6. **处理工单**：客服与用户通过实时聊天沟通，解决问题后标记为已解决
7. **用户评价**：用户对已解决的工单进行评价
8. **知识库同步**：管理员在后台管理同步知识库，AI客服自动学习解决方案

## 项目结构

```
program_last/
├── backend/                 # 后端服务
│   ├── app/
│   │   ├── ai/            # AI模块 (RAG, LLM, Embedding, 记忆)
│   │   ├── api/           # API路由
│   │   ├── models/        # 数据模型
│   │   ├── schemas/       # Pydantic Schemas
│   │   ├── services/      # 业务逻辑
│   │   └── utils/         # 工具函数
│   ├── chroma_db/         # 向量数据库
│   ├── faq_docs/          # FAQ文档
│   ├── tests/             # 测试用例
│   └── run.py             # 启动入口
├── frontend-client/         # 用户服务台 :5173
├── frontend-agent/          # ITSM客服端 :5174
├── frontend/                # 后台管理 :5175
├── frontend-ops/            # OPS统计 :5176
└── shared/                  # 共享组件库
```

## API概览

| 模块 | 端点数 | 主要功能 |
|------|--------|----------|
| 认证 | 3 | 登录、获取当前用户 |
| ITSM | 18 | 工单CRUD、生命周期、转派、取消、催办、SLA控制 |
| 聊天 | 7 | 房间管理、消息、WebSocket、已读状态 |
| 后台 | 15 | 用户管理、权限、分类CRUD |
| OPS | 7 | 统计、导出、趋势分析 |
| 上传 | 1 | 文件上传（图片/文档） |
| 模板 | 4 | 快捷回复模板 |
| **AI** | **3** | **AI聊天、知识库同步、知识库状态** |
