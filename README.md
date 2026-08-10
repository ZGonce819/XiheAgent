# Xihe (羲和) — AI Coding Assistant

羲和是一个基于 FastAPI + LangGraph + DeepAgents 构建的 AI 编码助手，支持 Gitee 仓库集成、多轮对话、代码审查、自动化任务编排等功能。

## 核心特性

- 🤖 **智能 Agent 编排** - 基于 LangGraph + DeepAgents v3，支持复杂任务的自动分解与执行
- 🔄 **SSE 实时流式输出** - 支持 Redis Stream 事件总线或 asyncio.Queue，前端实时展示 Agent 思考过程
- 💾 **三层存储架构** - Redis（Checkpoint）+ MySQL（业务数据）+ PostgreSQL（长期记忆）
- 🔐 **企业级安全** - 虚拟路径隔离、命令白名单、Git 分支保护、RBAC 权限控制（规划中）
- 📊 **Gitee 深度集成** - 自动创建 PR、添加 Code Review 评论、同步代码变更
- 🧠 **长期记忆系统** - 工作区级和仓库级记忆，支持跨会话知识积累
- 🎯 **意图识别路由** - 自动区分编码任务、方案生成、同步操作等不同流程
- 🖥️ **现代化前端** - Vue 3 + Vite + TanStack Router，响应式设计，支持深色模式

## 技术栈

### 后端

| 组件 | 技术选型 |
|------|----------|
| Web 框架 | FastAPI + Uvicorn |
| Agent 框架 | LangGraph + DeepAgents v3 |
| 语言模型 | DeepSeek API (deepseek-v4-pro) |
| 业务数据存储 | MySQL (pymysql) - Thread/Run/Finding 等 |
| 长期记忆存储 | PostgreSQL (psycopg) - LangGraph Store |
| Checkpoint 存储 | Redis (langgraph-checkpoint-redis) |
| SSE 事件总线 | Redis Stream（可选）或 asyncio.Queue |
| 代码托管集成 | Gitee API |
| Python 版本 | >= 3.11 |

### 前端

| 组件 | 技术选型 |
|------|----------|
| 框架 | Vue 3 + Vite |
| 状态管理 | Pinia |
| 路由 | TanStack Router |
| HTTP 客户端 | Axios |
| Markdown 渲染 | markdown-it + DOMPurify |
| 构建工具 | Vite 7 |

## 快速开始

### 前置依赖

项目需要以下数据库服务运行：

| 服务 | 用途 | 必需性 |
|------|------|--------|
| **Redis** | LangGraph Checkpoint（Agent 运行状态） | ✅ 必需 |
| **MySQL** | 业务数据存储（Thread/Run/Finding） | ✅ 必需 |
| **PostgreSQL** | LangGraph Store（长期记忆） | ⚠️ 可选（不使用 memories 功能可跳过） |
| **OpenSandbox** | 内核级代码沙盒隔离（企业多租户场景） | ⚠️ 可选（默认使用本地后端） |

#### 快速安装（Windows + Docker）

```bash
# 使用 Docker Compose 一键启动所有数据库
docker-compose up -d

# 或单独启动
docker run -d --name redis -p 6379:6379 redis:7-alpine
docker run -d --name mysql -p 3306:3306 -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=xihe mysql:8
docker run -d --name postgres -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=xihe postgres:16
```

#### 手动安装

**Redis**（Windows）：
- 下载 Redis for Windows: https://github.com/microsoftarchive/redis/releases
- 或使用 WSL2: `sudo apt install redis-server && sudo service redis-server start`

**MySQL**：
```sql
CREATE DATABASE xihe CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- 后端启动时会自动建表，无需手动执行 DDL
```

**PostgreSQL**：
```sql
CREATE DATABASE xihe;
-- 后端启动时会自动初始化 schema
```

### OpenSandbox 沙盒环境（可选，仅 Linux）

OpenSandbox 提供内核级隔离的代码执行环境，适用于企业多租户场景。需要在 Linux 环境下通过 Docker 部署。

#### 1. 安装 Docker（Linux）

```bash
# CentOS/RHEL
wget -O /etc/yum.repos.d/docker-ce.repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
yum list docker-ce.x86_64 --showduplicates | sort -r
yum -y install docker-ce-26.1.4-1.el7

# 设置开机启动并启动 Docker
systemctl enable docker
systemctl start docker

# 配置镜像加速（编辑 /etc/docker/daemon.json）
{
    "features": {
        "containerd-snapshotter": true
    },
    "exec-opts": ["native.cgroupdriver=systemd"],
    "registry-mirrors": [
        "https://docker.m.daocloud.io",
        "https://docker.rainbond.cc",
        "https://docker.lmirror.top",
        "https://docker-0.unsee.tech",
        "https://docker.hlmirror.com"
    ]
}

# 重启 Docker
systemctl restart docker
```

