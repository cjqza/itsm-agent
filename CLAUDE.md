# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

公司桌面IT服务台 (Company Desktop IT Service Desk) — an IT ticket management platform with four separate frontends, one shared backend, and an AI-powered intelligent customer service system. Users submit repair requests via a chat-like interface, agents pick up tickets manually, and both parties communicate through a built-in WebSocket chat system.

## Quick Start

```bash
# One-click start (all services)
start.bat

# Or start individually:
cd backend && python run.py                    # Backend on :8000
cd frontend-client && npm install && npm run dev  # User Service Desk on :5173
cd frontend-agent && npm install && npm run dev   # ITSM (Agent) on :5174
cd frontend && npm run dev -- --port 5175         # Admin Panel on :5175
cd frontend-ops && npm install && npm run dev     # OPS Statistics on :5176

# Stop all
stop.bat

# Reset database (delete SQLite file + re-seed)
rm backend/it_ops.db && cd backend && python seed_data.py

# Run tests (requires backend running on :8000)
cd backend && python tests/test_api.py    # ~73 cases, ~3.5min

# Docker deployment
docker-compose up -d
```

**Test mode for API calls**: Add header `X-Test-Mode: true` (localhost only) to skip CAPTCHA in automated tests.

## Architecture

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ frontend-    │  │ frontend-    │  │ frontend/    │  │ frontend-    │
│ client/      │  │ agent/       │  │ (admin)      │  │ ops/         │
│ :5173        │  │ :5174        │  │ :5175        │  │ :5176        │
│ 用户服务台    │  │ ITSM客服端   │  │ 后台管理      │  │ OPS统计      │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │                 │
       └─────────────────┴────────┬────────┴─────────────────┘
                                  │ /api proxy
                         ┌────────▼────────┐
                         │   backend/      │
                         │   FastAPI :8000 │
                         │   SQLite/MySQL  │
                         └─────────────────┘
                                  │
                         ┌────────▼────────┐
                         │   shared/       │
                         │   Vue 共享层    │
                         └─────────────────┘
```

### Backend (`backend/`)

- **Framework**: FastAPI with async SQLAlchemy (aiosqlite for dev, aiomysql for prod)
- **Auth**: JWT tokens + bcrypt password hashing + CAPTCHA + account locking + IP fail limit
- **Config**: `app/config.py` — Pydantic Settings, reads `.env`, cached via `@lru_cache`
- **Database**: `app/database.py` — `get_db()` dependency yields a session. **No auto-commit** — all endpoints must explicitly `await db.commit()`.
- **API routes**: `app/api/` — `auth.py`, `itsm.py`, `chat.py`, `admin.py`, `ops.py`, `upload.py`, `templates.py`, `captcha.py`
- **Services**: `app/services/` — `ticket_service.py` (core ticket logic), `sla_service.py` (SLA timer checks)
- **Models**: `app/models/` — `user.py`, `ticket.py`, `category.py`, `permission.py`, `chat.py`, `template.py`, `audit_log.py`
- **Cache**: `app/utils/redis.py` — Redis cache with automatic fallback to in-memory when Redis unavailable
- **WebSocket**: Two separate WS systems — global notification WS in `utils/websocket.py`, chat-specific WS in `api/chat.py`. Per-user connection limit: 5.
- **Background tasks**: `app/tasks/sla_checker.py` — APScheduler runs every minute to update SLA status colors
- **AI module**: `app/ai/` — RAG pipeline with ChromaDB + BGE embeddings + LLM (Qwen2.5/DeepSeek) + Session Memory

### Frontend Shared Layer (`shared/`)

Four frontends share common code via `@shared/` alias (configured in each `vite.config.js`):

| Module | Purpose |
|--------|---------|
| `shared/utils/status.js` | `statusType`, `statusText`, `priorityType`, `slaColor`, `slaText`, `slaTagType`, `slaColorByPercent` |
| `shared/utils/format.js` | `formatTime`, `formatShortTime`, `formatMsgTime`, `utcToDate` |
| `shared/api/request.js` | `createApiClient()` — axios instance with token injection + 401/403/error handling |
| `shared/stores/user.js` | `createBaseStore(authApi)` — base Pinia store for login/logout/fetchMe |
| `shared/composables/useWebSocket.js` | WS connection + heartbeat + exponential backoff reconnect |
| `shared/components/BaseLogin.vue` | Configurable login component (props: title/color/showRegister/showForgotPassword/captchaApi) |
| `shared/components/ChatMessage.vue` | Chat message renderer (system/text/image/file, mine/other bubbles) |
| `shared/components/ChatInput.vue` | Chat input area with file upload |

Each frontend extends the base store with its own computed properties (admin adds WS connection, agent adds hasItsm, etc.).

### Agent Architecture (`.claude/agents/`)

| Agent | Model | Role |
|-------|-------|------|
| `coder` | Opus | Orchestrator: analyze → delegate to front/backend → review code → run tests → git commit. Does NOT write business code directly. |
| `front` | Sonnet | Frontend specialist: Vue/JS code in `frontend*/src/` and `shared/` |
| `backend` | Sonnet | Backend specialist: Python code in `backend/` |
| `initializer` | Sonnet | CHANGELOG.md, FEATURES.md, git operations |
| `pm` | Opus | Product analysis, PRD, competitive analysis |

**Workflow**: User request → `coder` analyzes and delegates to `front`/`backend` → `coder` reviews + tests + commits → `initializer` updates logs.

**Key rule**: `coder` must NOT write business code directly. It delegates to `front`/`backend`, reviews their work, runs tests, and commits. This ensures code quality through separation of concerns.

## Ticket Lifecycle

```
pending → accepted → processing → resolved_pending_review → resolved
  (池)     (接单)     (处理中)        (待评价)              (已解决)
