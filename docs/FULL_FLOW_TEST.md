# XiheAgent 项目全流程测试

这份是**给人跑的**端到端清单：每一步写清「怎么操作、看哪里、期望看到什么、不对时先查哪条证据」。

它覆盖**除自动化任务循环之外**的全部纵向流程。自动化任务（一轮循环、验证门、调度自触发、
表决放行、并发登记）已经有专门清单 `docs/AUTOMATION_FULL_LOOP_TEST.md`，本文件**不重复**，
只在 §7 列交叉引用点。

## 为什么还需要这份

`pytest` 现有 **458 条**用例（`pytest --collect-only -q` 实测），前端另有 Vitest 用例。
下面这些是它们**盖不到**的：

| 只有真跑才能验的 | 对应风险 |
|---|---|
| 三套真实存储（Redis 带密码 / MySQL / PostgreSQL）组合下的读写 | 单测全部用假对象，连接串写错要到运行时才炸 |
| 真实模型返回的流式事件形状 | 事件分片、工具调用记录、token 统计都是打桩过的 |
| 前端交互（点击、下拉、动画、禁用态、确认对话框） | 前端用例只覆盖 6 个组件，页面级交互没有覆盖 |
| 前端调用的接口是不是真的存在 | `lib/api.ts` 是移植来的，里面有一批后端没实现的路径（§2.2） |
| 真实沙箱容器里的技能同步与路径语义 | 沙箱用例只用了记录调用的假对象 |
| 依赖挂掉时的降级行为 | 「Redis 挂了不拦住活儿」这类取舍只有真实故障才验得到 |
| 跨环境的会话隔离提示 | 需要同时存在 local / sandbox 两种会话 |

---

## 0. 准备

### 0.1 起服务

```bash
# 后端 2024 + 前端 3000（脚本会先等后端就绪再起前端）
.venv/Scripts/python.exe scripts/start_all.py
```

- 关服务用 `scripts/stop_all.py`（会兜底清理 2024 / 3000 端口占用）。
- ⚠️ `start_all.py` 用的是 **uvicorn 不带 `--reload`**：改后端代码、改 `.env` 之后
  **必须重启**，否则你以为在测新行为、其实跑的是旧进程。
- 前端通过 `VITE_DASHBOARD_API_BASE_URL=http://127.0.0.1:2024` **跨源**调后端
  （不是走 Vite 代理），因此 **CORS 是真实生效的**：后端白名单里是
  `127.0.0.1:3000` 与 `localhost:3000`。用 `localhost:3000` 和 `127.0.0.1:3000`
  各访问一次，确认两种写法都能用（白名单里两个都列了）。

### 0.2 依赖三项连通

```bash
redis-cli -h 127.0.0.1 -p 6379 -a root --no-auth-warning ping   # 期望 PONG
```

| 服务 | 用途 | 必需性 |
|---|---|---|
| Redis 6379（**密码 `root`**） | Checkpoint | 必需 —— 连不上则任何一轮都跑不完 |
| MySQL 3306 | 业务数据 | 必需 |
| PostgreSQL 5432 | 长期记忆 | 可选，但本机是通的，**要测就开着** |

> 本机 `.env` 里 `MYSQL_DATABASE=xihe` / `PG_DATABASE=xihe`。
> **不要照 README 的 `xihe-agent` 去建库** —— README 那份库名和实际 `.env` 不一致。

### 0.3 数据与日志落点（先认清，否则会查错地方）

`.env` 里 `XIHE_AGENT_DATA_DIR` / `XIHE_AGENT_LOG_DIR` **没配**，所以落**仓库内**：

| 内容 | 落点 |
|---|---|
| 自动化任务定义 | `data/automations.json` |
| 自动化循环状态 | `data/automation_state/<id>.json` |
| 待审批命令 | `data/pending_command_approvals.json` |
| 自定义模型 | `data/custom_models.json` |
| 内置模型覆盖 | `data/builtin_model.json` |
| 命令审批设置 | `data/command_approval.json`（**首次保存后才生成**） |
| 技能检索开关 | `data/skill_retrieval.json`（**首次保存后才生成**） |
| 后端日志 | `logs/backend.log` |

> `.env` 里的 `XIHE_DATA_DIR` / `XIHE_LOG_DIR` 是**读不到的旧键**，程序认
> `XIHE_AGENT_*`。`E:/Xihe/logs/` 那份是 9 月 10 日前的旧文件，别去看它。

### 0.4 技能与工作区落点

`.env` 里 `AI_WORKSPACE_ROOT=E:/ai_workspace`，技能目录默认 `<全局技能目录>/skills`
（`Settings.skills_dir`）。测试前记下它当前的实际值（个人设置页「全局工作目录」里能看），
后面所有技能落点都相对它算：

```
<全局技能目录>/                          ← global
<全局技能目录>/.project-set/<项目名>/     ← project_set（项目名保留原始大小写）
<项目文件夹>/.xihe-agent/skills/          ← local（需项目已绑定本地文件夹）
```

### 0.5 测试用仓库

用测试仓库，**别用会改到主分支的仓库**。技能/审批/回退类用例都只能拿它练手。

### 0.6 观测速查