#### 2. 安装 OpenSandbox Python 包

```bash
# 创建 Python 环境（推荐使用 conda）
conda create --name sandbox python=3.11
conda activate sandbox

# 安装 OpenSandbox 组件
pip install opensandbox opensandbox-code-interpreter opensandbox-server
```

#### 3. 配置 OpenSandbox Server

```bash
# 生成配置文件
opensandbox-server init-config ~/.sandbox.toml --example docker-zh

# 编辑配置文件（修改 host 和 host_ip 为 Linux 节点 IP）
vi ~/.sandbox.toml
```

关键配置项：
```toml
[server]
host = "192.168.1.100"  # 改为你的 Linux 节点 IP
port = 8080
log_level = "INFO"

[docker]
network_mode = "bridge"
drop_capabilities = ["AUDIT_WRITE", "MKNOD", "NET_ADMIN", "NET_RAW", "SYS_ADMIN"]
no_new_privileges = true
pids_limit = 4096
secnomp_profile = ""
host_ip = "192.168.1.100"  # 改为你的 Linux 节点 IP

[ingress]
mode = "direct"
```

#### 4. 启动 OpenSandbox Server

```bash
# 启动服务
opensandbox-server

# 查看帮助
opensandbox-server -h
```

#### 5. 验证连接

在浏览器中访问：`http://192.168.1.100:8080/health`

如果返回 `{"status":"healthy"}`，说明沙盒服务正常运行。

#### 6. 拉取沙盒镜像（可选）

```bash
docker pull sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/code-interpreter:v1.0.2
```

#### 7. Xihe 项目启用沙盒

在 `.env.local` 中添加：
```ini
# 启用 OpenSandbox 沙盒模式
USE_SANDBOX=true
SANDBOX_HOST=192.168.1.100  # OpenSandbox Server 的 IP
SANDBOX_PORT=8080
```

然后安装沙盒依赖：
```bash
pip install -e ".[sandbox]"
```

**注意：** 如果不启用沙盒，Xihe 会自动降级使用 `LocalShellBackend`（本地 Shell 执行），适合开发和个人使用场景。

### 安装 Python 依赖

```bash
cd D:\Bemyself\Python\xihe
pip install -e ".[dev]"
```

### 配置环境变量

```bash
copy .env .env.local
# 编辑 .env.local，修改数据库连接信息和 API Key
```

关键配置项：
```ini
# DeepSeek API
DEEPSEEK_API_KEY=your-api-key-here

# Gitee Token
GITEE_TOKEN=your-gitee-token-here

# Redis（必需）
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_CHECKPOINT_DB=0

# MySQL（必需）
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=root
MYSQL_DATABASE=xihe

# PostgreSQL（可选，不使用 memories 可设为空）
PG_HOST=127.0.0.1
PG_PORT=5432
PG_USER=postgres
PG_PASSWORD=postgres
PG_DATABASE=xihe

# Redis Stream SSE（可选，默认使用 asyncio.Queue）
USE_REDIS_STREAM=false
REDIS_STREAM_DB=1

# OpenSandbox 沙盒配置（可选，仅企业多租户场景需要）
USE_SANDBOX=false  # 设为 true 启用沙盒模式
SANDBOX_HOST=192.168.1.100  # OpenSandbox Server IP
SANDBOX_PORT=8080
```

### 启动后端服务

```bash
# 方式一：使用脚本（推荐）
xihe-start

# 方式二：直接启动
uvicorn xihe.app:app --reload --host 127.0.0.1 --port 2024
```

### 启动前端服务

```bash
cd ui
npm install  # 首次需要安装依赖
npm run dev  # 启动 Vite 开发服务器，端口 3000
```

访问 http://127.0.0.1:3000 即可使用完整应用。

### 健康检查

```bash
curl http://127.0.0.1:2024/health
```

## 项目结构