```

- `pending`: Created by user, visible in agent's shared pool
- `accepted`: Agent manually accepts → auto-creates ChatRoom
- `processing`: Agent working on it
- `resolved_pending_review`: Agent marks resolved → user sees rating prompt
- `resolved`: User rates → chat room auto-closes

**状态流转验证**: `VALID_TRANSITIONS` dict in `ticket_service.py` enforces legal transitions only.

SLA color coding: green (normal) → yellow (30%+) → red (50%+) → black (overdue). SLA paused/resumed via dedicated endpoints.

## Authentication System

### Login
- `POST /api/auth/login` — `{account, password, captcha_id, captcha_text}` (account = login_id or phone)
- **CAPTCHA always required** — every login attempt must include valid captcha
- Account lockout: 5 failed attempts → **permanently locked** (needs admin to unlock via `PUT /api/admin/users/{id}/unlock`)
- IP fail limit: 10 failed login attempts per IP per 5 minutes → 429 "请求过于频繁"
- Unified error: "账号或密码错误" (prevents account enumeration)

### Registration
- `POST /api/auth/register` — `{name, phone, password, captcha_id, captcha_text}`
- Auto-creates user with ACTIVE status + auto-generates login_id (U00001 format)
- Returns token immediately (register = login)

### Forgot Password
- `POST /api/auth/reset-password` — `{name, phone, captcha_id, captcha_text, new_password}`
- Validates: CAPTCHA → name+phone match → new password ≠ old password
- Reserved `sms_code` field for future SMS integration

### Unlock Account
- `PUT /api/admin/users/{id}/unlock` — admin_access required, resets fail count and lock

### Permission Model
- Three flags: `itsm_access`, `ops_access`, `admin_access`
- `admin_access` can only be modified by `super_admin` (others get 403)
- `require_permission("field")` dependency in `app/utils/auth.py` with 60s Redis/memory cache
- `has_permission(user, field)` helper for inline permission checks (reuses cache)
- Admins and super_admins auto-grant all permissions

### Admin User Management
- `GET /api/admin/users` — returns users with permission fields (joined with Permission table)
- `POST /api/admin/agents/upgrade?user_id=X` — upgrade existing user to agent (grants itsm+ops)
- `POST /api/admin/agents/downgrade?user_id=X` — downgrade agent to user (revokes itsm+ops)
- `PUT /api/admin/users/{id}/unlock` — unlock locked account
- Frontend: "设置" dialog allows editing user info and toggling permissions via switches

### Key Credentials
| Role | login_id | phone | password |
|------|----------|-------|----------|
| Super Admin | `admin` | `10000000000` | `admin123` |
| Agent (张三) | `U00001` | `13900000001` | `123456` |
| User (刘一) | `U00006` | `13900010001` | `123456` |

All logins require CAPTCHA. In tests, use `X-Test-Mode: true` header to bypass.

## AI Intelligent Customer Service

The system includes a RAG (Retrieval-Augmented Generation) AI chatbot with **three-layer session memory**.

### Architecture
```
User question → Session Memory → Embedding → ChromaDB search → BGE-Reranker → LLM → Answer
                    │
                    ├─ Sliding Window (last 5 turns, exact replay)
                    ├─ Summary (old conversations condensed)
                    └─ Metadata (device model, OS, issue category, scenario)