| 想看什么 | 看哪里 / 怎么看 |
|---|---|
| 后端活着 | `curl http://127.0.0.1:2024/health` |
| 所有接口实际存在哪些 | `curl -s http://127.0.0.1:2024/openapi.json \| python -m json.tool` |
| 一轮对话的原始 SSE 帧 | 浏览器 DevTools → Network → 那条 `stream-message` → EventStream |
| 后端报错栈 | `logs/backend.log` 尾部 |
| 前端报错 | DevTools → Console；4xx/5xx 看 Network 里 `detail` 字段 |
| 业务数据（Thread/Run/Finding） | MySQL：`use xihe; show tables;` |
| Checkpoint 是否存在 | `redis-cli -a root --no-auth-warning --scan --pattern 'checkpoint*'` |
| 长期记忆 | PostgreSQL：`\dt`、`select * from store limit 5;` |

---

## 1. 质量闸门（每次改动后的基线，不是这份清单的重点）

先跑一遍自动化基线，把结果抄进 §11 结果表；**之后所有用例都在一个绿的基线上跑**，
否则分不清是新缺陷还是本来就没过。

| 编号 | 命令 | 期望 |
|---|---|---|
| 1.1 | `.venv/Scripts/python.exe -m pytest -q` | **458 passed**（用例数变了先确认是有意加的） |
| 1.2 | `.venv/Scripts/ruff.exe check .` | 无**新增**告警 |
| 1.3 | `.venv/Scripts/lint-imports.exe` | `Contracts: 1 kept, 0 broken` |
| 1.4 | `cd frontend && npx tsc --noEmit` | 无输出 |
| 1.5 | `cd frontend && npx vitest run` | 全绿 |
| 1.6 | `cd frontend && npm run build` | 构建成功 |

- **1.2 的判据是「按文件比对规则集合」，不是数字增减。** 存量规则集合是
  `B008/B904/F401/I001/UP017`。正确做法：`git worktree add --detach D:/tmp/xihe-tmp/base-wt HEAD`，
  在**同结构工作树**里跑一遍 ruff，逐文件对比规则集合。
  > ⚠️ 别用 `git show` 把基线文件导到临时目录再跑 —— `per-file-ignores` 按路径匹配，
  > 那样会虚高出一堆假告警。也别吞掉 worktree 创建的输出：空集合不等于「一致」。
- **不要跑 `ruff format`**：会重排 82 个未格式化文件，把 diff 淹掉。

---

## 2. 接口契约与前后端对齐

> 这一节是**单测完全没盖的**：后端 458 条不测路由是否存在，前端 6 个用例不测调用是否有效。

### 2.1 路由烟测

```bash
curl -s http://127.0.0.1:2024/openapi.json | python -m json.tool > /tmp/xihe-openapi.json
grep -o '"/dashboard/api[^"]*"' /tmp/xihe-openapi.json | sort -u > /tmp/xihe-routes.txt
wc -l /tmp/xihe-routes.txt
```

对着下面这张表（**代码实测**出来的，比 README 全）逐条确认在 `/tmp/xihe-routes.txt` 里，
少一条就是路由没注册：

| 分组 | 路径 |
|---|---|
| 基础 | `/health` |
| 用户 | `GET /me`、`GET /options`、`GET /profile` |
| 模型 | `GET/POST /custom-models`、`PUT/DELETE /custom-models/{id}`、`GET/PUT /builtin-model` |
| 设置 | `GET/PUT /command-approval-settings`、`GET/PUT /skill-retrieval-settings` |
| 仓库 | `GET /repos`、`GET /my-mapping` |
| 会话 | `GET /threads`、`GET /threads/{id}`、`POST /threads/stream-message`、`POST /threads/{id}/stream-message`、`POST /threads/{id}/edit-preview`、`PUT /threads/{id}/view-snapshot`、`PATCH /threads/{id}/title`、`DELETE /threads/{id}` |
| 审批 | `GET/POST /threads/{id}/approval`、`POST /threads/{id}/approval/explanation` |
| 技能 | `GET /skills`、`GET /skills/{id}`、`POST /skills`、`PUT/DELETE /skills/{id}`、`POST /skills/upload` |
| 工作区 | `GET/PUT /workspace`、`GET /workspace/pick-folder`、`GET /workspace/projects`、`PATCH /workspace/projects/rename`、`POST /workspace/projects/bind-folder`、`DELETE /workspace/projects/{name}/unbind-folder` |
| 自动化 | `GET/POST /schedules`、`PATCH/DELETE /schedules/{id}`、`GET /schedules/{id}/state`、`POST /schedules/{id}/run`、`POST /schedules/{id}/resume`、`POST /schedules/{id}/reset`、`PATCH /schedules/{id}/budget` |

> README 的接口清单**落后于代码**：`GET /profile`、`POST /threads/{id}/edit-preview`、
> `PUT /threads/{id}/view-snapshot`、`POST /schedules/{id}/resume`、`PATCH /schedules/{id}/budget`、
> `PATCH /workspace/projects/rename` 都不在里面 —— 以实测为准，顺手把 README 补齐。

### 2.2 ★ 前端调用的接口是不是真的存在

`frontend/src/lib/api.ts` 是从云端版本**移植过来的**，里面有一批本地后端**根本没有**的路径。

| 前端函数 | 请求 | 后端有没有 |
|---|---|---|
| `api.profile` | `GET /profile` | ✅ 有 |
| `api.repos` / `api.myMapping` | `GET /repos` / `GET /my-mapping` | ✅ 有 |
| `api.saveProfile` | **`PUT /profile`** | ❌ 只有 GET，没有 PUT |
| `api.listReviewStyles` 等 6 个 | `/review-styles*` | ❌ 无 |
| `api.getTeamSettings` / `saveTeamSettings` | `/team-settings` | ❌ 无 |
| `api.usageLeaderboard` | `/agent-usage-leaderboard` | ❌ 无 |
| `api.adminListUserMappings` | `/admin/user-mappings` | ❌ 无 |
| `api.logout` | `POST /auth/logout` | ❌ 无 |
| `loginUrl()`（登录页那个按钮） | `GET /auth/dev-login` | ❌ 无 |
| `slackConnectUrl()` | `/slack/login` | ❌ 无 |