```
xihe/
├── app.py                 # FastAPI 应用入口
├── server.py              # DeepAgent 工厂（组装模型/工具/中间件）
├── prompt.py              # 系统提示词构建
│
├── config/                # 配置管理
│   ├── app_config.py      # Settings 单例配置
│   ├── env_config.py      # 环境变量加载
│   └── logging_config.py  # 日志配置
│
├── agent/                 # Agent 核心模块
│   ├── graph/             # LangGraph 基础设施
│   │   ├── graph.py       # Agent 图构建
│   │   ├── model.py       # 语言模型管理
│   │   ├── persistence.py # Checkpoint 持久化（Redis/PostgreSQL）
│   │   ├── state.py       # Agent 状态定义
│   │   └── checkpoint_history.py  # 历史消息查询
│   │
│   ├── memory/            # 长期记忆
│   │   ├── workspace.py   # 工作区级记忆
│   │   ├── repo.py        # 仓库级记忆
│   │   └── repo_update.py # 记忆更新策略
│   │
│   ├── middleware/        # Agent 中间件
│   │   ├── context_injection.py  # 上下文注入
│   │   ├── memory_update.py      # 记忆更新触发
│   │   ├── message_sanitize.py   # 消息清洗
│   │   ├── tool_error.py         # 工具异常处理
│   │   ├── tool_sanitize.py      # 工具调用清洗
│   │   └── run_limits.py         # 运行限制
│   │
│   └── runtime/           # 任务编排与运行时
│       ├── task_router.py      # 主任务路由
│       ├── planning_router.py  # 方案生成路由
│       ├── sync_router.py      # 同步操作路由（git pull 等）
│       ├── task_intent.py      # 意图分类
│       ├── streaming_runtime.py # 事件流消费
│       ├── event_handlers.py   # 事件处理器
│       ├── events.py           # 事件记录
│       ├── repo_mapping.py     # 仓库目录映射
│       └── _helpers.py         # 内部辅助函数
│
├── api/                   # HTTP 接口层
│   ├── routes.py          # 基础路由（/health）
│   └── dashboard.py       # Dashboard 前端接口（SSE 流式输出）
│
├── backends/              # Backend 实现
│   ├── workspace.py       # 工作区操作
│   ├── local_shell.py     # Shell 命令执行
│   └── permissions.py     # 权限控制
│
├── tools/                 # 外部工具集成
│   ├── gitee_api.py       # Gitee REST API
│   ├── gitee_tools.py     # Gitee 工作流工具
│   ├── web_search.py      # 网络搜索
│   ├── fetch_url_tools.py # URL 内容抓取
│   └── reviewer_tools.py  # 代码审查工具
│
├── utils/                 # 通用工具函数
│   ├── http_utils.py      # SSRF 安全 HTTP 客户端
│   ├── context_utils.py   # LangGraph 运行时上下文提取
│   ├── reviewer_utils.py  # PR Diff 解析与位置验证
│   ├── id_utils.py        # UUID 生成工具 (thread/run/message/finding ID)
│   ├── time_utils.py      # ISO 时间戳转毫秒工具
│   ├── intent_utils.py    # 用户意图识别 (方案确认/修订判断)
│   └── message_utils.py   # LangChain 消息内容处理 (文本提取/方案抽取)
│
├── store/                 # 数据持久化（per-table Repo 拆分）
│   ├── _connection.py     # ConnectionManager 基类（连接/锁/重连）
│   ├── thread_repo.py     # threads 表 CRUD + 级联删除
│   ├── run_repo.py        # runs 表
│   ├── event_repo.py      # run_events 表
│   ├── message_repo.py    # thread_messages 表
│   ├── plan_repo.py       # thread_plans 表
│   ├── finding_repo.py    # review_findings 表
│   ├── setting_repo.py    # settings 表（键值存储）
│   ├── mapping_repo.py    # repo_workspace_mappings 表
│   ├── mysql_store.py     # LocalMySQLStore Facade（组合所有 Repo）
│   └── __init__.py        # Store 导出
│
├── models/                # 业务实体模型 (dataclass, per-table)
│   ├── _helpers.py        # 共享解析函数 (_to_iso, _parse_json, _parse_bool)
│   ├── thread.py          # Thread 实体
│   ├── run.py             # Run 实体
│   ├── event.py           # RunEvent 实体
│   ├── message.py         # ThreadMessage 实体
│   ├── plan.py            # ThreadPlan 实体
│   ├── finding.py         # ReviewFinding 实体
│   ├── setting.py         # Setting 实体
│   ├── mapping.py         # RepoWorkspaceMapping 实体
│   └── __init__.py        # 模型导出
│
├── schemas/               # API 请求/响应 Schema (Pydantic)
│   ├── request_schema.py  # Dashboard 请求体
│   ├── user_schema.py     # 用户信息响应
│   ├── options_schema.py  # 模型选项响应
│   ├── thread_schema.py   # 线程/消息/PR 响应
│   └── __init__.py        # Schema 导出
│
├── services/              # 业务逻辑层
│   ├── repo_service.py    # 仓库 URL 规范化
│   ├── thread_service.py  # Thread DTO 组装 + 状态映射
│   ├── streaming_service.py # SSE 流式编排（双模式：asyncio.Queue / Redis Stream）
│   ├── redis_stream.py    # Redis Stream 事件总线（可选启用）
│   └── __init__.py        # 服务导出
│
├── scripts/               # CLI 脚本
│   ├── start_all.py       # 启动脚本
│   └── stop_all.py        # 停止脚本
│
├── ui/                    # 前端项目（Vue 3 + Vite）
│   ├── src/
│   │   ├── api/           # API 客户端（axios + SSE 解析）
│   │   ├── components/    # Vue 组件
│   │   ├── routes/        # TanStack Router 路由
│   │   ├── stores/        # Pinia 状态管理
│   │   └── styles/        # 样式文件
│   ├── package.json       # 前端依赖
│   └── vite.config.js     # Vite 配置（代理到后端）
│
└── reviewer_rules/        # 代码审查规则
    └── default_review_rules.md
```