```

### Components (`backend/app/ai/`)
- `memory.py` — `SessionMemoryManager`: Redis-backed session memory (TTL 30min, fallback to in-memory dict). Three layers: sliding window (10 messages), auto-summary (triggered at 8+ messages), metadata extraction (every 3 turns via LLM)
- `rag.py` — `RAGPipeline`: retrieve → dedup docs against history → build_messages → generate/stream. Integrates memory manager. Sanitizes LLM output to strip fake conversation turns
- `llm.py` — LLM abstraction: GGUF (ctransformers), Transformers (HuggingFace), DeepSeek (API). All return `{answer, thinking}` with `<think>` tag parsing
- `embeddings.py` — BGE-small-zh-v1.5 (local CPU) or OpenAI API
- `vectorstore.py` — ChromaDB persistent storage
- `knowledge.py` — Knowledge base builder (tickets + FAQ docs)
- `prompts.py` — Prompt templates with anti-repetition and anti-simulation instructions
- `models.py` — Pydantic schemas: `AIChatRequest` (question, history, stream, session_id), `AIChatResponse` (answer, thinking, sources)

### API Endpoints
- `POST /api/ai/chat` — AI chat (supports SSE streaming, session_id for memory)
- `POST /api/ai/knowledge/sync` — Sync knowledge base (admin only)
- `GET /api/ai/knowledge/status` — Knowledge base status

### SSE Event Types (streaming)
`sources` → `thinking` → `token` (×N) → `done` | `error`

### Anti-Repetition / Anti-Simulation
The prompts include explicit instructions to prevent:
1. **Repeating previous answers** — "不要重复对话历史中已经提供过的建议"
2. **Simulating user feedback** — "禁止代替用户说话，只基于用户实际发送的内容回答"
3. **Generating fake conversation turns** — "只输出你自己的一条回复，绝对不要输出 `<|user|>` 等角色标签"
4. **Repetitive closings** — "禁止结尾写多句感谢/祝福/告别，最多一句话结尾"
5. **Post-processing sanitizer** — `_sanitize_answer()` and streaming filter detect and truncate `<|user|>`, `<|assistant|>`, `User:`, `Assistant:` etc.

### Configuration
```env
AI_LLM_PROVIDER=deepseek          # or transformers/gguf
AI_LLM_MODEL_NAME=deepseek-chat
AI_LLM_API_KEY=sk-xxx
AI_EMBEDDING_PROVIDER=bge
AI_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
AI_VECTORSTORE_PATH=./chroma_db
AI_RAG_TOP_K=5
AI_RAG_SCORE_THRESHOLD=0.5
AI_RAG_MAX_HISTORY_TURNS=5
```

### Knowledge Base
- Auto-syncs from resolved tickets
- Manual FAQ documents in `backend/data/faq/` (Markdown, `##` headings)
- SOP documents in `backend/data/sop/`

## Key Patterns

**`_ticket_to_dict` safety**: When converting Ticket ORM objects to dicts, always use the `safe_rel_name()` helper that checks `ticket.__dict__` before accessing relationships — accessing unloaded relationships in async SQLAlchemy raises `MissingGreenlet`.

**Route order**: Static routes (e.g. `/tickets/sla-warnings`) must be defined before dynamic routes (e.g. `/tickets/{ticket_id}`) or FastAPI will match the wrong endpoint.