**要做的事：**

1. 在 `frontend/src` 里搜这些函数名，确认**有没有调用方**。目前只在 `lib/profile.ts`
   里看到 `api.profile` / `api.repos` / `api.saveProfile` 被用到，其余全是死代码。
   把结论记下来：**死代码** 还是 **会被点到的 404**，性质完全不同。
2. 打开 `http://127.0.0.1:3000/login`，点「使用控制台账号继续」。
   - 期望：**`GET /dashboard/api/auth/dev-login` 返回 404**（后端没这个路由）。
   - 但同时注意：`GET /me` 是**恒定返回**一个写死的用户（`login: "XiheAgent"`,
     `is_admin: true`），**从不会 401**。所以 `useSession()` 永远有值、`/login` 永远
     会 `Navigate` 到 `/my-settings` —— 这个按钮在正常路径下**走不到**。
   - 记下这是哪一种：「不可达路径上的坏按钮」还是「用户真的会点到」。
3. 结论只有两种，都要写清楚：**该删的死代码**，还是**该补的后端接口**。

### 2.3 错误语义

| 请求 | 期望 |
|---|---|
| 不存在的 thread：`GET /threads/nope` | **404** |
| 不存在的 thread：`DELETE /threads/nope` | **404** |
| 不存在的技能：`DELETE /skills/nope?scope=global` | **404** |
| 技能请求不带 `scope` | **400**（`scope` 创建/更新/删除时必填） |
| 非法 `scope` 值（如 `scope=xxx`） | **400** |
| `PUT /skill-retrieval-settings` 传 `{"top_k": 99999}` | **400**，且**存量值不变**（不许静默夹取） |
| `PUT /command-approval-settings` 传未知类别 `{"local":{"不存在的类别":"allow"}}` | 未知类别被**过滤掉**，其余正常保存 |

- 「不许静默夹取」的验法：改前读一次 `GET`，改后**再读一次**，确认还是旧值。

---

## 3. 会话全流程（产品主线）

> 对应 `PRODUCT.md` 的 6 条 Core Jobs。这一节是**用户能直接感知的**部分，
> 单测只覆盖了流式消息解析，页面交互零覆盖。

### 3.1 新建会话 + SSE 流式

1. 首页选仓库（或填仓库 URL）、选模型，输入一个**只读**任务：
   `列出仓库根目录的文件并说明这个项目是做什么的`，发送。
2. 全程开着 DevTools 的 EventStream 面板。

- 期望：
  - 发送后**立刻**出现用户消息与一个「运行中」状态（不能等模型返回才显示）；
  - 流式帧按段落推进，最终事件类型落在 `text` / `tool-execution` / `todo` / `error` 之内
    （前端 `ChunkKind` 的全集是 `text|code|error|list|tool-execution|todo|image`）；
  - 工具调用在界面上是**折叠区**（渐进呈现），不是直接铺开；
  - 会话状态从 `running` → `finished`；刷新页面后内容**仍在**（说明落到了
    MySQL/Checkpoint，不是只活在前端内存里）。
- 不对时先看：`logs/backend.log` 尾部；MySQL 里 `runs` 这条记录的状态。

### 3.2 落库核查（前端好看 ≠ 后端存对）

跑完上面这一轮后，直接查库：

```sql
use xihe;
select id, status, created_at from runs order by created_at desc limit 3;
select thread_id, role, left(content, 40) from thread_messages order by created_at desc limit 10;
```

- 期望：`runs` 有新记录且状态与界面一致；`thread_messages` 里**用户与助手消息都成对存在**；
  `run_events` 里能看到这一轮的工具调用记录。
- ⚠️ `run_events` 是**临时表**语义（会被清理），别拿它当长期证据。

### 3.3 命令审批三态

在会话里分别触发（用测试仓库）：

| 编号 | 让它执行 | 期望档位 | 期望表现 |
|---|---|---|---|
| 3.3a | `git status` | `allow` | **不弹审批**，直接跑 |
| 3.3b | `mkdir foo` 或 `npm install` | `approval` | 输入框**上方内联**面板弹出，带命令详情 + 「解释」「拒绝」「允许一次」 |
| 3.3c | `git push --force origin main` | `deny` | 直接拒绝，**不弹面板、不给放行按钮** |

- 3.3b 的「解释」：点一下，期望**几秒内**返回一段中文说明（做了什么 / 主要影响 / 为什么需要确认），
  且说明里**不含**命令之外的信息（解释器不执行命令、不改写命令、不替你决定）。
- 3.3b 点「允许一次」→ 命令执行、会话继续；点「拒绝」→ 会话**继续**（把拒绝交回模型换做法），
  不是直接把整轮判失败。
- ⚠️ **审批面板必须在输入框上方内联，不能是遮挡会话内容的居中弹窗。** 这是产品硬约束
  （`PRODUCT.md` / `DESIGN.md` 都写了），看到了居中弹窗就是缺陷。
- 不对时先看：`data/pending_command_approvals.json`；命令分类逻辑在
  `backends/command_policy.py`。

### 3.4 编辑最近一条用户消息 + 代码回退（最容易出错的一段）

1. 找一轮**已经产生过代码改动**的会话（比如让它改一个文件的注释）。
2. 点最后一条用户消息上的「编辑」，改文字后发送。
3. 发送前会弹**页面内**确认对话框（说明影响：替换 N 条消息 / 回退代码）。