## 核心概念

### Agent 工厂 (`server.py`)

`get_agent(config)` 根据配置创建完整的 DeepAgent 实例，将模型、工具、子 Agent、Backend、记忆、中间件和 Checkpoint 组装在一起。每次 API 调用都会获取一个新的 Agent runnable，真正的会话状态由 LangGraph checkpointer 保存。

### 任务路由 (`agent/runtime/`)

任务路由决定每轮用户输入走哪条流程：

- **直达分支**：git pull / workspace listing 等不需要 LLM 的操作
- **方案生成**：`planning_router.py` 生成技术方案，等待用户确认
- **编码执行**：`task_router.py` 执行实际的编码任务

### 长期记忆 (`agent/memory/`)

- **工作区记忆**：跨仓库的全局上下文
- **仓库记忆**：每个仓库的专属知识，任务结束后自动更新

### 中间件 (`agent/middleware/`)

中间件在 Agent 运行前后执行，负责上下文注入、消息清洗、工具异常捕获、运行限制等横切关注点。

## API 接口

### 基础接口

```
GET  /health                    # 健康检查
```

### Dashboard 接口

```
GET  /dashboard/api/me          # 当前用户信息
GET  /dashboard/api/options     # 页面选项（模型列表等）
GET  /dashboard/api/threads     # 会话列表
GET  /dashboard/api/threads/{id} # 会话详情
POST /dashboard/api/threads/stream-message         # 创建新会话
POST /dashboard/api/threads/{id}/stream-message    # 追加对话轮次
DELETE /dashboard/api/threads/{id}                 # 删除会话
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | *必填* |
| `GITEE_TOKEN` | Gitee 访问令牌 | *必填* |
| `XIHE_DATA_DIR` | 数据文件目录 | `{project_root}/data` |
| `XIHE_LOG_DIR` | 日志文件目录 | `{project_root}/logs` |
| `XIHE_LOG_LEVEL` | 日志级别 | `INFO` |
| `AI_WORKSPACE_ROOT` | 代码工作区根目录 | `E:/ai_workspace` |
| `REDIS_HOST` | Redis 主机地址（Checkpoint） | `localhost` |
| `REDIS_PORT` | Redis 端口 | `6379` |
| `REDIS_PASSWORD` | Redis 密码（可选） | *无* |
| `REDIS_CHECKPOINT_DB` | Checkpoint 使用的 Redis DB | `0` |
| `MYSQL_HOST` | MySQL 主机地址（业务数据） | `localhost` |
| `MYSQL_PORT` | MySQL 端口 | `3306` |
| `MYSQL_USER` | MySQL 用户名 | `root` |
| `MYSQL_PASSWORD` | MySQL 密码 | *必填* |
| `MYSQL_DATABASE` | MySQL 数据库名（业务数据） | `xihe` |
| `PG_HOST` | PostgreSQL 主机地址（长期记忆） | `localhost` |
| `PG_PORT` | PostgreSQL 端口 | `5432` |
| `PG_USER` | PostgreSQL 用户名 | `postgres` |
| `PG_PASSWORD` | PostgreSQL 密码 | *必填* |
| `PG_DATABASE` | PostgreSQL 数据库名（LangGraph Store） | `xihe` |
| `USE_SANDBOX` | 是否启用 OpenSandbox 沙盒模式 | `false` |
| `SANDBOX_HOST` | OpenSandbox Server IP（仅沙盒模式需要） | *无* |
| `SANDBOX_PORT` | OpenSandbox Server 端口（仅沙盒模式需要） | `8080` |

## 开发命令

```bash
# 代码格式化
black xihe/
isort xihe/

# 代码检查
ruff check xihe/
ruff check --fix xihe/

# 类型检查
mypy xihe/

# 运行测试
pytest tests/ -v
```

## 日志

日志文件位于 `{log_dir}` 目录下：

- `backend.log` — 后端服务日志
- `agent-runs.log` — Agent 运行日志
- `backend.log.YYYY-MM-DD` — 历史日志（按天轮转）