**UNIQUE constraint on chat_rooms.ticket_id**: Always check for existing room before creating — see `ticket_service.py accept_ticket()`.

**Redis fallback**: All Redis operations (`redis.py`) wrap in try/except and fall back to in-memory storage when Redis is unavailable. The system works identically with or without Redis.

**CAPTCHA test mode**: Requests with `X-Test-Mode: true` header (localhost only) skip CAPTCHA verification in tests.

**Explicit commit**: `get_db()` does NOT auto-commit. Every write endpoint must call `await db.commit()` explicitly. Do not rely on implicit commits.

**Permission cache**: `require_permission()` caches permission checks for 60s (Redis or memory). After modifying permissions via `update_permission`, call `await _invalidate_perm_cache(user_id)` to clear the cache.

**`has_permission()` helper**: Use `from app.utils.auth import has_permission` for inline permission checks that reuse the cache (e.g. in `itsm.py`'s `_has_itsm_access`).

**Login always requires CAPTCHA**: `LoginRequest` requires `captcha_id` and `captcha_text`. Frontend must load captcha via `GET /api/auth/captcha` before showing login form.

**ChromaDB embedding**: Always pass `embedding_function=NullEmbeddingFunction()` (512-dim) to `get_or_create_collection` to avoid ChromaDB downloading its default onnx model.

**AI memory system**: `SessionMemoryManager` stores sessions in Redis (key: `ai:session:{id}`, TTL 1800s). The `_build_messages()` method assembles: `[system_prompt + memory_context] → [sliding_window] → [current_question + RAG_docs]`. RAG docs are deduplicated against the sliding window to prevent LLM from repeating previous answers.

## Common Issues

**Port 8000 in use**: Multiple Python processes accumulate. Use `stop.bat` or manually `taskkill /F /PID <pid>`.

**Backend 500 errors**: Usually caused by accessing unloaded SQLAlchemy relationships in async context. Use `__dict__` checks.

**SQLite timezone issues**: SQLite returns timezone-naive datetimes. Use the `_ensure_utc()` helper in `sla_service.py`.

**SLA pause/resume**: If you see "can't subtract offset-naive and offset-aware datetimes", ensure `_ensure_utc()` is being used.

**alembic.ini encoding**: On Windows, `alembic.ini` must be GBK encoded (not UTF-8) or alembic will fail with `UnicodeDecodeError`.

**bcrypt warning**: `passlib 1.7.4` + `bcrypt 4.x` shows `AttributeError: module 'bcrypt' has no attribute '__about__'` — this is a known compatibility warning, functionality works correctly.

**Missing commit**: If data seems to not persist, check that the endpoint calls `await db.commit()`. `get_db()` does NOT auto-commit.

**Vue component errors**: If a page shows "页面出现异常，请刷新重试", check for missing icon imports (`@element-plus/icons-vue`) or uncaught async errors in `openXxxDialog` functions (add try-catch).

**UTC vs local time**: Backend stores UTC timestamps without timezone suffix. Frontend `shared/utils/format.js` uses `dayjs.utc(t).local()` to convert. Dashboard/TicketDetail use `utcToDate()` helper for SLA calculations.

**Test pass rate: 73/73 (100%)**. All tests must pass before committing.

## Environment Variables

Copy `backend/.env.example` to `backend/.env`. Key vars:
- `DB_TYPE`: `sqlite` (default) or `mysql`
- `JWT_SECRET_KEY`: Change in production
- `REDIS_URL`: Redis connection (empty = use in-memory fallback)
- `TRUST_PROXY`: `false` (default) — set to `true` behind reverse proxy to trust X-Forwarded-For
- `CORS_ORIGINS`: Comma-separated allowed origins (default: localhost ports)
- `AI_LLM_PROVIDER`: `deepseek` (default) or `transformers` or `gguf`
- `AI_LLM_API_KEY`: DeepSeek API key (required for `deepseek` provider)
- `AI_EMBEDDING_PROVIDER`: `bge` (default) or `openai`
- `AI_VECTORSTORE_PATH`: ChromaDB storage path (default: `./chroma_db`)