| 编号 | 操作 | 期望 |
|---|---|---|
| 3.4a | 直接调 `edit-preview` 看它返回什么 | 含 `messages_to_replace`、`runs_to_replace`、`code_changes_detected`、`requires_rollback` 四个字段 |
| 3.4b | `stream-message` 带 `edit_message_id` 但**不带** `rollback_confirmed` | **400「请先确认回退影响」** |
| 3.4c | 编辑**非最后一条**用户消息（手工构造请求） | **409「只能编辑最近一条用户消息」** |
| 3.4d | 正常编辑并确认 | 旧消息与旧回复**从当前版本消失**；新消息可以**再次编辑**（关键：不能被锁死） |
| 3.4e | 任务产生的提交之后又出现新提交/未提交改动 | 回退被拒 → **409**，且**不能覆盖**掉那些改动 |

- 3.4e 是数据安全红线，一定要真的制造出「之后又有新改动」的场景再点回退。
- 3.4d 的「再次编辑」是 `PRODUCT.md` 明写的不变量：**编辑后的新消息仍必须可以再次编辑**。
- 不对时先看：`services/repo_rollback.py`；`git log` 是否真的被回退了。

### 3.5 历史版本只读

1. 在 3.4 那个会话里，用消息上的**版本导航**切到旧版本。

- 期望：
  - 旧版本**只读** —— 没有编辑入口，也不能在历史视图里执行回退；
  - 版本导航**同时切换**三块：用户消息、任务过程、助手正文（不能只换正文）；
  - 有明确的「返回当前版本」入口；
  - 悬停/键盘聚焦时出现时间、复制、版本导航，**布局尺寸不变**（不跳动）。
- 「复制」只复制**助手最终正文**，不含任务过程与工具输出。
- 不对时先看：`PUT /threads/{id}/view-snapshot` 是否 204；`threads` 表里版本字段。

### 3.6 跨环境会话隔离

1. 先在**当前模式**下建一个会话（`.env` 里现在是 `USE_SANDBOX=true`）。
2. 把这个会话的 `backend_type` 当成另一种环境去继续：

| 编号 | 操作 | 期望 |
|---|---|---|
| 3.6a | 请求里声明 `backend_type=sandbox`，但 `.env` 是 `USE_SANDBOX=false` | **400「您未开启沙盒配置，请开启后再尝试」** |
| 3.6b | 会话是 local 建的，用 sandbox 继续 | **409**「此对话在本地环境中创建，请切换到本地模式后继续」 |
| 3.6c | 界面上（模式与会话环境不匹配时） | 显示**只读提示**且**输入框禁用**，不是让你输完才报错 |

- 3.6c 需要真的切一次 `.env` 的 `USE_SANDBOX` 并**重启后端**才看得到。

### 3.7 会话生命周期

| 编号 | 操作 | 期望 |
|---|---|---|
| 3.7a | 改会话标题 | `PATCH /threads/{id}/title` 返回 200，列表与详情同步更新 |
| 3.7b | 删除会话 | **204**；`threads` / `thread_messages` / `runs` 里该会话的数据**级联删干净**（MySQL 里确认） |
| 3.7c | 删除后再刷列表 | 会话不再出现；**不报错** |
| 3.7d | 会话列表分页 | `GET /threads?limit=5` 生效 |

---

## 4. 技能系统全流程

### 4.1 四层作用域与覆盖

1. 在全局技能目录放一个 `demo-e2e`（`global`）。
2. 在 `<全局技能目录>/.project-set/<项目名>/` 放**同名**技能（`project_set`）。
3. 界面上把当前项目设为那个项目名。

- 期望：同名时**后写的层覆盖先写的层**（`project_set` 盖 `global`）；技能列表里
  两层的落点路径清楚可辨；`builtin`（`agent/skills/`）那一层**不挂载**（只在
  `<全局技能目录>` 里看得见它被装出来的副本）。
- 项目集合目录名**保留用户输入的原始大小写**（`Xihe` 就是 `Xihe`，不折成小写）。
- ⚠️ **技能名**受规范约束（只能小写字母/数字/连字符），**目录名**不受约束 —— 别把两者搞混。

### 4.2 校验规则（口径必须和加载器一致）

| 输入 | 期望 |
|---|---|
| 没有 `---` frontmatter | **400** |
| `name` 含大写或下划线（如 `My_Skill`） | **400**（须匹配 `^[a-z0-9]+(?:-[a-z0-9]+)*$`） |
| `name` 空 / 超 64 字符 | **400** |
| `description` 空 / 超 1024 字符 | **400** |
| 单文件 > 512KB | **400** |
| zip 包 > 8MB 或 > 200 个文件 | **400** |
| zip 内含 `../evil.md` 或绝对路径 / 盘符 | **400（拒绝路径穿越）** |

- **这一节的判据是「接口说成功 ⇒ 加载器真的能加载」。** 接口放行、加载器静默丢弃 =
  **幽灵技能**，属于缺陷。验法：过校验的技能必须在**下一条消息**的提示词里真的出现。

### 4.3 上传与附属文件

1. 上传一个 `.md` 技能。
2. 上传一个 `.zip` 技能包（含 `SKILL.md` + `scripts/x.py`）。
3. 再上传一次同名技能，`overwrite=true` / `false` 各试一次。

- 期望：`created` 里有它；`extraFiles` 列出附属文件；技能详情
  （`GET /skills/{id}`）能读到 `SKILL.md` 全文；覆盖开关语义正确
  （`false` 时不覆盖、报明确原因，不是静默改个名）。
