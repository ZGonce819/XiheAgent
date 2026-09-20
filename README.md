# XiheAgent (羲和) — AI Coding Assistant

![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)

羲和（XiheAgent）是一个基于 FastAPI + LangGraph + DeepAgents 构建的 AI 编码助手，支持 Gitee / GitHub / Codeup 多平台仓库集成、多轮对话、代码审查、自动化任务编排等功能。

## 核心特性

- 🤖 **智能 Agent 编排** - 基于 LangGraph + DeepAgents，支持复杂任务的自动分解与执行
- 🔄 **SSE 实时流式输出** - 支持 asyncio.Queue（单进程）或 Redis Stream（多进程部署）事件分发
- 💾 **三层存储架构** - Redis（Checkpoint）+ MySQL（业务数据）+ PostgreSQL（长期记忆）
- 🔐 **企业级安全** - 虚拟路径隔离、命令白名单、Git 分支保护、RBAC 权限控制（规划中）
- 📊 **多平台 SCM 集成** - 支持 Gitee / GitHub / Codeup，自动检测平台并路由到对应工具
- 🧠 **长期记忆系统** - 工作区级和仓库级记忆，支持跨会话知识积累
- 🎯 **意图识别路由** - 自动区分编码任务、方案生成、同步操作等不同流程
- 🎛️ **自定义模型配置** - 支持通过 API 配置自定义模型（API Key/Base URL/模型名），可在对话中切换
- 🔒 **会话环境隔离** - 自动追踪会话创建环境（local/sandbox），防止跨环境继续对话
- 📁 **本地文件夹绑定** - 项目可绑定本地文件夹作为工作根目录，Agent 直接在用户项目中工作
- ⏰ **自动化任务编排** - 自动化任务按「一轮一轮」跑：每轮读上一轮状态、跑完独立验证、把结论写回状态，连续没通过就停下交给人；支持按 cron 自动触发，也可以手动跑一轮；只读任务直接跑，写入任务配好验证检查项后也能跑
- 🧩 **四层技能系统** - 内置 / 全局 / 项目集合 / 本地四层作用域，同名逐层覆盖；支持网页上传与 Agent 自动沉淀
- 🖥️ **现代化前端** - React + Vite + Tailwind CSS + TanStack Router，响应式设计，支持深色模式

## 技术栈

### 后端

| 组件 | 技术选型 |
|------|----------|
| Web 框架 | FastAPI + Uvicorn |
| Agent 框架 | LangGraph + DeepAgents |
| 语言模型 | OpenAI 兼容接口（支持自定义模型配置） |
| 业务数据存储 | MySQL (pymysql) - Thread/Run/Finding 等 |
| 长期记忆存储 | PostgreSQL (psycopg) - LangGraph Store |
| Checkpoint 存储 | Redis (langgraph-checkpoint-redis) |
| SSE 事件总线 | asyncio.Queue / Redis Stream（USE_REDIS_STREAM=true 启用） |
| 代码托管集成 | Gitee / GitHub / Codeup API（自动平台路由） |
| Python 版本 | >= 3.11 |

### 前端

| 组件 | 技术选型 |
|------|----------|
| 框架 | React 19 + TanStack Start（SPA 模式）+ Vite 8 |
| 状态管理 | TanStack Query（服务端状态）+ 组件内 `useState` |
| 路由 | TanStack Router（文件式路由，路由树由插件生成） |
| HTTP 客户端 | Fetch API |
| CSS 框架 | Tailwind CSS v4 |
| Markdown 渲染 | react-markdown + remark-gfm |
| 语法高亮 | Shiki |
| 代码 Diff | `diff` + `@pierre/diffs` |
| 构建与离线壳 | Vite 8 + Nitro；`vite-plugin-pwa` 生成 PWA 清单 |
| 组件与测试 | base-ui + Phosphor Icons + Lucide + Vitest / Testing Library |

## 快速开始

### 前置依赖

项目需要以下数据库服务运行：

| 服务 | 用途 | 必需性 |
|------|------|--------|
| **Redis** | LangGraph Checkpoint（Agent 运行状态） | ✅ 必需 |
| **MySQL** | 业务数据存储（Thread/Run/Finding） | ✅ 必需 |
| **PostgreSQL** | LangGraph Store（长期记忆） | ⚠️ 可选（不使用 memories 功能可跳过） |
| **OpenSandbox** | 内核级代码沙盒隔离（企业多租户场景） | ⚠️ 可选（默认使用本地后端） |

#### 快速安装（Docker）

```bash
# MySQL 8.4
docker run -d \
  -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=root \
  -v /home/user/data/mysql/conf:/etc/mysql/conf.d \
  -v /home/user/data/mysql/data:/var/lib/mysql:rw \
  -v /home/user/data/mysql/my.cnf:/etc/mysql/my.cnf \
  --name mysql \
  --restart=always \
  mysql:8.4.9

# Redis Stack（含 RedisInsight）
docker run -d \
  --restart=always \
  --name redis-stack \
  -p 6379:6379 \
  -p 8001:8001 \
  -v /home/user/data/redis/data:/data \
  -e REDIS_ARGS="--requirepass root" \
  redis/redis-stack:latest

# PostgreSQL
docker run -d \
  --name postgresql \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  -v /home/user/data/postgres/data:/var/lib/postgresql/18/docker \
  -v /home/user/data/postgres/logs:/var/log/postgresql \
  postgres:latest
```

> ⚠️ 上面这条 Redis 命令带 `--requirepass root`，**密码必须同时写进 `.env` 的 `REDIS_PASSWORD`**，否则后端连不上 Redis（`NOAUTH Authentication required`），checkpointer 建不出来，任何一轮对话都跑不完。而「手动安装」里 WSL2 的 `apt install redis-server` 默认**不设密码**，两种装法对 `.env` 的要求相反 —— 换装法时要记得同步改。
>
> 想确认当前实例到底要不要密码：`redis-cli -h 127.0.0.1 -p 6379 ping`。回 `PONG` 就是不要；回 `NOAUTH Authentication required.` 就是要（注意别把 `NOAUTH` 误当成「没设密码」，那正好是反的）。

#### 手动安装

**Redis**（Windows）：
- 下载 Redis for Windows: https://github.com/tporadowski/redis/releases
- 或使用 WSL2: `sudo apt install redis-server && sudo service redis-server start`

**MySQL**：
```sql
CREATE DATABASE `xihe-agent` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- 后端启动时会自动建表，无需手动执行 DDL
```

**PostgreSQL**：
```sql
CREATE DATABASE "xihe-agent";
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

#### 6. 拉取沙盒镜像

```bash
docker pull sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/code-interpreter:v1.0.2
```

#### 7. XiheAgent 启用沙盒

在 `.env` 中添加：
```ini
# 启用 OpenSandbox 沙盒模式
USE_SANDBOX=true
SANDBOX_HOST=http://192.168.1.100:8080  # OpenSandbox Server 完整地址（含协议和端口）
SANDBOX_API_KEY=your-secret-api-key  # 与 server 端 api_key 配置一致
SANDBOX_IDLE_TIMEOUT_DAYS=3          # 沙箱空闲超过此天数自动清理
SANDBOX_MAX_PER_THREAD=3             # 每个线程最多保留的沙箱数量
```

**注意：** 如果不启用沙盒，XiheAgent 会自动降级使用 `LocalShellBackend`（本地 Shell 执行），适合开发和个人使用场景。

### 安装 Python 依赖

在项目根目录（即本 README 所在目录）执行：

```bash
pip install -e ".[dev]"
```

### 配置环境变量

```bash
copy .env.example .env
# 编辑 .env，修改数据库连接信息和 API Key
```

关键配置项：
```ini
# DeepSeek API（使用自定义模型时可选）
DEEPSEEK_API_KEY=your-api-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com  # API 基础地址（必填）

# SCM 平台 Token（按需配置使用的平台）
GITEE_TOKEN=your-gitee-token-here
GITHUB_TOKEN=your-github-token-here
CODEUP_TOKEN=your-codeup-token-here

# Redis（必需）
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_CHECKPOINT_DB=0

# MySQL（必需）
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=root
MYSQL_DATABASE=xihe-agent

# PostgreSQL（可选，不使用 memories 可设为空）
PG_HOST=127.0.0.1
PG_PORT=5432
PG_USER=postgres
PG_PASSWORD=postgres
PG_DATABASE=xihe-agent

# Redis Stream SSE（设为 true 启用多进程模式，默认使用 asyncio.Queue）
USE_REDIS_STREAM=false
REDIS_STREAM_DB=1

# OpenSandbox 沙盒配置（可选，仅企业多租户场景需要）
USE_SANDBOX=false  # 设为 true 启用沙盒模式
SANDBOX_HOST=http://192.168.1.100:8080  # OpenSandbox Server 完整地址
SANDBOX_API_KEY=your-secret-api-key      # 与 server 端 api_key 配置一致
SANDBOX_IDLE_TIMEOUT_DAYS=3              # 沙箱空闲超过此天数自动清理
SANDBOX_MAX_PER_THREAD=3                 # 每个线程最多保留的沙箱数量
```

### 启动后端服务

```bash
# 方式一：使用脚本（推荐，会一并拉起前端）
xihe-agent-start

# 方式二：直接启动
uvicorn app:app --reload --host 127.0.0.1 --port 2024

# 停止（配合方式一的脚本启动时使用）
xihe-agent-stop
```

> 后端启动时会自动拉起自动化任务的常驻调度（可用 `XIHE_AUTOMATION_SCHEDULER=false` 关掉）。调度起不来不会拦住整个服务 —— 手动触发和其它功能都不依赖它。

### 启动前端服务

```bash
cd frontend
npm install  # 首次需要安装依赖
npm run dev  # 启动 Vite 开发服务器，端口 3000
```

访问 http://127.0.0.1:3000 即可使用完整应用。开发服务器会把 `/api/v1` 代理到后端（默认 `http://127.0.0.1:2024`）；后端换地址时用 `VITE_DASHBOARD_API_BASE_URL` 指定。注意这是**前端构建期**变量，写在仓库根的 `.env` 里不生效 —— Vite 的工作目录是 `frontend/`，要放在 `frontend/.env` 或直接作为命令的环境变量传进去。

### 健康检查

```bash
curl http://127.0.0.1:2024/health
```

## 项目结构

```
xihe-agent/
├── .env.example           # 环境变量模板
├── pyproject.toml         # Python 项目配置与依赖声明
├── app.py                 # FastAPI 应用入口
├── server.py              # DeepAgent 工厂（组装模型/工具/中间件）
├── prompt.py              # 系统提示词构建
│
├── database/              # 数据库连接管理
│   ├── mysql.py           # MySQL 连接池与 ConnectionManager 基类
│   ├── redis.py           # Redis 连接（Checkpoint / Stream）
│   ├── postgres.py        # PostgreSQL 连接（LangGraph Store）
│   └── __init__.py        # 数据库模块导出
│
├── config/                # 配置管理
│   ├── app_config.py      # Settings 单例配置
│   ├── env_config.py      # 环境变量加载
│   ├── model_config.py    # 自定义模型配置管理
│   ├── automation_config.py # 自动化任务定义（data/automations.json）
│   ├── automation_state.py  # 自动化任务跨轮状态（data/automation_state/<id>.json）
│   ├── approval_config.py # 命令审批策略配置
│   ├── approval_runtime.py # 审批运行时状态管理
│   ├── skill_retrieval_config.py # 技能检索开关配置
│   ├── workspace_config.py # 工作区配置管理
│   ├── constants.py       # 全局常量
│   └── logging_config.py  # 日志配置
│
├── agent/                 # Agent 核心模块
│   ├── graph/             # LangGraph 基础设施
│   │   ├── graph.py       # Agent 图构建
│   │   ├── model.py       # 语言模型管理
│   │   ├── persistence.py # Checkpoint 持久化（Redis/PostgreSQL）
│   │   ├── redis_checkpointer.py # Redis Checkpointer 实现
│   │   └── checkpoint_history.py  # 历史消息查询
│   │
│   ├── memory/            # 长期记忆
│   │   ├── workspace.py   # 工作区级记忆
│   │   ├── workspace.md   # 工作区记忆模板（随仓库分发）
│   │   ├── repo.py        # 仓库级记忆
│   │   └── repo_update.py # 记忆更新策略
│   │
│   ├── middleware/        # Agent 中间件
│   │   ├── context_injection.py  # 上下文注入
│   │   ├── memory_update.py      # 记忆更新触发
│   │   ├── message_sanitize.py   # 消息清洗
│   │   ├── tool_error.py         # 工具异常处理
│   │   ├── tool_sanitize.py      # 工具调用清洗
│   │   ├── tool_tracking.py      # 工具调用追踪
│   │   ├── run_limits.py         # 运行限制
│   │   ├── final_response.py     # 最终响应处理
│   │   └── skill_retrieval.py    # 技能检索中间件
│   │
│   └── runtime/           # 任务编排与运行时
│       ├── cancellation.py     # 运行中任务的协作式停止登记表（api/services/agent 三层共用）
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
├── agent/skills/          # 内置技能（仅作模板源，不直接挂载）
│   ├── code-review/       # 代码审查技能
│   ├── repo-bootstrap-analysis/ # 仓库引导分析技能
│   └── ai-coding-implementation/ # AI 编码实现技能
│
├── api/                   # HTTP 接口层
│   ├── routes.py          # 基础路由（/health）
│   └── dashboard/         # Dashboard 前端接口（按业务域拆分为子模块）
│       ├── settings.py    # 用户、模型、审批、检索设置
│       ├── schedules.py   # 自动化任务管理
│       ├── skills.py      # 技能管理
│       ├── threads.py     # 会话管理与 SSE 流式输出
│       └── workspace.py   # 工作目录与项目管理
│
├── backends/              # Backend 实现
│   ├── workspace.py       # 工作区操作
│   ├── local_shell.py     # Shell 命令执行
│   ├── opensandbox.py     # OpenSandbox 沙盒执行
│   ├── command_policy.py  # 命令审批策略引擎
│   └── permissions.py     # 权限控制
│
├── tools/                 # 外部工具集成
│   ├── gitee_tools.py     # Gitee 工作流工具
│   ├── github_tools.py    # GitHub 工作流工具
│   ├── codeup_tools.py    # Codeup 工作流工具
│   ├── web_search.py      # 网络搜索
│   ├── fetch_url_tools.py # URL 内容抓取
│   ├── reviewer_tools.py  # 代码审查工具
│   └── skill_tools.py     # 技能工具（create_skill / list_skills）
│
├── utils/                 # 通用工具函数
│   ├── platform_utils.py  # SCM 平台检测与工具路由
│   ├── gitee_utils.py     # Gitee URL 解析与 API
│   ├── github_utils.py    # GitHub URL 解析与 API
│   ├── codeup_utils.py    # Codeup URL 解析与 API
│   ├── http_utils.py      # SSRF 安全 HTTP 客户端
│   ├── context_utils.py   # LangGraph 运行时上下文提取
│   ├── reviewer_utils.py  # PR Diff 解析与位置验证
│   ├── id_utils.py        # UUID 生成工具 (thread/run/message/finding ID)
│   ├── time_utils.py      # ISO 时间戳转毫秒工具
│   ├── intent_utils.py    # 用户意图识别 (方案确认/修订判断)
│   ├── message_utils.py   # LangChain 消息内容处理 (文本提取/方案抽取)
│   ├── recycle_bin.py     # 代码回退回收站
│   ├── redis_utils.py     # Redis 通用工具
│   └── thread_utils.py    # 会话工具函数
│
├── store/                 # 数据持久化（per-table Repo 拆分）
│   ├── thread_repo.py     # threads 表 CRUD + 级联删除
│   ├── run_repo.py        # runs 表
│   ├── event_repo.py      # run_events 表
│   ├── finding_repo.py    # review_findings 表
│   ├── sandbox_repo.py    # sandbox_registry 表（线程-沙盒映射）
│   ├── project_folder_repo.py # project_local_folders 表（项目-本地文件夹绑定）
│   ├── mapping_repo.py    # repo_workspace_mappings 表
│   ├── conversation_version_repo.py # 会话版本快照
│   ├── mysql_store.py     # LocalMySQLStore Facade（组合所有 Repo）
│   └── __init__.py        # Store 导出
│
├── models/                # 业务实体模型 (dataclass, per-table)
│   ├── _helpers.py        # 共享解析函数 (_to_iso)
│   ├── thread.py          # Thread 实体
│   ├── run.py             # Run 实体
│   ├── event.py           # RunEvent 实体
│   ├── finding.py         # ReviewFinding 实体
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
│   ├── thread_service.py  # Thread DTO 组装 + 状态映射
│   ├── streaming_service.py # SSE 流式编排（asyncio.Queue / Redis Stream 双模式）
│   ├── skill_registry.py    # 技能注册表（四层作用域的唯一事实来源）
│   ├── skill_installer.py   # 技能安装器（薄封装 skill_registry）
│   ├── skill_retrieval.py   # 技能检索（BM25-lite 打分）
│   ├── sandbox_skill_sync.py # 技能变更后同步进沙箱（运行中的会话延迟同步）
│   ├── cron_schedule.py     # cron 表达式解析与命中判断
│   ├── automation_guard.py  # 「这个任务正在跑」的运行登记（调度与手动触发共用一份）
│   ├── automation_loop.py   # 自动化任务的一轮循环（读状态 → 跑一轮 → 验证 → 写状态）
│   ├── automation_verification.py # 循环的独立验证门（跑检查项，不靠模型自评；含容器内执行器）
│   ├── automation_runner.py # 循环接真实运行时的一侧（只读任务 + 配了检查项的写入任务）
│   ├── automation_scheduler.py # 按分钟扫描并启动到点的任务（进程内常驻，跨进程用 Redis 租约收敛）
│   ├── approval_vote.py     # 审批表决（三评审多数决）
│   ├── approval_explanation.py # 审批解释（调用模型生成命令说明）
│   ├── repo_rollback.py     # 代码回退与版本恢复
│   └── __init__.py        # 服务导出
│
├── scripts/               # CLI 脚本
│   ├── start_all.py       # 启动脚本
│   ├── stop_all.py        # 停止脚本
│   ├── start_backend.cmd  # Windows 后端启动脚本
│   ├── start_ui.cmd       # Windows 前端启动脚本
│   └── prune_orphan_automation_states.py # 收走没有对应定义的循环状态文件（默认归档，`--delete` 才删）
│
├── frontend/              # 前端项目（React + Vite）
│   ├── src/
│   │   ├── client.tsx     # 客户端入口
│   │   ├── router.tsx     # TanStack Router 实例
│   │   ├── routeTree.gen.ts # 路由树（由 Vite 插件生成，勿手改）
│   │   ├── components/
│   │   │   ├── AppShell.tsx        # 应用外壳（顶栏 + 侧边栏 + 用户菜单）
│   │   │   ├── SidebarUserMenu.tsx # 侧边栏用户菜单
│   │   │   ├── sidebar-layout.tsx  # 侧边栏布局
│   │   │   ├── agents/    # Agent 相关组件
│   │   │   │   ├── AgentThreadView.tsx    # 会话线程视图（含顶部技能入口与停止接线）
│   │   │   │   ├── AgentPromptBar.tsx     # 会话输入区
│   │   │   │   ├── AgentsHome.tsx         # Agent 首页
│   │   │   │   ├── AgentsSidebar.tsx      # Agent 侧边栏（项目分组可折叠）
│   │   │   │   ├── RepoSelector.tsx       # 仓库选择器
│   │   │   │   ├── ProjectSelector.tsx    # 项目选择器
│   │   │   │   ├── CommandApprovalDialog.tsx # 命令审批面板
│   │   │   │   ├── utils/diffUtils.ts     # Diff 解析工具
│   │   │   │   └── ported/              # 移植组件
│   │   │   │       ├── CloudPromptBar.tsx # 输入框组件（含「停止任务」按钮）
│   │   │   │       ├── MessageView.tsx    # 消息渲染组件
│   │   │   │       ├── Markdown.tsx / MarkdownTable.tsx # Markdown 渲染
│   │   │   │       ├── CodeBlock.tsx / DiffView.tsx     # 代码块与 Diff 视图
│   │   │   │       ├── ShellCommand.tsx / ToolExecution.tsx # 命令与工具执行展示
│   │   │   │       ├── TodoList.tsx / ReplyCard.tsx / Logo.tsx
│   │   │   │       └── index.ts
│   │   │   ├── skills/    # 技能相关组件
│   │   │   │   ├── SkillPanel.tsx         # 对话内技能面板（查看 + 上传）
│   │   │   │   ├── SkillPanel.test.tsx    # 面板行为矩阵回归测试
│   │   │   │   ├── SkillRow.tsx           # 技能行
│   │   │   │   └── SkillRow.test.tsx
│   │   │   └── ui/        # 通用 UI 组件
│   │   │       ├── select.tsx / select.test.tsx # 下拉选择组件
│   │   │       ├── button.tsx / input.tsx / textarea.tsx / label.tsx
│   │   │       ├── card.tsx / avatar.tsx / skeleton.tsx / switch.tsx
│   │   │       └── confirm-dialog.tsx     # 确认弹窗
│   │   ├── lib/           # API 客户端与数据层
│   │   │   ├── agents/    # Agent 相关 API
│   │   │   │   ├── api.ts               # API 调用
│   │   │   │   ├── queries.ts           # TanStack Query（含取消会话的 mutation）
│   │   │   │   ├── types.ts             # 类型定义
│   │   │   │   ├── threadReveal.ts      # 发送消息后的即时出现动画信号
│   │   │   │   ├── useThreadStream.ts   # SSE 流钩子
│   │   │   │   ├── cron.ts              # cron 表达式的可读描述
│   │   │   │   ├── messageGroups.ts     # 消息分组
│   │   │   │   ├── pendingPrompts.ts    # 待处理输入
│   │   │   │   └── useModelOptions.ts   # 模型选项钩子
│   │   │   ├── api.ts     # 通用 API 客户端
│   │   │   ├── profile.ts / session.ts / theme.ts / query.ts
│   │   │   ├── repo.ts    # 仓库 URL 规范化（与后端同口径）
│   │   │   └── utils.ts   # 前端工具函数
│   │   ├── routes/        # TanStack Router 路由
│   │   │   ├── __root.tsx            # 根路由（挂载 AppShell）
│   │   │   ├── index.tsx             # 首页
│   │   │   ├── agents.tsx            # Agent 布局路由
│   │   │   ├── agents/index.tsx      # Agent 首页路由
│   │   │   ├── agents/$threadId.tsx  # 会话详情路由
│   │   │   ├── automations.tsx       # 自动化任务页面
│   │   │   ├── skills.tsx            # 技能管理页面（作用域概览 + 创建/上传）
│   │   │   └── my-settings.tsx       # 个人设置页面
│   │   ├── styles.css     # 全局样式
│   │   └── styles/agents.css        # Agent 相关样式与统一动画类
│   ├── public/            # 静态资源（favicon / logo / robots.txt）
│   ├── assets/            # 应用图标与品牌图
│   ├── scripts/           # 走查与验证脚本（不参与构建）
│   ├── package.json       # 前端依赖与脚本
│   └── vite.config.ts     # Vite 配置（代理到后端）
│
├── data/                  # 运行时数据（JSON 配置与状态，已 gitignore）
│   ├── automations.json   # 自动化任务定义
│   ├── automation_state/  # 自动化任务跨轮状态
│   ├── custom_models.json # 自定义模型配置
│   ├── builtin_model.json # 内置模型覆盖配置
│   ├── command_approval.json          # 命令审批类别设置
│   ├── pending_command_approvals.json # 待审批命令
│   ├── skill_retrieval.json           # 技能检索开关
│   └── workspace_config.json          # 工作区路径配置
│
├── logs/                  # 日志文件（按天轮转）
│
├── DESIGN.md              # 设计文档
├── PRODUCT.md             # 产品文档
├── LICENSE                # Apache 2.0 许可证
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

方案确认与「再改一下」都要还原**用户最早提出的完整需求**，而这个需求不能指望方案消息自带（方案正文是模型直接产出的，身上没有 metadata）。所以它存在**会话**上（`threads.plan_source_prompt`）：写入端一次算好、同时用于写库与拼给模型的内容，读取端从会话补回 `metadata.source_prompt`。回退链（「最近一条非确认用户消息」）保留给没有这一列的老会话 —— 第一轮修订看不出差别，从第二轮起才会退化成「再改一下」。

### 停止运行中的一轮 (`agent/runtime/cancellation.py`)

输入框在运行中会变成「停止任务」按钮，对应 `POST /api/v1/threads/{id}/cancel`。这是**协作式**停止，不是强杀：

- Agent 跑在 SSE worker 线程里，Python 线程无法安全强杀，LangGraph 的流式迭代也必须在一致的位置退出，否则 checkpoint 与 `runs` 表会留下一条永远 `running` 的记录。
- 做法是按会话记一个停止请求，运行循环在**每两个协议事件之间**检查它，检查到就退出。所以接口返回时这一轮通常还没结束，前端要等 `thread_done` 事件拿终态。
- 语义是**不再继续下一步，不回滚**：当前正在生成的这一段模型输出会丢，已经写进 checkpoint 的内容保留。停止后消息卡片显示「任务已停止」。

几条边界：

- **没在跑就拒绝（409）**，不留悬空标志 —— 留了下次开跑会在第一个事件被停，看起来像「自己莫名停了」。
- **登记可重入**（`thread_id → 嵌套深度`），只有最外层才清残留标志。SSE worker 一进线程就登记（覆盖准备仓库那段，clone 可能要几十秒），Agent 运行层再登记一次；内层若也清，用户在准备阶段点的停止会被抹掉。
- **`cancelled` 不算失败**：它映射成 `blocked`，自动化任务的连续失败计数不会把一次主动停止算进去；对前端则映射成 `idle`，按钮自动消失。

### 长期记忆 (`agent/memory/`)

- **工作区记忆**：跨仓库的全局上下文
- **仓库记忆**：每个仓库的专属知识，任务结束后自动更新

### 中间件 (`agent/middleware/`)

中间件在 Agent 运行前后执行，负责上下文注入、消息清洗、工具异常捕获、运行限制等横切关注点。

### 自定义模型配置 (`config/model_config.py`)

系统支持通过 API 配置自定义模型，优先级如下：

1. **对话级指定**：用户在对话中选择的自定义模型
2. **默认自定义模型**：通过 API 设置为默认的自定义模型
3. **内置模型覆盖**：通过 API 修改的内置模型配置
4. **环境变量回退**：`.env` 中的 `DEEPSEEK_API_KEY` 等配置

自定义模型配置存储在 `data/custom_models.json`，包含：
- `name`: 显示名称
- `model_name`: 模型标识符（如 `deepseek-v4-pro`）
- `api_key`: API 密钥
- `api_base`: API 基础地址
- `is_default`: 是否为默认模型

### 会话环境隔离

系统自动追踪每个会话的创建环境（`backend_type` 字段）：
- `local`: 本地 Shell 执行环境
- `sandbox`: OpenSandbox 沙盒环境

当前端检测到会话环境与当前模式不匹配时，会显示只读提示并禁用输入，防止跨环境继续对话导致的问题。

两个环境的**技能目录语义不同**：本地环境按项目分层（全局 / 项目集合 / 本地三层，见下节「技能系统」）；沙箱环境少一层「本地」——容器里没有项目本地文件夹，全局层与项目集合层都会同步进容器。因此技能面板在两种环境下都能看到「全局」与「项目」，只有「本地」在沙箱下不可用。

### 本地文件夹模式

项目可以绑定一个本地文件夹作为工作根目录，绑定后 Agent 直接在该文件夹中工作：

- **文件夹即项目根**：选定的本地文件夹就是项目根目录，不再经过全局工作区的 `/projects/<repo>` 中间层。
- **Agent 目录布局**：系统自动在本地文件夹内维护 `.xihe-agent/{skills,reviews/<repo>/}` 目录结构。
- **双根沙箱**：本地文件夹为可写区域，全局工作区（`.secrets`、运行时配置等）保持只读，无需修改 Workspace 权限模型。
- **持久绑定**：`local_folder` 按项目名持久化到 MySQL（`project_local_folders` 表），同一项目的所有会话共享同一工作根目录。
- **虚拟路径映射**：绑定后 `/skills/`、`/reviews/` 等虚拟路径自动映射到 `.xihe-agent/` 下的对应子目录。
- **本地技能落点**：`.xihe-agent/skills/` 即为「本地」作用域技能的存放位置，与全局、项目集合两层共同构成技能的四层加载顺序。

未绑定本地文件夹的项目继续使用传统的全局工作区模式，「本地」作用域技能不可用（此时可在技能接口页把技能存到全局或项目集合作用域）。沙箱环境下也没有本地文件夹，「本地」作用域同样不可用。

### 自动化任务 (`config/automation_config.py`)

一个自动化任务由**定义**和**跨轮状态**两部分组成，分开存：

| | 落点 | 内容 |
|---|---|---|
| 定义 | `data/automations.json` | 名称、任务指令、cron 表达式、仓库、模型、验证检查项、最近一次触发信息与 `lastRunId` |
| 状态 | `data/automation_state/<任务 id>.json` | 已跑几轮、每轮结果、上一轮验证反馈、处理到哪、是否已停止 |

分开存有三个原因：多个任务可能同时跑，分开存不会互相覆盖；单个任务的历史变长不拖累其它任务；删除任务时直接删对应文件即可。

**删除任务会连同状态文件一起删**（`DELETE /api/v1/schedules/{id}`）。只删定义的话状态目录只增不减，界面上一个任务都没有、磁盘上却堆着一批点不进去的历史。想重新观察一个已经跑歪的任务而不重建配置时，用「清空历史」（`POST /api/v1/schedules/{id}/reset`）—— 它保留任务本身，把轮数、token 用量、连续未通过次数与停止标记一起归零。

`lastRunId` 只在**跑完一轮之后**写回定义。触发那一刻还不知道运行 id，所以它和 `lastTriggeredAt` 分开写：`lastTriggeredAt` 必须在启动这一轮**之前**落盘（`is_due` 靠它判「这一分钟跑过没有」），跑完再写一次就把这层判断改成了「跑完的时刻」，同一分钟可能被再触发一次。

#### 一轮循环做了什么

顺序固定，每一步都落盘，跑完能查到每一轮发生了什么：

1. **先看该不该停** —— 上一轮可能已经把循环跑停了，这一轮不该再启动；
2. **读状态** —— 拿到「上一轮做到哪」「上一轮验证反馈」；
3. **拼指令** —— 任务指令 + 状态摘要 + 上一轮验证反馈，一起发给模型；
4. **跑一轮** —— 走和普通会话一样的 Agent 流程，产生一条会话记录；
5. **独立验证** —— 在**循环自己**这一侧跑配置好的检查项（例如 `pytest -q`），不采信模型的自我评价；
6. **写状态** —— 记下这一轮的结果、验证结论与新的游标；
7. **再看该不该停** —— 判断下一轮还能不能继续。

#### 停止条件

停止条件必须是机器能判定的，否则循环停不下来。目前有五个来源：

- **连续多少轮没通过验证**（`maxConsecutiveFailures`）—— 这是最常用的一个；
- **最多跑多少轮**（`maxRuns`）；
- **最多消耗多少 token**（`maxTokens`）—— 用量从模型返回消息的 `usage_metadata` 里累加；拿不到用量时按 0 记，这一轮不会因此被判失败；
- **停在等人工确认** —— 这一轮撞上了需要审批的命令。无人值守时没人会去点那个确认，再跑一轮还是卡在同一条命令上，所以循环停下来等人。它排在上面三条上限之前报出来：只报「额度用完了」会把人引去调上限，调完还是卡在同一个地方。开了「全流程自动化」的任务一般不会停在这一条上（审批由表决处理），只有表决**得不出结论**时才会；
- **显式停止** —— 由验证连续失败升级而来，也可以人工置位。

上限写 `0` 表示不限制。五个来源的**恢复方式不一样**：「显式停止」和「停在等人工确认」能清掉标记继续跑（`POST /api/v1/schedules/{id}/resume`），其余三条要调高对应的上限（`PATCH /api/v1/schedules/{id}/budget`）。接口返回的 `stopSource` 会说明是哪一个，界面据此决定给「继续运行」按钮还是给一句提示。上限存在循环状态里而不是任务定义里 —— 它和「已经跑了多少轮 / 用掉多少 token」是一对，必须放在一起才不会算错。

验证的判定规则：配了检查项就**以检查项的结论为准**，没配检查项时退回看这一轮本身跑成没跑成；「没做完但也没报错」（`blocked`）不算失败。

**「没跑成验证」和「验证没通过」是两件事**，结论里分开记：`passed` 回答「检查项都确认通过了吗」，`executed` 回答「有检查项真的跑起来过吗」。一条检查都没执行时（例如在容器里没能确定仓库目录）我们**不知道**活儿干得怎么样，所以它不算未通过 —— 不这么分的话，`maxConsecutiveFailures` 会数着环境问题把任务停掉，而停的理由写着「未通过验证」，把人指去改代码。这种情况会写进任务的 `lastError`，在列表上就看得出来。

#### 验证门的三条硬规则

- **没配置检查项 = 不通过**。否则「什么都没验」会被当成「验过了」。
- **要审批的命令不执行**。检查项里的命令先过一遍既有的命令审批策略，判定为「拒绝」或「需要审批」的一律记为跳过，执行器一次都不调用。无人值守的循环不能自己批准自己 —— 「全流程自动化」（下一节）只管 Agent 执行命令时的那处审批，**不覆盖这里**。
- **说不清仓库在哪就不执行验证**。检查项必须在**目标仓库**里跑：调用方没指定目录时用「跑一轮」报上来的那个，两者都没有就整体记为「没有执行验证」并说明原因。退回后端进程的当前目录会在后端自己的仓库里跑检查项，得到一份与目标仓库无关的结论，比「没验证」更糟。

#### 仓库在沙箱容器里时，验证怎么跑

默认部署是 `USE_SANDBOX=true`，仓库克隆在容器内，本机 `subprocess` 到不了它。所以「跑一轮」跑完会**顺手解析一个容器执行器**（`services/automation_runner.py::resolve_container_exec`），循环拿到它之后，检查项在容器内、容器里的仓库目录下执行。

- **容器内的仓库目录是问出来的，不是猜的**。沙箱镜像不由我们控制，容器内工作目录只有跑起来才知道，所以逐个候选位置发一条只读的 `git -C <候选目录> rev-parse --show-toplevel`，问到哪个算哪个。
- **探不到就如实记「没有执行验证」**，绝不退回本机目录糊一份与目标仓库无关的结论。
- **退出码里有三个是协议值不是命令的退出码**（`services/automation_verification.py::make_container_runner`）：`124` 是沙箱侧超时、`126` 是沙箱自己拒了、`1` 且输出带固定前缀是沙箱执行出错。三者都必须转成「没能执行」，否则「沙箱坏了」会被记成「验证没通过」，下一轮就去修一个根本没跑过的东西。拿不到退出码时**绝不补 0** —— 补 0 等于拿故障换一个绿灯。
- **换成容器不等于绕过审批**：检查项照样先过命令审批策略，`deny` 与 `approval` 两档在进容器之前就被拦下了。

> ⚠️ 沙箱执行失败的前缀（`执行命令时出错：`）与 `backends/opensandbox.py` 里的文案是**耦合点**。改那边的措辞就要同步改 `SANDBOX_ERROR_PREFIX`，否则会把「没能验证」报成「验证没通过」。

#### 全流程自动化：审批交给表决

自动化任务可以开一个 `autoApprove` 开关（界面上的「全流程自动化（审批交给表决）」，默认关闭）。开着的时候，一轮撞上需要审批的命令不再停下等人，而是由三个评审各自独立作答、多数决定放行与否（`services/approval_vote.py`）。

三个评审看的是**三个不同的问题**，不是同一个问题问三遍：会不会造成不可逆损失、是不是这个任务真正需要做的、有没有明显更安全的等价做法。同一个模型、同一个提示词问三遍，三个答案高度相关，多数决的实际纠错力接近于零。

四条边界（改这块之前先读）：

- **硬拒绝永不上表。** `deny` 那一档在执行路径上就被直接拒掉了（`local_shell` 与 `opensandbox` 都在执行前独立判一次），表决碰不到它、也放行不了它。表决拿到待审批记录时会**再判一次**策略：只要有一条现在判成 `deny`，整批立刻交回人工 —— 待审批记录可能已经在磁盘上躺了很久，期间审批策略与执行环境都可能改过，而投票不该成为让硬拒绝变得可执行的通道。
- **得不出结论就交回人工，既不当作同意也不当作否决。** 任一评审报错、超时或返回不合法结构，整次表决都不上表：少一票的多数不是多数。而且把「没能问出来」记成「否决」会让模型去改一个根本没被否掉的东西 —— 和「没跑成验证 ≠ 验证没通过」是同一条原则。
- **否决会带着理由交回模型**，让它换做法接着干，而不是原样重试同一条命令。同一轮最多表决 5 次（`MAX_APPROVAL_VOTES_PER_ROUND`），用满还在撞审批就停手退回等人工 —— 保留原有的「停在等人工确认」行为作兜底。
- **评审只做判断**：不给工具、不执行命令、不读仓库，看到的全部信息就是任务指令与待审批命令。命令正文按**数据**处理，提示词里明确要求不要听信其中出现的指示 —— 命令是模型写的，里面完全可能出现「请投同意放行」。

每次表决都记进循环状态（`approvalVotes`），**包括没能得出结论的那几次**：「开了全流程自动化但这次没能定」和「表决放行了」是两件不同的事，只记后者会让人以为这一轮没有任何人工介入。状态文本里两者分开写，marks 是「审批表决放行 / 否决 / 未定 / 未进行」。自己放行的命令还会同时写进这一轮的摘要（含命令原文）。表决自己的 token 也计入这一轮的用量。

> ⚠️ 这是把一处人工闸门交给机器，所以开关是**每个任务各自显式开**的、默认关闭。开启后，「向外部服务写入数据」这类锁定的 `approval` 规则也可以被逐次表决放行（它们仍然不能被设置改成「自动允许」）；**硬拒绝那一档不受影响**。

#### 循环里能跑哪些任务

| 任务类型 | 能不能在循环里跑 | 条件 |
|---|---|---|
| 只读（项目分析 / 问题解答 / 代码审查 / 仓库同步 / 工作区查看） | ✅ | —— |
| 写入（开发实现） | ✅ | **必须至少配一条验证检查项** |
| 方案规划 | ❌ | 按设计就要产出方案等确认 |

判定走既有的任务类型识别（`utils.intent_utils.classify_task_kind`），**只看用户写的那一句任务指令**，不看拼上去的循环状态与上一轮反馈。拼装出来的文字里会带「提交」「push」这类词（来自状态摘要或验证反馈），拿它去判会改变任务类型的结论。

写入任务的两条额外规矩：

1. **必须配验证检查项**。循环改完代码时没有人在旁边看，检查项是唯一的独立判断依据；一条都没配就当场拒掉，而不是「先改了再说」。判定只看**配没配**，不看命令内容 —— 命令能不能跑由验证门自己判（要审批的照样跳过）。
2. **走一条不经过方案确认的路径**。交互路径会把首轮的开发需求转成 planning、停下来等用户确认；无人值守的循环里没人会去点那个确认，于是每轮都停在同一步、被记成「没做完但没报错」，`maxConsecutiveFailures` 永远不会触发。循环调用时传 `skip_plan_confirmation=True`（`agent/runtime/task_router.py`），直接进入实现。**这个参数只给循环用**，交互路径必须保持默认值 —— 用户看着的时候，方案确认是一道有价值的人工闸门。

「方案规划」仍然拒掉：要跑这类需求，把任务指令写成明确的实现内容，它会被识别为开发实现并直接执行。

#### 怎么触发

两条路都通：

- **手动触发** —— 在自动化任务页面点「立即跑一轮」，或调 `POST /api/v1/schedules/{id}/run`。接口是受理式的，返回 `202` 表示这一轮已经派到后台，跑完会作为一条会话记录出现在会话列表里；这个任务已经有一轮在跑时返回 `409`（见下面「同一个任务不会同时跑两轮」）。
- **按 cron 自动触发** —— 服务启动时拉起一个后台扫描线程（`services/automation_scheduler.py`），默认每 30 秒扫一遍，到点的任务各起一个线程去跑。扫描顺序上**先把触发时间写进定义，再启动这一轮**：`is_due` 靠 `lastTriggeredAt` 判断「这一分钟跑过没有」，写晚了同一分钟会被重复触发。同一个任务已经有一轮在跑时，这一分钟跳过它。

**同一个任务不会同时跑两轮**：调度线程和手动触发是两条路，两边共用 `services/automation_guard.py` 里那份运行登记（Redis key 前缀 `xihe:automation:running`，TTL 默认 1 小时）。先抢到的一方跑，后到的一方 —— 调度侧这一分钟跳过、接口侧当场返回 `409`。没有这份登记时，一次点击撞上一次自动触发就是两个线程同跑一轮：各自读同一份 `budget.runsUsed`，跑完再各自把状态写回去，轮次计数会丢、历史会互相盖。跑完**无论跑成什么样**都会放掉登记（一轮炸了、受理那一步就失败、线程都没起来，都会放），进程崩掉则等 TTL 超时自愈；放掉时核对凭据，不会撬开已经落到别人手里的锁。

**扫描这一步在多进程之间用一份 Redis 租约收敛**（`ScannerLease`，key `xihe:automation:scheduler:lease`，TTL 默认 90 秒）。谁拿到租约谁扫，没拿到的这一轮什么都不做 —— 没有它，多进程部署时每个进程都会扫一遍，同一个到点任务被重复触发。每次 tick 会续期，停调度时主动放掉；释放只删自己那份（租约可能已经超时落到别人手里，那时删掉等于撬开别人的锁）。

租约收敛的是「谁负责触发」，运行登记收敛的是「谁在跑」—— 前者一层就够，后者必须是跨入口共享的一份，因为手动触发不经过调度线程。

> ⚠️ **Redis 不可用时都不拦住活儿**：扫描租约记一条警告后照常扫，运行登记降级为「只在本进程内互斥」。理由是另一头更糟 —— 缓存故障会让所有定时任务静默停掉，而当前部署本来就是单进程（真出问题的是多进程部署，那是配置问题，不是运行期故障）。换部署形态时这条取舍要一起重新想。
>
> ⚠️ **测试造调度器时必须注入假租约与假登记**（`tests/test_automation_scheduler.py::StubLease`、`make_scheduler`）：两个默认实现都接真 Redis，同一个进程里前一个用例留下的租约或登记还在有效期内，后面每个用例都会变成空转 / 「已经有一轮在跑」，表现成一片「到点却没启动」。

自动触发跑出问题时会写进定义的 `lastError`，列表上直接看得见；跑成功的那次触发会把它清掉。算「问题」的有三种：这一轮失败了、这一轮根本没执行、以及这一轮**没做完**（`blocked`）—— 最后这种最常见的是「停在等人工审批」，定时任务在列表上看着一切正常、实际每轮都在等一个没人会点的确认，不写出来基本发现不了。

循环已经把自己停了的任务（连续没通过、到达轮数上限等）**不再触发** —— 触发它只会空转，却会把 `lastTriggeredAt` 写新，界面上显示成「刚触发过」。

### 技能系统

技能是可复用的 Agent 能力模块，每个技能是一个目录，至少包含一个 `SKILL.md`（YAML frontmatter + 正文说明），可附带脚本、参考资料等附属文件。

#### 四层作用域

技能按「越贴近具体工作的层优先级越高」组织，**同名时后写的层覆盖先写的层**：

| 作用域 | 物理落点 | 虚拟路径（本地） | 沙箱内路径 | 可写性 |
|--------|----------|------------------|-----------|--------|
| `builtin` | `agent/skills/` | 不挂载 | 不挂载 | 仅开发者，只作模板源 |
| `global` | `<全局技能目录>/`（`Settings.skills_dir`，默认 `E:/ai_workspace/skills`） | `/skills-global/` | `/workspace/skills/` | 网页 + Agent |
| `project_set` | `<全局技能目录>/.project-set/<project-set-name>/` | `/skills-set/` | `/workspace/skills/.project-set/<project-set-name>/` | 网页 + Agent |
| `local` | `<项目本地文件夹>/.xihe-agent/skills/` | `/skills/` | 不同步 | 网页 + Agent |

其中 `<project-set-name>` 是当前项目的项目集合名（即对话里选定的项目名）。以项目名 `Xihe`、全局技能目录 `E:/ai_workspace/skills` 为例：

```
E:/ai_workspace/skills/                          ← global
E:/ai_workspace/skills/.project-set/Xihe/        ← project_set（项目名 Xihe）
E:/my_project/Xihe/.xihe-agent/skills/           ← local（项目文件夹已绑定到 E:/my_project/Xihe）
```

> **项目集合（`project_set`）与本地（`local`）是两个不同的层。** 项目集合落在全局技能目录的 `.project-set` 子目录下，因此**不要求项目绑定本地文件夹**；本地层才要求绑定。

**沙箱环境（`USE_SANDBOX=true`）下只少本地层。** 容器里没有项目本地文件夹，`local` 既不加载也不同步；全局层与项目集合层都会在构建 backend 时同步进容器。落点刻意与主机目录结构保持一致，`SkillsMiddleware` 拿到的就是容器内真实路径（沙箱 backend 不做虚拟路径映射）：

```
沙箱容器内
/workspace/skills/                               ← global
/workspace/skills/.project-set/Xihe/             ← project_set（会话所属项目集合）
```

会话不属于任何项目集合时只同步全局层。同步口径与挂载口径由同一组函数产出（`services.skill_registry.sandbox_skill_sources` / `load_sandbox_skill_documents`），不会出现「传了没挂」或「挂了没传」。

设计要点：

- **项目集合目录名保留用户输入的原始大小写**（`Xihe` 就是 `Xihe`，不会被折成小写）。注意这与**技能名**不同 —— 技能名受 Agent Skills 规范约束，必须是小写字母/数字/连字符。
- **`local` 是按项目名反查绑定文件夹定位的**，接口不接受直接传路径；绑定关系通过 `POST /api/v1/workspace/projects/bind-folder` 建立，并持久化在 MySQL 的 `project_local_folders` 表。
- 本地环境技能加载顺序为 `[/skills-global/, /skills-set/, /skills/]`，沙箱环境为 `[/workspace/skills/, /workspace/skills/.project-set/<项目集合名>/]`，由 DeepAgents `SkillsMiddleware` 的 `sources` 语义保证覆盖关系，无需自行实现合并逻辑。
- **项目名不再按 backend 类型跳过反查**：项目集合层落在全局技能目录下，不依赖磁盘绑定，本地与沙箱两种环境都会挂载它。

#### 技能变更后的沙箱同步

技能在主机上是唯一副本，沙箱会话读的是容器里的拷贝，因此网页或 Agent 改动技能后会立即推一次（`services/sandbox_skill_sync.py`）：

- **同步范围**：`global` 变更推给所有沙箱会话；`project_set` 变更只推给该项目集合下的沙箱会话；`local` 与沙箱无关，直接跳过。
- **只在对话跑完时动容器**：会话处于 `running` / `awaiting_approval` 时正在使用容器，中途换掉技能目录会让同一轮回答前后依据不一致，此时只登记「待同步」标记，在下一次构建智能体前补上（`server._apply_pending_skill_refresh`）。
- **幂等同步**：先清空技能根目录再写入（`OpenSandboxBackend.sync_skills`），因此主机上删掉的技能也会从容器里消失。清空与建目录走沙箱文件系统接口而非 shell，不受命令审批策略影响。
- **缓存淘汰不需要额外处理**：不在 backend 缓存里的会话，其沙箱会在下一次构建时重新同步。

#### 作用域选项的展示规则

界面上三处下拉的可见选项并不相同，这是有意设计。分界条件是「该会话能不能加载到项目 / 本地两层」：

| 位置 | 未指定项目 | 已指定项目（本地） | 已指定项目（沙箱） |
|------|-----------|-------------------|-------------------|
| 对话内技能面板 · 顶部筛选 | 只有「全局」 | 全局 / 项目 / 本地 | 全局 / 项目 |
| 对话内技能面板 · 上传对话框工作域 | 只有「全局」 | 全局 / 项目（项目已绑文件夹时含本地） | 全局 / 项目 |
| 技能接口页 · 新建技能作用域 | 全局 / 项目 / 本地 | 全局 / 项目 / 本地 | — |

- 两个下拉判定的是不同问题：**顶部筛选**是「这个会话所在环境有哪些技能层」，由 `SkillPanel` 按项目名与 `backendType` 判定；**上传工作域**是「这个会话能写到哪」，以后端 `GET /api/v1/skills` 的 `writableScopes` 为准——面板会把当前会话的 `backendType` 一并传给接口，后端据此把沙箱下的「本地」剔除。
- 技能接口页可在页内自由切换项目，且作用域是独立的意图表达，因此始终列全；选到当前不可用的层时，由下方的归属项目 / 本地目录字段引导补齐条件。

#### 两个创建入口

- **网页入口**：技能接口页与对话内技能面板，支持粘贴创建与文件上传（`.md` 或 `.zip` 技能包）。
- **Agent 入口**：`create_skill` 工具，让智能体把可复用流程沉淀为技能。技能目录本身是只读资产，`create_skill` 是唯一合法的写入通道 —— 模型不能通过 shell 或文件工具直接改技能文件。

技能来源记录在技能目录下的 `.origin` 标记文件中（`builtin` / `user` / `agent`）；界面只对 `agent` 来源显示「智能体创建」徽章。

#### 校验规则

校验口径与 `SkillsMiddleware` 完全一致，否则会出现「接口说成功、加载器静默丢弃」的幽灵技能：

- 必须有以 `---` 起始的 YAML frontmatter，且为映射；
- `name` 非空、≤64 字符、匹配 `^[a-z0-9]+(?:-[a-z0-9]+)*$`；
- `description` 非空、≤1024 字符；
- 单文件 ≤512KB，整包 ≤8MB / 200 个文件；
- ZIP 上传拒绝路径穿越（`..`、绝对路径、盘符）。

内置技能（`agent/skills/`，仅作模板源）：

- **code-review**：代码审查，检测代码质量问题并记录结构化 finding
- **repo-bootstrap-analysis**：仓库引导分析，帮助理解项目结构、启动与测试方式
- **ai-coding-implementation**：AI 编码实现，辅助代码生成并控制实施节奏

> ⚠️ 技能改动**下一次构建智能体时**才会生效（`SkillsMiddleware` 在建图时读取技能目录），当前会话内无法立即加载。沙箱会话下一次构建时会重新同步技能到容器，因此上传完技能后发下一条消息即可看到。

#### 技能检索开关

技能目录（每个技能的 `name` + `description` + 路径）会被拼进**每一次模型调用**的 system prompt，且主 Agent 与两个子 Agent 各注入一份。技能变多以后这笔成本线性上涨，因此个人设置里提供了可选的检索开关。

| 模式 | 行为 |
|------|------|
| `off`（默认） | 始终注入全部技能，与未引入检索时逐字一致 |
| `auto` | 技能数**超过** `auto_threshold`（默认 60）时才检索 |
| `on` | 每次都按当前任务检索 |

开启后，系统用最后一条用户消息作为查询，按 BM25-lite 词法打分（中文按二元组切分，与 `pg_trgm` 思路一致，不需要分词器与向量库）挑出 `top_k`（默认 20）个技能注入。

**参与打分的文本，和最终写进提示词的文本，不是同一份**：

| | 内容 | 去向 |
|---|---|---|
| 写进提示词 | `name` + `description` + `path` | 每个技能两行 |
| 参与打分 | 上面这些 + `metadata` 全部键值 | 只用于打分，**不写进提示词** |
| 技能正文 | `SKILL.md` 的 body | 既不参与打分也不写进提示词，模型命中后用 `read_file` 按需读取 |

打分时看得比写进去的多，是为了让命中更准：`metadata` 里可以放 `keywords` / `when_to_use` 这类额外提示，`path` 里的作用域信息（如 `.project-set/Xihe`）会让「提到项目名」自然抬高该项目专属技能。但写进提示词的永远只有那两行摘要 —— 技能正文自始至终不进入提示词，这是控制成本的关键，也是 DeepAgents 渐进披露（progressive disclosure）的原有约定。

因此写技能时：**「做什么 + 什么时候用 + 关键词」都要写进 `description`**（技能规范要求 description 同时描述用途与触发条件，上限 1024 字符），更细的提示可以放进 `metadata`。

三条安全性质：

- **常驻技能不受额度约束**：`prompt.py` 按名字引用了 `repo-bootstrap-analysis`、`ai-coding-implementation`、`code-review`，它们始终出现在提示词里，并优先占用 `top_k` 名额；
- **fail-open（宁可多列几个）**：查询为空、无任何命中、`top_k` 非法或检索过程抛异常时，一律回退为列出全部技能；
- **不切断读取通道**：检索只筛选写进提示词的技能列表，模型仍可通过 `list_skills` 与 `read_file` 触达未列出的技能。

改动在**下一条消息**生效，无需重启：中间件挂在 `wrap_model_call → modify_request`（每次模型调用都执行一次），而不是把结果写进会被 checkpoint 持久化的 `skills_metadata`。

实现上有一个关键点：DeepAgents 在 `deepagents/graph.py` 内部硬编码构造 `SkillsMiddleware`，没有对外注入点，但它合并调用方 `middleware=` 的规则是**按 `.name` 原位替换**（`.name` 默认取类名）。因此 `agent/middleware/skill_retrieval.py` 里的类**刻意与内置实现同名**，靠这一机制顶替它，既不用 monkeypatch 第三方包，也不用重写技能加载逻辑。若把类名改掉，合并会退化成「新增一个中间件」，`create_agent` 会因同名冲突直接报错 —— 失败是响亮的，不会静默失效。

#### 实测收益

`eval/skill_retrieval/` 是这套开关的端到端评估：造 150 条真实落盘的技能，走生产同一条链路（`LocalShellBackend` → DeepAgents 加载器 → 中间件渲染），量模型真正收到的提示词。

```bash
.venv/Scripts/python.exe -m eval.skill_retrieval --label baseline
```

150 条技能、25 条查询、`top_k=20` 下的实测结果：

| 指标 | `off`（默认） | `on` | 变化 |
|---|---|---|---|
| 技能段字符数 | 20334 | 5041 | **-75.2%** |
| 平均列出技能数 | 150 | 25.2 | -124.8 |
| 整条提示词 token（含基础提示词） | 15050 | 9074 | -39.7% |
| 门槛样本命中率 | 100%（基线） | 100%（20/20） | — |

`top_k` 收紧到 5 时命中率仍是 100% —— 注意此时 3 个名额已被常驻技能占掉，留给排序的只有 2 个，也就是说相关技能稳定排在前列。

**没测的**：模型是否真的选对技能并完成任务（需要真实 LLM 运行）。评估只量提示词的形状。另有一个已知边界：领域外查询（如「帮我写一首关于秋天的诗」）在中文二元组分词下切不出与技能目录重合的词元，按 fail-open 设计列出全部技能，这类查询省不下长度。

评估自带负向对照（`--negative-control`）：把打分换成「不看查询、按目录取前 N 条」，命中率应掉到 20% —— 如果还能通过，说明评估本身失效。一个恒过的评估比没有评估更糟。

### 命令审批与安全边界

XiheAgent 对 `execute` 工具采用统一的三段式策略：`ALLOW` 直接执行，`REQUIRE_APPROVAL` 通过 LangGraph 原生 interrupt 暂停，`DENY` 直接拒绝。暂停点由现有 Redis Checkpoint 保存，用户点击“允许一次”或“拒绝”后，服务端用 `Command(resume={"decisions": [...]})` 恢复同一个会话；同步的 `execute()` 不等待人工输入，也不需要额外的 Redis 队列。

本地工作区默认更保守：测试可自动运行，构建、依赖安装、项目脚本、文件修改、Git 写操作和网络访问默认询问。OpenSandbox 只在隔离容器内自动放行可恢复的构建、脚本、依赖和只读网络操作；删除、远端写入、凭据、提权和沙箱逃逸仍受保护。

`git` 允许把全局选项写在子命令前面（`git -C <路径> status`、`git --no-pager diff`），分类时会**先剥掉这些选项再匹配**，因此 `git -C <路径> status` 与 `git status` 同判、`git -C <路径> push --force` 与 `git push --force` 同判。这一步是必需的：不剥掉的话，只读命令会掉进「未识别 → 每次询问」，破坏性命令则会绕过上面那条「强制推送、破坏性重置」的高危规则。Agent 在仓库外的目录里工作时天然会用 `-C <路径>` 这种写法，所以这条不是边角情况。

个人设置可以按语义类别选择“自动允许”或“每次询问”。服务端会过滤未知类别，且设置无法覆盖高危规则，包括工作区外写入、受保护密钥、强制推送、破坏性重置、提权、系统/磁盘配置、凭据注入和沙箱逃逸。

人工审批面板以内联方式显示在会话输入框上方，展示命令详情以及”解释””拒绝””允许一次”，不会使用遮挡会话内容的居中弹窗。

自动化任务可以开启「全流程自动化」，把这道闸门交给三个评审的表决（见上一节）。它走的是**同一条恢复路径**（`resume_agent_task`，只是把决定从人换成票），因此挂在同一个 checkpoint 上；唯一的差别是拒绝时交回给模型的话不一样 —— 界面点拒绝说的是「用户拒绝了这次命令执行」，表决必须说清是机器投的票以及否决理由，否则模型只会原样重试同一条命令，然后撞上同一次否决。

点击”解释”按钮时，系统会调用专门的审批解释模型（`COMMAND_APPROVAL_EXPLANATION_SYSTEM_PROMPT`），用中文向用户说明：
- 命令会做什么
- 从命令本身能判断的主要影响（修改工作区、访问网络、启动进程、安装依赖、提交代码、删除数据等）
- 系统为什么要求人工确认

解释器不会执行命令、改写命令或替用户决定，只依据输入的命令和审批原因作答。

## API 接口

### 基础接口

```
GET  /health                    # 健康检查
```

### Dashboard 接口

> 目前没有登录入口，也没有鉴权：`/me` 返回写死的单用户，前端仅用它显示登录名并做数据守卫。部署到公网前需要自己补上认证层。

```
GET  /api/v1/me          # 当前用户信息（写死的单用户，恒返回 200，无登录入口）
GET  /api/v1/options     # 页面选项（模型列表等）
GET  /api/v1/profile     # 工作台默认配置
GET  /api/v1/repos       # 可用仓库列表（占位实现，恒返回空列表）
GET  /api/v1/threads     # 会话列表
GET  /api/v1/threads/{id} # 会话详情
POST /api/v1/threads/stream-message         # 创建新会话
POST /api/v1/threads/{id}/stream-message    # 追加对话轮次
POST /api/v1/threads/{id}/cancel            # 停止这一轮正在跑的任务（没在跑返回 409）
DELETE /api/v1/threads/{id}                 # 删除会话
PATCH /api/v1/threads/{id}/title            # 修改会话标题
POST /api/v1/threads/{id}/edit-preview      # 编辑消息前预览影响
PUT  /api/v1/threads/{id}/view-snapshot     # 保存会话视图快照
GET  /api/v1/command-approval-settings      # 获取命令审批设置
PUT  /api/v1/command-approval-settings      # 保存普通命令类别设置
GET  /api/v1/threads/{id}/approval          # 获取待审批操作
POST /api/v1/threads/{id}/approval          # 审批并恢复运行（SSE）
POST /api/v1/threads/{id}/approval/explanation # 生成审批命令解释
```

### 自定义模型管理

```
GET    /api/v1/custom-models              # 获取自定义模型列表
POST   /api/v1/custom-models              # 创建自定义模型
PUT    /api/v1/custom-models/{id}         # 更新自定义模型
DELETE /api/v1/custom-models/{id}         # 删除自定义模型
GET    /api/v1/builtin-model              # 获取内置模型配置
PUT    /api/v1/builtin-model              # 更新内置模型配置
```

### 自动化任务管理

```
GET    /api/v1/schedules                  # 列出自动化任务
POST   /api/v1/schedules                  # 创建自动化任务
PATCH  /api/v1/schedules/{id}             # 更新自动化任务（支持局部字段）
DELETE /api/v1/schedules/{id}             # 删除自动化任务（连同它的循环状态文件）
GET    /api/v1/schedules/{id}/state       # 读这个任务的循环状态（只读，不会建出状态文件）
POST   /api/v1/schedules/{id}/run         # 手动跑一轮（202 受理；不能跑时返回 404/409/400 与原因）
POST   /api/v1/schedules/{id}/reset       # 清空循环历史（保留任务；返回与 /state 同构的状态）
POST   /api/v1/schedules/{id}/resume      # 恢复已停止的自动化任务（清除显式停止/等人工确认标记）
PATCH  /api/v1/schedules/{id}/budget      # 调整自动化任务的额度上限（轮数/token/连续失败次数）
```

`POST /schedules/{id}/run` 的失败状态码：

| 状态码 | 含义 |
|--------|------|
| 404 | 任务不存在 |
| 409 | 这个循环已经停了（响应里带上停止原因），或这个任务已经有一轮在跑 |
| 400 | 任务超出循环可跑范围（方案规划、或写入任务没配验证检查项）、没有配置代码仓库，或验证检查项配置有误 |

「能不能跑」的判断全部在这个请求里同步做完，不会先回 `202` 再在后台失败。抢运行登记也在这批判断里，**在写 `lastTriggeredAt` 之前** —— 抢不到却已经把触发时间写新了，界面上就会显示成「刚触发过」，而实际一次都没跑。

### 技能管理

```
GET    /api/v1/skills                     # 列出可见技能 + 三层作用域配置 + 当前可写作用域
GET    /api/v1/skills/{skill_id}          # 读取技能详情（含 SKILL.md 全文）
POST   /api/v1/skills                     # 创建技能（JSON：name/description/body/scope/project_name）
PUT    /api/v1/skills/{skill_id}          # 更新技能
DELETE /api/v1/skills/{skill_id}          # 删除技能及其附属文件
POST   /api/v1/skills/upload              # 上传技能（.md 或 .zip，支持附属文件与覆盖）
```

**作用域参数**（`scope`，创建/更新/删除时必填）：

| 值 | 说明 | 是否要求 `project_name` |
|----|------|------------------------|
| `global` | 全局技能 | 否 |
| `project_set` | 项目集合技能 | 是 |
| `local` | 项目文件夹内的技能 | 是，且该项目须已绑定本地文件夹 |

> 旧参数值 `project` 会自动折叠为 `local`，保持向后兼容。

`GET /api/v1/skills` 返回的 `scopes` 始终包含三项（未指定项目时后两项标 `available: false`），`writableScopes` 则按当前项目实际可写性动态给出。

### 技能检索设置

```
GET    /api/v1/skill-retrieval-settings   # 读取检索开关（含模式选项与取值区间）
PUT    /api/v1/skill-retrieval-settings   # 更新检索开关（mode/top_k/auto_threshold，均可缺省）
```

配置持久化在 `<data_dir>/skill_retrieval.json`，未配置或文件损坏时回退为 `mode=off`。越界或非法的入参返回 400，不做静默夹取。语义见「技能系统 → 技能检索开关」。

### 工作目录管理

```
GET    /api/v1/workspace                  # 获取工作目录配置
PUT    /api/v1/workspace                  # 更新工作目录配置（workspace_root/projects_dir/skills_dir）
GET    /api/v1/workspace/projects         # 获取项目列表（含 local_folder 绑定信息）
GET    /api/v1/workspace/pick-folder      # 打开系统文件夹选择对话框
POST   /api/v1/workspace/projects/bind-folder    # 绑定项目到本地文件夹
PATCH  /api/v1/workspace/projects/rename  # 重命名项目（级联更新所有关联会话）
```

> 项目解绑与改显示名两个入口已移除；`project_local_folders.display_name` 这一列仍然保留，由重命名链路写入。

## 环境变量

以仓库根的 `.env.example` 为准（下表是同一份清单）。前端构建期变量不在其中，见「启动前端服务」。

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MAIN_MODEL` | 默认使用的语言模型标识 | `deepseek-v4-pro` |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥（使用自定义模型时可选） | *可选* |
| `DEEPSEEK_BASE_URL` | DeepSeek API 基础地址 | *必填* |
| `INTENT_MODEL` | 意图分类专用模型（不设则使用 MAIN_MODEL） | *同 MAIN_MODEL* |
| `ZHIPU_API_KEY` | `web_search` 工具使用；不配则该工具调用会报错 | *可选* |
| `GITEE_TOKEN` | Gitee 访问令牌（可回退到 `SCM_GITEE_TOKEN`） | *按需* |
| `GITHUB_TOKEN` | GitHub 访问令牌（可回退到 `SCM_GITHUB_TOKEN`） | *按需* |
| `CODEUP_TOKEN` | Codeup 访问令牌（可回退到 `SCM_CODEUP_TOKEN`） | *按需* |
| `CODEUP_ORG_ID` | Codeup 组织 ID（用 Codeup 时必填，缺失会直接报错） | *按需* |
| `CODEUP_API_DOMAIN` | Codeup API 域名 | `https://devops.aliyuncs.com` |
| `GITEE_API_BASE_URL` | Gitee API 地址（自建 / 企业版时改这里） | `https://gitee.com/api/v5` |
| `GITHUB_API_BASE_URL` | GitHub API 地址（GitHub Enterprise 时改这里） | `https://api.github.com` |
| `AGENT_MAX_TOOL_CALLS` | 单轮 Agent 的工具调用上限 | 按任务类型取值 |
| `AGENT_MAX_SECONDS` | 单轮 Agent 的时长上限（秒） | 按任务类型取值 |
| `XIHE_AGENT_DATA_DIR` | 数据文件目录 | `{project_root}/data` |
| `XIHE_AGENT_LOG_DIR` | 日志文件目录 | `{project_root}/logs` |
| `XIHE_AGENT_LOG_LEVEL` | 日志级别 | `INFO` |
| `XIHE_AGENT_LOG_WHEN` | 日志轮转触发时机 | `midnight` |
| `XIHE_AGENT_LOG_INTERVAL` | 日志轮转间隔 | `1` |
| `XIHE_AGENT_LOG_RETENTION_DAYS` | 日志保留天数 | `14` |
| `XIHE_AUTOMATION_SCHEDULER` | 是否启用自动化任务的常驻调度 | `true` |
| `XIHE_AUTOMATION_TICK_SECONDS` | 常驻调度多久扫一次（秒，最小 1） | `30` |
| `AI_WORKSPACE_ROOT` | 代码工作区根目录 | `E:/ai_workspace` |
| `REDIS_HOST` | Redis 主机地址（Checkpoint） | `localhost` |
| `REDIS_PORT` | Redis 端口 | `6379` |
| `REDIS_PASSWORD` | Redis 密码（实例开了认证就必须填，否则建不出 checkpoint，任何一轮都跑不完） | *无* |
| `REDIS_CHECKPOINT_DB` | Checkpoint 使用的 Redis DB | `0` |
| `USE_REDIS_STREAM` | 启用 Redis Stream SSE 多进程模式（默认使用 asyncio.Queue） | `false` |
| `REDIS_STREAM_DB` | Redis Stream SSE 使用的 Redis DB | `1` |
| `MYSQL_HOST` | MySQL 主机地址（业务数据） | `localhost` |
| `MYSQL_PORT` | MySQL 端口 | `3306` |
| `MYSQL_USER` | MySQL 用户名 | `root` |
| `MYSQL_PASSWORD` | MySQL 密码 | *必填* |
| `MYSQL_DATABASE` | MySQL 数据库名（业务数据） | `xihe-agent` |
| `PG_HOST` | PostgreSQL 主机地址（长期记忆） | `localhost` |
| `PG_PORT` | PostgreSQL 端口 | `5432` |
| `PG_USER` | PostgreSQL 用户名 | `postgres` |
| `PG_PASSWORD` | PostgreSQL 密码 | *必填* |
| `PG_DATABASE` | PostgreSQL 数据库名（LangGraph Store） | `xihe-agent` |
| `USE_SANDBOX` | 是否启用 OpenSandbox 沙盒模式 | `false` |
| `SANDBOX_HOST` | OpenSandbox Server 完整地址（含协议和端口，如 `http://127.0.0.1:8080`） | *无* |
| `SANDBOX_PORT` | OpenSandbox Server 端口（独立于 SANDBOX_HOST） | `8080` |
| `SANDBOX_API_KEY` | OpenSandbox Server API Key（与 server 端 `api_key` 配置一致） | *无* |
| `SANDBOX_IDLE_TIMEOUT_DAYS` | 沙箱空闲超过此天数自动清理 | `3` |
| `SANDBOX_MAX_PER_THREAD` | 每个线程最多保留的沙箱数量 | `3` |