- 技能来源在技能目录的 `.origin` 标记里（`builtin` / `user` / `agent`）；界面上
  **只对 `agent` 显示「智能体创建」徽章**。

### 4.4 Agent 入口 `create_skill`

1. 在会话里让 Agent 把某个可复用流程沉淀成技能。

- 期望：技能真的落盘；`.origin` 是 `agent`；界面显示徽章。
- **红线**：技能目录是**只读资产**，`create_skill` 是**唯一**合法写入通道。让 Agent
  尝试用 shell（`echo > .../SKILL.md`）或文件工具直接改技能文件 —— **必须改不动**。
  这条破了就是权限模型失效。

### 4.5 技能变更后的沙箱同步（需要真容器）

| 编号 | 操作 | 期望 |
|---|---|---|
| 4.5a | 会话**空闲**时改一个 global 技能，然后发下一条消息 | 容器里 `/workspace/skills/` 下已更新 |
| 4.5b | 会话正在 `running` 时改技能 | **只登记「待同步」**，不在中途换目录；下一次构建智能体前补上 |
| 4.5c | 在主机上删掉一个技能，再触发同步 | 容器里那个技能**也消失**（同步是「先清空再写入」，是幂等的） |
| 4.5d | 改 `project_set` 技能 | **只推给该项目集合下**的沙箱会话，别的不受影响 |
| 4.5e | 改 `local` 技能 | 与沙箱无关，**直接跳过**（容器里本地层本来就不存在） |

- 期望的容器内落点：`/workspace/skills/`（global）、
  `/workspace/skills/.project-set/<项目集合名>/`（project_set）。
  沙箱 backend **不做虚拟路径映射**，所以传的是容器内真实路径。
- 验容器内文件：让 Agent 在会话里 `ls -R /workspace/skills`，不要在本机猜。
- 不对时先看：`services/sandbox_skill_sync.py`；`server._apply_pending_skill_refresh`。

### 4.6 技能检索开关

个人设置 → 技能检索。落盘 `<data_dir>/skill_retrieval.json`。

| 编号 | 操作 | 期望 |
|---|---|---|
| 4.6a | `mode=off`（默认） | 提示词里列出**全部**技能，与未引入检索时**逐字一致** |
| 4.6b | `mode=auto` + 技能数 > `auto_threshold`（默认 60） | 才触发检索 |
| 4.6c | `mode=on` | 每次都按当前任务检索，最多 `top_k`（默认 20）个 |
| 4.6d | `top_k` 传非法值 / 越界 | **400**，存量值不变 |
| 4.6e | 改动后**不重启**，只发下一条消息 | 新设置生效（开关挂在 `wrap_model_call` 上，不需要重启） |
| 4.6f | 查询为空 / 无命中 / 检索抛异常 | **fail-open**：退回列出全部技能，**不能一条都不列** |
| 4.6g | 手改 `skill_retrieval.json` 成损坏 JSON 后重启 | 回退为 `mode=off`，服务能起来 |

- **常驻技能不受额度约束**：`repo-bootstrap-analysis` / `ai-coding-implementation` /
  `code-review` 必须**始终**在提示词里，并优先占 `top_k` 名额。把 `top_k` 设成 1
  再验一次 —— 常驻技能仍要在位。
- **技能正文永远不进提示词**：写进去的只有 `name`+`description`+`path` 两行。
  验法：把某个技能正文写一句独一无二的话，然后在提示词里搜 —— **不该出现**。
- 可跑一遍现成的评估（它量的就是提示词形状）：

```bash
.venv/Scripts/python.exe -m eval.skill_retrieval --label baseline
.venv/Scripts/python.exe -m eval.skill_retrieval --negative-control   # 命中率应掉到 ~20%
```

### 4.7 三处下拉的选项矩阵（有意设计，不是 bug）

| 位置 | 未指定项目 | 已指定项目（本地） | 已指定项目（沙箱） |
|---|---|---|---|
| 对话内技能面板 · 顶部筛选 | 只有「全局」 | 全局 / 项目 / 本地 | 全局 / 项目 |
| 对话内技能面板 · 上传工作域 | 只有「全局」 | 全局 / 项目（项目已绑文件夹时含本地） | 全局 / 项目 |
| 技能接口页 · 新建技能作用域 | 全局 / 项目 / 本地 | 全局 / 项目 / 本地 | — |

- 逐格点一遍，**对不上就是缺陷**。注意沙箱下**没有「本地」**这一层。
- 上传工作域以后端 `GET /skills` 的 `writableScopes` 为准（面板会把 `backendType` 传上去）。

---

## 5. 工作区与本地文件夹

### 5.1 工作目录三项

`个人设置 → 全局工作目录`：`workspace_root` / `projects_dir` / `skills_dir`。

| 编号 | 操作 | 期望 |
|---|---|---|
| 5.1a | 改 `skills_dir` 到新目录并保存 | `GET /workspace` 返回新值；**技能列表随之切到新目录** |
| 5.1b | 「浏览文件夹」按钮 | 弹出**系统**文件夹选择框；返回的路径能直接落到输入框（含中文/空格的路径也要试） |
| 5.1c | 填一个不存在的目录 | 明确报错，**不要静默创建一堆空目录** |
| 5.1d | 改完重启后端 | 配置持久（存在 MySQL `settings` 表 / `data/` 里，不是内存） |

### 5.2 项目绑定

| 编号 | 操作 | 期望 |
|---|---|---|
| 5.2a | `GET /workspace/projects` | 列出项目，含各项目的 `local_folder` 绑定信息 |
| 5.2b | 绑定一个项目到本地文件夹 | 返回绑定关系；**落 MySQL `project_local_folders` 表**（查库确认） |
| 5.2c | 绑定后在该项目下发一条消息 | Agent 的**工作根目录就是那个本地文件夹**（不再走 `/projects/<repo>` 中间层） |
| 5.2d | 检查文件夹内自动生成的目录 | 出现 `.xihe-agent/{skills,reviews/<repo>/}` |
| 5.2e | `PATCH /workspace/projects/rename` | 重命名生效；**已有绑定跟着走**（别丢绑定） |
| 5.2f | `DELETE .../unbind-folder` | 解绑；界面「本地」层随之不可用 |
| 5.2g | 同一项目的**多个会话** | 共享同一工作根目录（绑定是按项目名持久化的） |

### 5.3 本地文件夹模式的两条边界

- **双根沙箱**：本地文件夹可写，全局工作区（`.secrets`、运行时配置）保持**只读**。
  验法：让 Agent 试着往全局工作区里写一个文件 —— 应被拒。
- **虚拟路径映射**：绑定后 `/skills/`、`/reviews/` 映射到 `.xihe-agent/` 下的对应子目录。
  验法：在会话里 `ls /skills/`，看到的应是 `.xihe-agent/skills/` 的内容。
- 未绑定本地文件夹的项目：继续用全局工作区模式，**「本地」作用域不可用**（不是报错，
  是选项里不出现）。

---

## 6. 模型设置与配置持久化

### 6.1 自定义模型 CRUD

| 编号 | 操作 | 期望 |
|---|---|---|
| 6.1a | 新建自定义模型（name / model_name / api_key / api_base） | **201**；`data/custom_models.json` 里出现；`GET /options` 的 `models` 里多一条 |
| 6.1b | 设为默认 | `is_default` 唯一（**同时只能有一个**）；`/options` 的 `default_agent_model` 跟着变 |
| 6.1c | 编辑 | 更新落盘；`api_key` 不回显明文（或按现约定脱敏） |
| 6.1d | 删除 | **204**；`/options` 里消失；**用着它的会话**要有可理解的降级（不是 500） |
| 6.1e | 停用/清空 `DEEPSEEK_API_KEY` 后只靠自定义模型 | 仍能跑通一轮（`README` 说这种情况 `.env` 的 key 可选） |

### 6.2 内置模型覆盖

| 编号 | 操作 | 期望 |
|---|---|---|
| 6.2a | 改内置模型的 `api_base` / `model_name` | 保存成功；`has_overrides == true` |
| 6.2b | 读取 | `api_key_masked` 是脱敏值，**不是明文** |
| 6.2c | 恢复默认 | `has_overrides` 回到 `false` |

### 6.3 优先级（README 写死的那条链）

对话级指定 > 默认自定义模型 > 内置模型覆盖 > `.env` 回退。

1. 建两个自定义模型 A、B，把 B 设为默认。
2. 在会话里**显式选 A** 发一条消息。

- 期望：**用的是 A**（对话级最高）。验法：看请求打到了哪个 `api_base`
  （两个模型指向不同 `api_base` 时最好判），或看日志里选的模型名。
- 4 层各压一次，共 4 条结论。

### 6.4 配置损坏回退

把 `data/custom_models.json` 改成非法 JSON → 重启后端。

- 期望：服务**能起来**；界面上自定义模型列表为空或给出可理解提示，
  **不是白屏 / 500 循环**。`builtin_model.json` 同样试一次。

---

## 7. 自动化任务

**不重复**：完整清单见 `docs/AUTOMATION_FULL_LOOP_TEST.md`（A 组循环与验证门 / B 组容器内验证 /
C 组 cron 自触发 / D 组并发 / E 组审批与表决 / F 组生命周期与界面）。

本文件只补它**没覆盖**的交叉点：

| 编号 | 操作 | 期望 | 为什么在这份里 |
|---|---|---|---|
| 7.1 | 跑完一轮后，去 MySQL 查这一轮的 `runs` 记录 | 状态与 `data/automation_state/<id>.json` 里的结论**一致** | 自动化那份只看状态文件，没查库 |
| 7.2 | 任务列表页与 `GET /schedules` 对比 | `lastTriggeredAt` / `lastRunId` / `lastError` 与文件里一致 | 前后端字段对齐 |
| 7.3 | 自动化任务点进它产生的会话 | 能看到完整的一轮过程（这是排查唯一的现场） | 与 §3 的会话链路打通 |
| 7.4 | 任务用**自定义模型**跑一轮 | 用对模型；token 统计进 `budget.tokensUsed` | 与 §6 打通 |
| 7.5 | 任务在某项目下跑，该项目**已绑本地文件夹** | 仓库目录解析正确，验证在正确目录里跑 | 与 §5 打通 |
| 7.6 | 杀掉 Redis 后触发一轮 | **记警告后照常跑**（登记降级为本进程互斥） | 与 §9 打通 |

---

## 8. 安全边界

> 这节是**故意破坏**，全在测试仓库 + 非生产环境做。

### 8.1 命令审批策略