`AGENT_MAX_TOOL_CALLS` / `AGENT_MAX_SECONDS` 只是兜底值，可以按任务类型分别覆盖：把 `AGENT_` 与 `MAX_` 之间换成任务类型的大写名，例如 `AGENT_CODING_MAX_TOOL_CALLS`、`AGENT_QA_MAX_SECONDS`（任务类型见 `config/constants.py` 的 `VALID_TASK_KINDS`）。

## 开发命令

### 后端

```bash
# 代码检查（ruff 配置见 pyproject.toml，line-length=100）
ruff check .
ruff check --fix .

# 代码格式化
ruff format .

# 类型检查
mypy .

# 分层依赖契约（utils 禁止反向依赖上层业务包）
lint-imports
```

### 前端

```bash
cd frontend

npm install            # 首次需要安装依赖
npm run dev            # 启动开发服务器（127.0.0.1:3000）
npm run build          # 生产构建
npm run preview        # 预览构建产物（127.0.0.1:3000）
npm run typecheck      # 类型检查（tsc --noEmit）
npm test               # 运行全部单元测试（vitest run；watch 模式直接跑 npx vitest）
```

## 日志

日志文件位于 `logs/` 目录下：

- `backend.log` — 后端服务日志
- `agent-runs.log` — Agent 运行日志
- `backend.log.YYYY-MM-DD` — 历史日志（按天轮转）