| 编号 | 命令 | 期望 |
|---|---|---|
| 8.1a | `git status` | `allow` / `read_only` |
| 8.1b | `git -C <路径> status` | **与 8.1a 同判**（全局选项先被剥掉） |
| 8.1c | `git -C <路径> push --force` | **`deny` / `git_destructive`**，与 `git push --force` 同判 |
| 8.1d | `echo hi > /tmp/x` | `approval`（输出重定向会改工作区文件） |
| 8.1e | `sudo rm -rf /` | **`deny` / `privilege_escalation`** |
| 8.1f | `git reset --hard HEAD~1` | **`deny`** |
| 8.1g | `git clean -fd` | **`deny`** |
| 8.1h | `cat ../../.env` | **`deny` / `protected_secrets`** |
| 8.1i | `docker run --privileged` | **`deny` / `sandbox_escape`** |
| 8.1j | `export TOKEN=xxx` | **`deny` / `credential_injection`** |
| 8.1k | 空命令 | **`deny` / `invalid`** |
| 8.1l | 一个完全不认识的命令（如 `frobnicate`） | `approval` / `unknown`（**不默认放行**） |
| 8.1m | `git status && sudo rm -rf /` | `deny`（复合命令里**任一段**是硬拒就整体拒） |

- ⚠️ **8.1b / 8.1c 是重点**：不剥全局选项的话，只读命令会掉进「未识别 → 每次询问」，
  而破坏性命令会**绕着硬拒绝走**。Agent 在仓库外工作时天然会写 `git -C <路径>`。

### 8.2 设置不能覆盖高危规则

1. 个人设置 → 命令审批，把能开的都设成「自动允许」。
2. 再跑 8.1 里的硬拒命令。

- 期望：**硬拒那 8 类一条都不受影响**（提权、磁盘/启动配置、系统/注册表、破坏性 Git、
  受保护密钥、沙箱逃逸、凭据注入）。设置**能覆盖的只有非高危类别**。
- `external_write`（向外部服务写入，如 `curl -X POST`、`git push`）是**锁定的 `approval`**：
  设置改不成「自动允许」，但仍可被自动化任务的**逐次表决**放行（见自动化那份 E2）。
- 「未知类别被过滤」：`PUT` 里塞一个不存在的类别名，它**不能**被写进设置里。

### 8.3 SSRF

让 Agent 抓取内网地址（`http://127.0.0.1:2024/health`、`http://169.254.169.254/`、
`http://localhost:6379`）：

- 期望：被 `utils/http_utils.py` 的 SSRF 防护拦下，**不返回内网内容**。
- 验法：看返回内容是不是内网的响应体；**必须是否**。

### 8.4 技能目录写入通道唯一性

见 §4.4 的红线，这里再单独试一次：让 Agent 用 `execute` 写技能目录。

- 期望：被拒（审批策略或权限层任一处拦下即可），且**文件真的没变**。

### 8.5 沙箱不是绕过审批的通道

在沙箱模式下把 8.1m、8.2 的硬拒命令再跑一遍。

- 期望：**结论与本地一致**。沙箱只在隔离容器内自动放行「可恢复的构建/脚本/依赖/只读网络」，
  删除、远端写入、凭据、提权、沙箱逃逸仍然受保护。

---

## 9. 故障与降级

> 这节的价值最高：**代码里的取舍注释全在这里被证伪或证实**。每条都要记「实际表现」。

| 编号 | 制造什么故障 | 期望 | 备注 |
|---|---|---|---|
| 9.1 | 把 Redis 密码改成错的（`REDIS_PASSWORD=wrong`）后重启 | 判据是**错误信息是否指向 Redis 认证**（不能只是 `NOAUTH`/`WRONGPASS` 裸栈）；启动成不成功都如实记下 | README 专门警告过「`NOAUTH` 不是没设密码，正好是反的」 |
| 9.2 | 停掉 Redis 服务，跑一轮对话 | 有明确报错；**不让进程挂死** | 记录是 500 还是流里报错 |
| 9.3 | 停掉 Redis，触发一轮自动化 | **记警告后照常扫**（扫描租约降级）；登记降级为本进程互斥 | 代码注释写的取舍，来验一下 |
| 9.4 | 停掉 MySQL，调 `GET /threads` | 明确报错，不返回假空列表（返假空会让人以为「数据没了」） | 这两者体验天差地别 |
| 9.5 | 停掉 PostgreSQL | **不影响**不使用 memories 的普通会话 | PG 在 README 里标的是「可选」 |
| 9.6 | `DEEPSEEK_API_KEY` 改成错的 | 会话里**看得见**错误（流里 `error` 事件或界面错误态），不是一直转圈 | |
| 9.7 | `SANDBOX_HOST` 指到一个不可达地址，`USE_SANDBOX=true` 重启 | **降级到 `LocalShellBackend`**，服务仍可用 | `create_backend` 会吞异常回退本地 Shell |
| 9.8 | 后端停掉，前端继续操作 | 前端给**可理解的错误**，不是白屏 | 「运行中的会话」这时会怎样？记下来 |
| 9.9 | 后端重启后打开一个「运行中」的会话 | 状态能收敛（靠 Checkpoint），不是永远转圈 | |
| 9.10 | 2024 / 3000 被别的进程占着启动 | 明确报「端口被占用」，不是静默起不来 | |
| 9.11 | 连点两次「新建会话发送」 | 不会产生两条重复的用户消息 | |
| 9.12 | 长任务跑到一半关掉浏览器标签再回来 | 会话**没丢**（后端在跑）；状态与库里一致 | |

---

## 10. 界面与可访问性回归

> 对应 `PRODUCT.md` 的 Experience Principles 与 `DESIGN.md` 的 token 约定。
> **注意：类名拼错单测照样全绿** —— 这节必须靠眼睛。

| 编号 | 检查什么 | 期望 |
|---|---|---|
| 10.1 | 全站搜原生弹窗 | **不存在** `window.alert` / `confirm` / `prompt`；所有确认都是页面内对话框 |
| 10.2 | 所有下拉（技能筛选、上传工作域、自动化表单、我的设置） | 一律用共享 `components/ui/select`；**菜单从触发器下方浮出**（有浮层感 + 滑入动画），不是原地盖住触发器 |
| 10.3 | 图表/数字 | 数字用 `tabular-nums`；代码块、diff、工具输出用等宽字体 |
| 10.4 | 深浅色切换 | 两种主题下文字都清晰可读；语义色只在深/浅背景对比足够时出现 |
| 10.5 | 键盘可达 | Tab 能走完所有交互元素；**焦点可见**；图标按钮有可访问名称与 hover 提示 |
| 10.6 | `prefers-reduced-motion: reduce` | 开启后所有入场/错峰/展开动画**关闭**（本项目有张关闭清单，新加动画类必须进清单） |
| 10.7 | 动效节奏 | 进入约 `cubic-bezier(0.16,1,0.3,1)`、退出 `cubic-bezier(0.4,0,1,1)`、180–300ms；列表行错峰入场，**不闪一下直接出现** |
| 10.8 | 布局稳定性 | 悬停/聚焦时出现的时间、复制、版本导航**不改变布局尺寸** |
| 10.9 | 移动端（窄到 ~375px） | 侧栏收起；**没有固定 `h-screen`**（用可滚动 `min-h-0`）；无横向滚动、无内容遮挡、无异常换行 |
| 10.10 | 空/加载/错误/禁用态 | 四种状态**都有中文文案**，不是空白或英文占位 |
| 10.11 | 自动化页 | 页面里**不得出现原生 `<select>`**（有单测钉着，但要在真页面确认一次） |
| 10.12 | 长内容 | 长任务记录默认**折叠为摘要**，能再次展开 |

---

## 11. 红线（跑完必须成立的不变量）

不管哪条用例怎么跑，这六条破了就是缺陷，优先修：

1. **技能目录只读、`create_skill` 是唯一写入通道。** 模型用 shell / 文件工具改技能文件必须失败。（§4.4 / §8.4）
2. **硬拒命令一次都不执行**，不能被设置放行、也不能被沙箱绕过。（§8.1 / §8.2 / §8.5）
3. **代码回退不覆盖后来出现的改动。** 任务后有新提交或未提交改动时必须拒绝回退。（§3.4e）
4. **编辑后的消息仍可再次编辑**，且只有**最近一条**用户消息可编辑。（§3.4c / §3.4d）
5. **「没能验证」/「没能执行」不能算「失败」。** 模型报错、沙箱坏了、组件不可用，
   都不能记成「做错了」—— 会把人指去改一个根本没跑过的东西。（§7 交叉、自动化那份 A4/B2）
6. **前端的每个调用都必须有后端真实接口。** 移植残留的 404 路径要么删、要么补，
   不能留在代码里等着用户点到。（§2.2）

---

## 12. 结果记录

| 编号 | 结论 | 证据（状态片段 / 日志行 / 截图 / 库记录） | 备注 |
|---|---|---|---|
| 1.1–1.6 | | | 实际用例数：____ |
| 2.1 | | | |
| 2.2 | | | 死代码还是 404： |
| 2.3 | | | |
| 3.1 / 3.2 | | | |
| 3.3a–c | | | |
| 3.4a–e | | | |
| 3.5 | | | |
| 3.6a–c | | | |
| 3.7a–d | | | |
| 4.1 | | | |
| 4.2 | | | |
| 4.3 | | | |
| 4.4 | | | |
| 4.5a–e | | | |
| 4.6a–g | | | |
| 4.7 | | | 对不上的格子： |
| 5.1a–d | | | |
| 5.2a–g | | | |
| 5.3 | | | |
| 6.1a–e | | | |
| 6.2a–c | | | |
| 6.3 | | | |
| 6.4 | | | |
| 7.1–7.6 | | | |
| 8.1a–m | | | |
| 8.2 / 8.3 / 8.4 | | | |
| 8.5 | | | |
| 9.1–9.12 | | | 逐条记「实际表现」 |
| 10.1–10.12 | | | |

### 大概率会出问题的地方（先说在这里，免得当成自己的操作失误）

1. **§2.2 前端那批 404 调用** —— `lib/api.ts` 是移植来的，`PUT /profile`、
   `/review-styles`、`/team-settings`、`/auth/dev-login` 等后端都没有。
   判断标准是「会不会被用户点到」，不是「存不存在」。
2. **§4.5 沙箱技能同步的时机** —— 「运行中只登记待同步」这条路只有单测覆盖
   （假 backend 记录调用），真容器里从没验过「回话跑完真的补上了」。
3. **§9 故障降级那几条** —— 代码注释里写的取舍（「Redis 挂了不拦住活儿」、
   「PG 可选」）是**设计时的判断**，不是实测结论。这节就是来验它们的。
4. **§5.3 双根沙箱的只读边界** —— 需要真容器才能验，本机 `USE_SANDBOX=false` 时跳过并标注。
5. **§10 的可访问性** —— 没有任何自动化覆盖，只能靠人。建议至少把
   `prefers-reduced-motion` 与键盘 Tab 两条走完。

### 补测建议

跑完一轮之后，**把暴露出来的每个缺陷都变成一条单测或一条前端用例**，
并确认「临时还原缺陷 → 用例变红」。这份清单里凡是被单测接走的条目，
下个版本就可以从本文件删掉 —— 让它始终只装「只有人跑才验得到的东西」。
