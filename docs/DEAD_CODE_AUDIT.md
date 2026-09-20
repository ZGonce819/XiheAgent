# 死代码排查报告

排查对象：仓库当前工作区（基线 `14235ce`，排查时其上有 12 个文件未提交；本文档记录的是
从那次扫描到三条决策落地为止的**全部批次**）。
排查日期：2026-09-20。

> **怎么读**：第一到七节是**排查当时**看到的清单，保留原样以便核对当时判断对不对；
> 每张表都补了「状态 / 处置」列，写的是最终结果。**最终处置的完整说明在第八节。**
> 所以前面某张表里写着「后端不存在」而第八节说「已实现」，不是矛盾 —— 是时间差。

## 排查方法

三条线交叉验证，避免只靠一种工具的口径：

1. **vulture 2.16 扫两遍**。第一遍只喂产品代码（`agent/ api/ backends/ config/ database/ models/ schemas/ services/ store/ tools/ utils/ app.py server.py prompt.py`），
   第二遍把 `tests/ eval/ scripts/` 也喂进去。两遍的差集就是「产品路径走不到、但测试还在用」。
2. **自建 import 图**：逐个文件解析 `import` / `from ... import`，找出没有任何模块 import 的文件。
3. **前端引用图**：解析 `import` 路径（含 `@/` 别名）与导出符号的标识符出现次数；
   再把前端调用的接口路径与后端已注册路由做双向差集。

**已剔除的误报类型**（这些不算死代码，但工具会报）：

- FastAPI 路由处理器：靠 `@dashboard_router.get(...)` 注册，vulture 看不到调用点。共 50 个路由，全部剔除。
- `LocalShellBackend` / `OpenSandboxBackend` 的 `ls` / `read` / `write` / `edit` / `grep` / `glob` / `upload_files` / `download_files`：
  是 deepagents `BaseSandbox` 的抽象方法，由框架按名字调用。
- 中间件的 `wrap_tool_call` / `awrap_tool_call` / `wrap_model_call` / `before_agent` / `after_agent`：同样是框架钩子。
- pydantic 模型字段（`schemas/*.py`、`config/app_config.py` 里的 `mysql_host` 等）：靠序列化生效，不是死变量。
- pytest 夹具与测试函数。

---

## 执行结果（已按本文档清理完毕）

### 已删除

| 范围 | 内容 |
|---|---|
| 后端整文件 | `agent/graph/state.py`（孤儿模块，37 行） |
| 后端函数/方法 | 22 处，见第一节表格中标「已删」的行 |
| 后端死计算 | `agent/runtime/planning_router.py` 的 `plan_source`（见下方「修订」；这一条后来按第八节第 3 条重新实现了，不是回滚） |
| 前端整文件 | `components/agents/z-index.ts`、`ui/badge.tsx`、`ui/combobox.tsx`、`ui/separator.tsx`、`ui/input-group.tsx`、`routes/login.tsx` |
| 前端死导出 | `lib/profile.ts`（2）、`lib/api.ts`（3，含 `loginUrl`）、`lib/agents/api.ts`（1）、`lib/agents/types.ts`（3）、`AppShell.tsx`（1） |
| 前端死 API 方法 | 21 个（指向不存在后端的 13 个 + `myMapping`、`logout`、`unbindProjectFolder`、`updateProjectDisplayName`、`adminListUserMappings` 等） |
| 前端悬空类型 | 14 个（`ReviewStyle`、`TeamSettings`、`UsageLeaderboard*`、`ReviewerStats*`、`UserMappingsPage`、`TriggerMode`、`AutofixMode`、`GitFileStatus` 等） |
| 打包声明 | `pyproject.toml` 的 `[project.scripts]` 与 `packages.find` |
| 依赖 | `structlog`、`aiofiles`（后端）；`remark-breaks`、`zustand`、`web-vitals`、`playwright-core`（前端） |
| 既有 lint 残留 | 3 处未使用导入（`repo_mapping.py` / `workspace_config.py` / `thread.py`） |
| 登录入口（后续批次） | `frontend/src/routes/login.tsx` 整文件、`lib/api.ts::loginUrl`、`SessionUser.slack_oauth_enabled`、`UserInfoResponse.slack_oauth_enabled`、5 个页面的 `/login` 跳转 |
| 解绑 / 改显示名（后续批次） | `api/dashboard.py` 两条路由、`DashboardProjectUpdateDisplayNameRequest`、`lib/api.ts` 两个方法、`mysql_store` 两个方法、`project_folder_repo` 的 `delete` 与 `update_display_name` |

`input-group.tsx` 是删除 `combobox.tsx` 之后才暴露出来的：前者唯一的引用方就是后者。
这一类「删掉 A 才发现 B 也空了」的链，删完必须重跑一遍扫描，不能只扫一次。

### 修订（初版结论有两处是错的，已更正）

| 项 | 初版怎么说 | 实际是什么 | 处置 |
|---|---|---|---|
| `utils/codeup_utils.py::mask_codeup_token` | 「全仓没有 Codeup 令牌脱敏，是写好了没接上的安全缺口」 | **错的**。`utils/gitee_utils.py::mask_token` 的脱敏名单里已经有 `CODEUP_TOKEN` 和 `SCM_CODEUP_TOKEN`，而且它是**全部日志与工具错误**的必经之路（`agent/middleware/tool_error.py`、`repo_update.py` 等 10 余处调用）。`mask_codeup_token` 只是它的一个子集，是重复实现。 | **删除**。初版说「留着让缺口可见」的前提不成立——缺口不存在。 |
| `agent/runtime/planning_router.py::plan_source` | 「疑似漏传参数」 | 赋值两次、从未读取。往下追：唯一的候选使用点（第 151 行 `build_plan_user_content(prompt=...)`）已经**单独**传了 `revision_prompt`，模板里「原始用户需求」与「用户新的修改要求」是分开渲染的；把合并后的 `plan_source` 传进去会让修改要求重复出现。所以它不是漏传，是**没有合法用途的残留计算**。 | **删除**。连带删掉只被它调用的 `agent/runtime/_helpers.py::revision_source_prompt`。 |

**这次修订暴露出来的真实问题**（不是死代码，是半成品）：`plan_source_prompt()` 会去读方案消息的
`metadata.source_prompt`，但**全仓没有任何地方写入这个字段**——所以它永远走 fallback。也就是说
「跨轮累积原始需求」这套机制只写了读取端和合并端，写入端从来没接上。删除合并端之后，读取端仍然
能正常降级（返回 fallback）。

**这一条后来按「修」处置了**，见第八节第 3 条：写入端补在 `threads` 表的 `plan_source_prompt`
列上，而不是补在模型产出的消息 metadata 上（那三层本来就带不出 metadata）。

### 保留（与初版结论不同，理由如下）

| 项 | 为什么留着 |
|---|---|
| `backends/local_shell.py::health` | 删它会连带 `_is_writable`（只被它调用）一起消失，影响面超出收益。它是后端自检入口，`python_venv` 这类问题靠它暴露。**属于「没接线但有用」，建议接上而不是删。** |
| `backends/local_shell.py::get_work_dir` | 文档标注为 DeepAgents 协议方法。当前装的 deepagents 版本确实不调它，但删掉等于赌后续版本也不调，收益只有 7 行。 |
| `agent/graph/redis_checkpointer.py::checkpoint_has_writes` | **误报**。它是 `ReliableRedisSaver` 覆写父类方法的形参，签名必须一致，不能删。 |
| shadcn 生成组件里没人用的子组件 | 见第七节。 |
| `@tanstack/router-plugin`、`workbox-build`、`workbox-window`、`@tanstack/react-router-ssr-query`、三个 devtools 包 | 前端依赖里搜不到 import，但分别是 tanstack-start 构建链、PWA（`client.tsx` 用了 `virtual:pwa-register`）、SSR 集成的运行依赖。**没有实测就不动。** |
| `opensandbox-code-interpreter` | 沙箱运行时插件，代码里搜不到 import 不代表不需要。 |

### 验证

| 检查 | 结果 |
|---|---|
| `pytest`（改动前基线） | 5 failed / 506 passed / 3 skipped |
| `pytest`（清理后） | 与基线完全一致，无新增失败 |
| `pytest`（加完协作式停止后） | 5 failed / **523** passed / 3 skipped（新增 17 条），失败仍是那 5 条既有问题 |
| `pytest`（删登录入口后） | 5 failed / 523 passed / 3 skipped，与上一步完全一致 |
| `pytest`（删解绑/显示名后） | 5 failed / 523 passed / 3 skipped，仍与上一步一致 |
| `pytest`（修完 source_prompt 后） | 5 failed / **529** passed / 3 skipped（新增 6 条），失败仍是那 5 条既有问题 |
| `pytest`（修完那 4 条红测试后，默认环境） | **1 failed / 533 passed** / 3 skipped —— 只剩环境造成的那一条 |
| `pytest`（同上，`PYTHONPATH=` 绕开 safe-delete shim） | **534 passed / 3 skipped，全绿** |
| `ruff check` | 无新增问题。清理阶段清掉 3 处未使用导入；`planning_router.py` 的 `F841 plan_source` 是 ruff 自己报出来的，删完变干净；`codeup_utils.py` 的 5 处 `W293` 是既有 |
| `lint-imports` | 1 kept / 0 broken（新增的 `agent.runtime.cancellation` 没有破坏分层契约） |
| `tsc --noEmit` | 通过 |
| `vitest run` | 11 个文件 / 71 个用例全部通过 |
| 删除后重扫 | 前端真死导出 0 个；后端仅剩 `scripts/` 与 `eval/` 下的孤儿脚本 |
| 新列真库回归 | `plan_source_prompt` 写入可读、不传参数时保留、换新需求时覆盖（用一条临时记录验完即删） |

### 那 5 条「既有失败」的真相（2026-09-20 逐条查清）

以前一直笼统写成「既有问题」，从没查过。查完发现**一条都不是产品缺陷**：

| 测试 | 真因 | 性质 | 处置 |
|---|---|---|---|
| `test_backend_type_actually_changes_the_decision` | 文件里**定义了 `data_dir` 夹具却一个用例都没请求它**（文件开头还专门写了「必须隔离」），开发机真实的 `data/command_approval.json`（`local.run_build=allow`）漏进来，`ruff check .` 于是被判 allow 而非 approval | 测试隔离缺陷 | 夹具改 `autouse=True` |
| `test_a_finished_round_records_its_run_id` | `/schedules/{id}/run` 改用**独立守护线程**后立刻返回 202，用例没等就断言 → 竞态；同文件里就有 `wait_until` 没用 | 测试竞态 | 用 `wait_until` 包住断言 |
| `test_task_router_routing.py` × 2 | `is_simple_coding_task("分析这个仓库的目录结构")` 返回 **True** → 绕过被替换掉的方案分支继续往下走，走到准备仓库那步，而 `FakeStore` 只有 `get_thread` | 测试桩过期 | 把 `is_simple_coding_task` 钉成 `False` |
| `test_delete_state_reports_whether_anything_was_removed` | **环境**：WorkBuddy 的 safe-delete shim（`PYTHONPATH` 里的 `cli/vendor/shim/sitecustomize.py`）把 `unlink` 劫持到回收站；删**不存在**的文件时 `SHFileOperationW` 返回 0x2，抛 `OSError` 而代码只捕 `FileNotFoundError` | 环境 | 不改代码，`PYTHONPATH=` 跑即过 |

**为什么值得修**：套件一直红着，真回归会藏在里面看不出来 —— 这次修完，默认环境只剩 1 条、
干净环境全绿，再出红就是真问题。

**负向对照**：把 `data_dir` 的 `autouse=True` 临时摘掉，
`test_backend_type_actually_changes_the_decision` 重新报出原症状 `assert 'passed' == 'skipped'`。

改动前的 5 个失败与本次清理无关（是测试自身的问题，见上）。

---

## 一、确认没人用，可以直接删（后端 22 处）

判定标准：产品代码、测试、脚本、前端全都没引用；且不是框架按名字调用的。
**状态列是最终结果** —— 其中 3 处复核后判定为误报，没有删。

| 文件 | 位置（排查当时） | 符号 | 行数 | 状态 |
|---|---|---|---|---|
| `agent/graph/state.py` | 整文件 | `AgentTaskState` | 37 | 已删 |
| `agent/graph/checkpoint_history.py` | 35 | `_extract_user_prompt` | 21 | 已删 |
| `agent/graph/checkpoint_history.py` | 58 | `_has_visible_markdown_value` | 18 | 已删 |
| `agent/graph/checkpoint_history.py` | 95 | `_is_short_process_message` | 21 | 已删 |
| `agent/graph/graph.py` | 86 | `build_agent` | 14 | 已删 |
| `agent/graph/redis_checkpointer.py` | 12 | `checkpoint_has_writes` | 1 | **保留（误报）** |
| `backends/permissions.py` | 105 | `ensure_safe_command` | 12 | 已删 |
| `backends/permissions.py` | 122 | `ensure_safe_git_branch` | 20 | 已删 |
| `backends/permissions.py` | 144 | `ensure_safe_git_message` | 21 | 已删 |
| `backends/command_policy.py` | 167 | `command_requires_approval` | 2 | 已删 |
| `backends/local_shell.py` | 273 | `get_work_dir` | 7 | **保留** |
| `backends/local_shell.py` | 289 | `health` | 28 | **保留** |
| `backends/local_shell.py` | 1469 | `create_local_shell_backend` | 10 | 已删 |
| `backends/local_shell.py` | 1481 | `validate_local_shell_startup_config` | 7 | 已删 |
| `backends/opensandbox.py` | 605 | `list_running_sandboxes` | 37 | 已删 |
| `models/_helpers.py` | 38 / 63 | `_parse_json` / `_parse_bool` | 23 + 20 | 已删 |
| `services/skill_registry.py` | 821 | `list_all_skills` | 29 | 已删 |
| `api/dashboard.py` | 137 | `_skill_summary` | 29 | 已删 |
| `store/sandbox_repo.py` | 179 | `list_active_by_thread` | 17 | 已删 |
| `store/mapping_repo.py` | 91 | `delete_mapping` | 12 | 已删 |
| `store/mysql_store.py` | 480 | `rename_project_folder` | 11 | 已删 |
| `config/skill_retrieval_config.py` | 296 | `skill_retrieval_mode` | 7 | 已删 |
| `config/constants.py` | 35 | `PROJECTS_DIR` | 1 | 已删 |
| `utils/codeup_utils.py` | 107 | `mask_codeup_token` | 19 | 已删 |

约 400 行。几处需要说明：

- **`agent/graph/state.py` 整个文件是孤儿**。没有任何模块 import 它，`AgentTaskState` 也从未被实例化或标注。
  唯一提到它的是 `agent/graph/__init__.py` 第 7 行的包说明注释。
- **`backends/local_shell.py` 的 `create_local_shell_backend`** 已经被 `get_local_shell_backend` 取代，
  工厂函数 `backends/__init__.py::create_backend` 现在只调后者。
- **`_parse_json` / `_parse_bool`** 只在 `README.md` 的结构树注释里被提到，代码里没有调用点。
  同文件的 `_to_iso` 仍在使用，别整文件删。
- **`api/dashboard.py::_skill_summary`** 只在 `services/skill_registry.py` 第 5 行的注释里被提及
  （注释说两处解析逻辑容易分叉），实际调用点已不存在。
- **`backends/permissions.py` 三个 `ensure_safe_*`** 是更底层的校验函数，现在实际生效的校验在
  `agent/middleware/tool_sanitize.py` 与 `backends/command_policy.py` 里，这三个已经没人调。
- **`agent/graph/graph.py::build_agent`** 是旧入口；`agent/runtime/planning_router.py` 与
  `task_router.py` 调的是同文件的 `build_agent_for_runtime`。删之前确认 `build_agent` 里没有
  只有它才做的初始化。
- **三处保留的理由**：`checkpoint_has_writes` 是 `ReliableRedisSaver` **覆写父类方法的形参**，
  签名必须一致，工具把它当成了没人用的函数；`local_shell.py::health` 删了会连带只被它调用的
  `_is_writable` 一起消失，而且它是后端自检入口（`python_venv` 这类问题靠它暴露）——
  属于「没接线但有用」，建议接上而不是删；`get_work_dir` 文档标注为 DeepAgents 协议方法，
  当前装的版本确实不调它，但删掉等于赌后续版本也不调，收益只有 7 行。

## 二、只在测试 / 脚本里出现，产品路径走不到（7 处）

这些删掉不会影响运行，但会让对应测试失去覆盖对象，需要一起处理。

| 文件 | 位置 | 符号 | 谁在用 |
|---|---|---|---|
| `config/automation_state.py` | 572 | `prune_orphan_states` | 只被 `scripts/prune_orphan_automation_states.py` 用 |
| `config/automation_state.py` | 709 | `record_attempt` | 只有测试 |
| `config/automation_state.py` | 788 | `set_cursor` | 只有测试 |
| `config/automation_state.py` | 851 | `mark_stopped` | 只有测试 |
| `services/automation_loop.py` | 186 | `as_dict` | 只有测试 |
| `services/automation_guard.py` | 111 | `in_memory` | 只有测试 |
| `services/sandbox_skill_sync.py` | 65 | `has_pending_refresh` | 只有测试 |

`prune_orphan_states` 这条要单独看：它对应的脚本 `scripts/prune_orphan_automation_states.py`
本身也没有任何地方调用，属于「一次性运维脚本」。脚本头部的说明写得很清楚（默认归档不删除、
`--delete` 才真删），**建议保留脚本 + 保留这个函数**，它是有意做成手动逃生口的。

其余 6 个是纯粹的测试缝：产品路径已经不走，测试靠它们断言中间状态。
要么删测试，要么留着——**不建议只删函数留下失败的测试**。

**处置：这 7 个全部保留**（含 `prune_orphan_states`）。见第七节。

---

## 三、前端没人用

### 3.1 整个文件没有任何地方 import（5 个，已全删）

| 文件 | 说明 |
|---|---|
| `frontend/src/components/agents/z-index.ts` | 导出的 `Z` 常量对象（FLOAT/OVERLAY/DROPDOWN/MODAL），全项目零引用 |
| `frontend/src/components/ui/badge.tsx` | shadcn 风格组件，`Badge` / `badgeVariants` 无人 import |
| `frontend/src/components/ui/combobox.tsx` | 同上，16 个导出全部无人用 |
| `frontend/src/components/ui/separator.tsx` | 同上 |
| `frontend/src/components/ui/input-group.tsx` | **删掉 `combobox.tsx` 之后才暴露出来的**：前者唯一的引用方就是后者 |

最后一条是这次排查里唯一一个「链式孤立」的例子 —— 删 A 之后 B 才变孤儿，所以删完必须
**重跑一遍扫描**，只扫一次是看不出来的。

（`components/agents/ported/index.ts` 看着像孤儿，实际是被
`import { MessageView } from "@/components/agents/ported"` 以目录形式引入的，属于误报。）

### 3.2 导出了但全项目没人用（8 个，已全删）

| 文件 | 符号 |
|---|---|
| `frontend/src/lib/profile.ts` | `buildProfileUpdate`、`useSaveProfile` |
| `frontend/src/lib/api.ts` | `isGithubReauthError`、`slackConnectUrl` |
| `frontend/src/lib/agents/api.ts` | `groupThreads` |
| `frontend/src/lib/agents/types.ts` | `ChunkKind`、`GitStatusEntry` |
| `frontend/src/components/AppShell.tsx` | `SettingsRow` |

删完这些之后还暴露出一批**悬空类型**（14 个），例如 `ReviewStyle`、`TeamSettings`、
`UsageLeaderboard*`、`ReviewerStats*`、`UserMappingsPage`、`TriggerMode`、`AutofixMode`、
`GitFileStatus` —— 它们是上面那批死方法的参数与返回值类型，方法没了，类型也就没人引用了。
已一并删除。

另外有 95 个导出属于「只在定义文件内部使用，export 是多余的」，例如
`lib/theme.ts` 的 `resolveTheme`、`lib/agents/queries.ts` 的 `agentScheduleKeys`。
这些**不是死代码**，只是把 `export` 去掉更干净。**没有动。**

### 3.3 API 客户端里 19 个方法没有任何页面调用

`frontend/src/lib/api.ts` 共 45 个方法，其中 19 个只有定义、没有调用方：

```
skillDetail          updateSkill          listReviewStyles     createReviewStyle
getReviewStyle       saveReviewStylePrompt analyzeReviewStyle  cancelReviewStyle
deleteReviewStyle    getTeamSettings      saveTeamSettings     listEnabledReviewRepos
setEnabledReviewRepo usageLeaderboard     myMapping            adminListUserMappings
adminDeleteUserMapping unbindProjectFolder updateProjectDisplayName
```

其中 `unbindProjectFolder` 和 `updateProjectDisplayName` 对应的后端路由**当时**是存在且能用的
（`DELETE /workspace/projects/{name}/unbind-folder`、`PATCH /workspace/projects/update-display-name`），
只是前端没有入口调它们——属于功能做了一半。**后来按第八节第 2 条连后端一起删了。**

**这 19 个方法已经全部删掉**，`lib/api.ts` 从 45 个方法降到 26 个。删的时候踩到一个坑：
判断方法有没有被调用**不能只用 `\.name(` 这个正则** —— `listProjects` 是以函数引用形式传的
（`queryFn: api.listProjects`，不带括号），用带括号的模式会把它误判成没人用。

---

## 四、前后端接口对不上（这是缺陷，不是死代码）

### 4.1 前端在调、后端没有这个路由 → 点下去就是 404

| 前端调用 | 出现位置 | 排查时后端 | 最终处置 |
|---|---|---|---|
| `POST /threads/{id}/cancel` | `lib/agents/api.ts:271` | **不存在**。`AgentThreadView.tsx:560` 的「停止」按钮走这里 | **已实现**（协作式停止），见第八节 |
| `/review-styles`、`/review-styles/{name}`、`/analyze`、`/cancel` | `lib/api.ts` | **不存在**（5 个方法，且无人调用） | 已删 |
| `/team-settings`、`/enabled-review-repos` | `lib/api.ts` | **不存在**（3 个方法，且无人调用） | 已删 |
| `/agent-usage-leaderboard` | `lib/api.ts:512` | **不存在**。`UsageLeaderboardPayload`、`ReviewerStatsPayload` 等类型在后端一个字段都找不到 | 已删 |
| `/admin/user-mappings`（列表 + 删除） | `lib/api.ts` | **不存在**（2 个方法，且无人调用） | 已删 |
| `/auth/logout`、`/auth/dev-login` | `lib/api.ts::loginUrl()` | **不存在** | 已删（连登录页一起），见第八节第 1 条 |

> 排查当时这一行还写了 `/slack/login`，**那是错的** —— 全仓没有这个字符串。
> Slack 只出现在两处：`ported/ReplyCard.tsx` 那套移植过来的消息渲染组件（`slack` 只是一种
> `toolKind` 的展示），以及 `GET /me` 里写死的 `slack_oauth_enabled: False`。
> 真正的登录入口只有一个：`lib/api.ts::loginUrl()` → `/dashboard/api/auth/dev-login`。

分三类看：

- **`/threads/{id}/cancel` 是真问题**（后来做成了功能）：界面上的按钮在调一个不存在的接口，
  点「停止」会失败。补充核实（2026-09-20）：

  | 问题 | 结论 |
  |---|---|
  | 按钮会出现吗 | **会**。`AgentThreadView.tsx:316` 的 `hasActiveRun` = `thread.status === "running" \|\| "interrupted"`，而 `status` 由 `latest_run_status` 映射（`utils/thread_utils.py:16`）：run 跑起来就是 `running`，等人工审批是 `awaiting_approval → interrupted`。两种真实场景都会亮出按钮。 |
  | 点下去会怎样 | 前端 `cancelThread.mutate()` **没有 `onError`**，404 被静默吞掉——按钮转一下，什么都不发生，也没有报错。 |
  | 后端有没有取消机制 | **完全没有**。`api/dashboard.py` 里没有任何 cancel/abort 路由；全仓没有运行中任务的注册表（`create_task` / `_RUNNING` 之类搜不到）。 |
  | 断开连接能停吗 | **不能**。Agent 跑在 `services/streaming_service.py:236` 的 `worker()` 线程里，SSE 生成器只是从队列取事件；客户端断开只会停掉生成器，worker 线程继续跑完。前端也没有 `AbortController`。 |

  所以「补后端路由」不是补一个转发，而是要**新建一套协作式停止**：注册 `thread_id → 停止标志`、
  在事件回调里检查并中断 LangGraph 的迭代、并保证 checkpoint 与 run 记录不残留。
  **已经按这个做法实现了**，做法与语义见第八节。
- **`/review-styles`、`/team-settings`、`/enabled-review-repos`、`/agent-usage-leaderboard`、
  `/admin/user-mappings` 是一整块没落地的功能**：类型定义、请求方法、页面入口都没有，
  只留下 API 客户端里的空壳。看起来是从别的项目搬过来的（前端有 `components/agents/ported/` 目录）。
  **空壳已全部删除。**
- **`/auth/*` 已按「本地部署不做认证」收掉**：删了登录页与 `loginUrl()`。但 `GET /me` 与
  `useSession()` **保留** —— `AppShell` 要用它拿 `user` 显示登录名，五个页面用它做数据守卫。

### 4.2 后端注册了、前端不调

三条，**已全部删除**：

| 接口 | 前端对应方法 | 处置 |
|---|---|---|
| `GET /dashboard/api/my-mapping`（`api/dashboard.py:314`） | `lib/api.ts::myMapping()`，无人调用 | **已删**（路由返回写死的 `{}`，是占位不是实现） |
| `DELETE /workspace/projects/{project_name}/unbind-folder` | `lib/api.ts::unbindProjectFolder()`，无人调用 | **已删**，见第八节第 2 条 |
| `PATCH /workspace/projects/update-display-name` | `lib/api.ts::updateProjectDisplayName()`，无人调用 | **已删**，见第八节第 2 条 |

后两条第一版漏在 4.2 之外了（它们只在第八节按「待决策」写了一次），补在这里。

`GET /health` 前端也不调，但它是给运维/探活用的，属于正常。
`GET /repos`（同样返回写死的空列表）**保留** —— `lib/profile.ts:30` 在调 `api.repos()`。
同样是占位接口，一个有人调一个没人调，处置就不同：**判断「死不死」看的是调用方，不是看它像不像桩。**

---

## 五、依赖与打包声明

### 5.1 后端声明了但没 import 的依赖

| 依赖 | 情况 | 处置 |
|---|---|---|
| `structlog` | `pyproject.toml` 里注释写着「可选」，但全项目零 import | 已删 |
| `aiofiles` | 零 import | 已删 |
| `opensandbox-code-interpreter` | 零 import | **保留** —— 沙箱运行时插件，代码里搜不到 import 不代表不需要 |

### 5.2 打包配置对不上实际目录结构

```toml
[project.scripts]
xihe-agent-start = "xihe.scripts.start_all:main"
xihe-agent-stop  = "xihe.scripts.stop_all:main"

[tool.setuptools.packages.find]
include = ["xihe*"]
```

仓库里**没有 `xihe` 包**，顶层包是 `agent` / `api` / `backends` / … 直接放在仓库根。
后果有两个：`xihe-agent-start` 这条命令装上就报 `ModuleNotFoundError`；
`packages.find include = ["xihe*"]` 一个包都找不到，`pip install .` 装出来是个空壳。
`scripts/start_all.py` 和 `stop_all.py` 本身是能用的（手工 `python scripts/start_all.py`），
问题只在这两行声明。

### 5.3 前端声明了但没引用的依赖

`frontend/package.json` 共 48 个依赖，以下 12 个在 `src/`、`vite.config.ts`、`vitest.config.ts`、
`index.html` 里都搜不到引用：

```
remark-breaks        zustand                web-vitals           workbox-window
playwright-core      prettier               @tanstack/router-plugin
@tanstack/react-devtools        @tanstack/react-query-devtools
@tanstack/react-router-devtools @tanstack/react-router-ssr-query
```

（`@types/node`、`@types/react`、`@types/react-dom` 虽然搜不到引用，但 `tsc` 需要，不算未使用。
`prettier` 若只作命令行使用也算正常，看你的习惯。）

**处置**：`remark-breaks`、`zustand`、`web-vitals`、`playwright-core` 四个已删。
其余八个**保留**，理由是它们分别是 tanstack-start 构建链、PWA（`client.tsx` 用了
`virtual:pwa-register`）与 SSR 集成的运行依赖 —— 搜不到 import 是因为它们通过 vite 插件与
虚拟模块工作，**没有实测就不动**。

---

## 六、建议顺手清但不紧急

- **shadcn 生成的组件里没人用的子组件**：`ui/select.tsx`（`SelectGroup`/`SelectLabel`/
  `SelectScrollUpButton`/`SelectScrollDownButton`/`SelectSeparator`/`SelectValue`）、
  `ui/card.tsx`（`CardAction`/`CardFooter`）、`ui/avatar.tsx`（3 个）。
  这类是生成器整包给的，删了下次生成还会回来，**我建议留着**。
  （`ui/input-group.tsx` 当时也在这张单子里，但它整个文件已经删了 —— 它是
  `combobox.tsx` 的唯一引用方，跟着一起没的。）
- **本地垃圾目录**：`.impeccable/`（一份 Edge 浏览器的文件拷贝）、`.artifacts/`、`.pytest_tmp/`、
  `.pytest_tmp3/`。都已经在 `.gitignore` 里，没进版本库，只是占本地磁盘，可以手工删。
- **`frontend/src/api/` 是空目录**。

---

## 七、建议不做的事

- **不要为了「清干净」去动测试里的桩和夹具**。第二节那 6 个只在测试里用的方法，
  删函数就必须同时删测试，净收益是负的，除非你本来就打算放弃那块覆盖。
- **不要删 shadcn 的生成组件**（见上）。
- **不要删 `/auth/*` 之外还留着的东西**——`GET /me` 与 `useSession()` 不是认证残留，
  删了页面会白屏（`AppShell` 靠它拿登录名，五个页面靠它做数据守卫）。
- **`scripts/prune_orphan_automation_states.py` 保留**。它是有意做成手动逃生口的运维脚本，
  不是漏掉的调用方。

---

## 八、决策与处置

这一节按「重量」分档，别并排看：协作式停止是一整块新功能，下面三条是收尾，最后一条是建议不做。

### 已做：协作式停止

「停止」按钮那条已经从缺陷变成能用的功能（你选了「真做协作式停止」）。做法：

| 环节 | 落点 | 说明 |
|---|---|---|
| 登记表 | `agent/runtime/cancellation.py`（新文件） | `thread_id → 嵌套深度` + `thread_id → 已请求停止`。模块级不依赖任何项目内模块，因为 `api` / `services` / `agent` 三层都要用它。 |
| 登记运行态 | `services/streaming_service.py` 的 `worker()`、`agent/runtime/streaming_runtime.py` | worker 一进线程就登记（覆盖准备仓库那段），Agent 运行层再登记一次。**登记可重入**：只有最外层会清残留标志，否则内层会把外层刚收到的停止请求抹掉。 |
| 检查点 | `streaming_runtime.py::_consume_raw_event_stream` 的事件循环首行 | `raise_if_cancelled(thread_id)`。放在这里而不是只在 `event_sink` 里，是因为工具事件、子 Agent 事件不一定都走 `event_sink`，而这个循环是所有协议事件的必经之处。 |
| 中断信号 | `AgentRunCancelledError` | 各 router（`task_router` 两处、`planning_router` 一处）在 `except Exception` **之前**单独接住，落成 `cancelled` 并返回 `{"status": "cancelled"}`。 |
| 收尾 | `cancellation.py::mark_run_cancelled` | thread 与 run 两处都写终态 + 一条「任务已停止」展示事件。整体容错：收尾失败只记日志。 |
| 接口 | `POST /dashboard/api/threads/{thread_id}/cancel` | 会话不存在 404；当前没有在跑的轮次 409；受理后回会话摘要。 |

**停止的语义**：不再继续下一步，**不回滚**。当前正在生成的那段模型输出会丢，已经写进
checkpoint 的内容保留。检查发生在「两个协议事件之间」，所以一次长工具调用会先跑完。

**为什么不能更狠**：Agent 跑在 `worker()` 线程里，Python 线程无法安全强杀；强行终止会让
checkpoint 与 runs 表留下一个永远 `running` 的记录。

**顺带确认的两件事**：① 被停止的一轮在自动化里映射成 `blocked`（`_outcome_from_status`），
**不算失败**，不会把 `maxConsecutiveFailures` 顶上去；② `status_for_frontend("cancelled")`
落到 `idle`，前端 `hasActiveRun` 转假，停止按钮会消失。

**那个「准备仓库」窗口已经补上（2026-09-20）**：原来自动化任务在准备仓库阶段点停止会拿到
409 —— 那个阶段还没进 `run_agent_with_event_stream`，登记表里没有它。现在
`services/automation_runner.py::make_agent_round_runner` 拆成两层：外层 `run()` 先生成会话 id、
`begin_run` 登记、`finally: end_run` 收尾；内层 `_run_round()` 是原来的主体（多了个
`current_thread` 参数）。这样「准备仓库」这段（clone 可能要几十秒）也落在登记范围内，
而内层 Agent 运行层再登记一次不会互相踩 —— **可重入本来就是为这种分层设计的**，
SSE worker 走的是同一个形态。

**残留的一小段**：如果这一轮走的是「工作区查看」/「仓库同步」这类**不进 Agent 循环**的出口
（`run_agent_task` 里那两个提前 return），停止请求会被受理但没人消费，收尾时由 `end_run`
清掉 —— 用户看到的是「受理了但没停」。这一段很短（不调模型、没有长工具调用），
而且**交互路径早就是这个形态**，不是这次新引入的。

**测试**：`tests/test_run_cancellation.py`，17 条，覆盖登记表的可重入与残留清理、收尾的
两处写入、接口的 404/409/受理、路由路径拼装、以及「cancelled 不算失败」这条判定。
`tests/test_automation_runner.py` 另加 2 条：一轮在 Agent 启动**之前**就已经登记、
一轮跑完不留残留标志。**两条都做过负向对照** —— 把 `begin_run` 摘掉后，
`test_the_round_is_registered_before_the_agent_starts` 报 `assert False is True`。
不覆盖「真跑一轮再停」——那需要真模型与真容器。

### 已处置（3 条，按你的决定）

1. **登录入口 —— 已删。** 你说「不需要登录入口」，所以按「本地部署不做认证」来收：
   - 删 `frontend/src/routes/login.tsx` 整个文件（原来上面只有一个「使用控制台账号继续」按钮）
   - 删 `lib/api.ts` 的 `loginUrl()`，以及 `SessionUser.slack_oauth_enabled`
   - `routeTree.gen.ts` 由 vite 插件重新生成（净删 21 行）
   - `agents.tsx` / `automations.tsx` / `my-settings.tsx` / `skills.tsx` / `agents/$threadId.tsx`
     五个页面里的 `if (!session.data) return <Navigate to="/login" />` 换成一段静态提示
     （`读取会话信息失败，请刷新页面重试。`），并清掉随之变成未使用的 `Navigate` 导入
   - 后端：`dashboard_me()` 返回值删掉 `"slack_oauth_enabled": False`，`schemas/user_schema.py`
     的 `UserInfoResponse` 删掉同名字段（该 schema 全仓没有使用方）

   **保留的是 `GET /me` 与 `useSession()`**：`AppShell` 要用它拿 `user` 显示登录名，
   五个页面用它做数据守卫。删掉的只是「去网关登录」这个动作，不是这个数据接口。

2. **解绑文件夹 / 改项目显示名 —— 已删。** 前端零引用，按「连后端一起删」处理：
   - `api/dashboard.py`：`DELETE /workspace/projects/{project_name}/unbind-folder`、
     `PATCH /workspace/projects/update-display-name` 两条路由，以及
     `DashboardProjectUpdateDisplayNameRequest` 的导入
   - `schemas/request_schema.py`：`DashboardProjectUpdateDisplayNameRequest`
   - `frontend/src/lib/api.ts`：`unbindProjectFolder` / `updateProjectDisplayName`
   - `store/mysql_store.py`：`delete_project_folder` / `update_project_display_name`
   - `store/project_folder_repo.py`：`delete` / `update_display_name`（删掉上面两个 store
     方法后成了链式孤儿）

   **没有删的是 `project_local_folders.display_name` 这一列**：它由 `upsert_project_folder`
   写入，属于 `renameProject` 那条链，不是这次要收的东西。删的只是「单独改显示名」这个入口。

3. **`plan_source_prompt` 跨轮累积 —— 已修。** 详见下节。

#### 第 3 条：原来断在哪，怎么修的

「跨轮累积原始需求」这套机制原来有**三个断口**，问题是方案正文由模型直接产出，
**身上根本没有 metadata**，所以三层里没有一层能把它带出来：

| # | 位置 | 原状 |
|---|---|---|
| 1 | `agent/graph/checkpoint_history.py:50` `visible_checkpoint_messages` | 只提取 `id` / `role` / `content`，不读也不带 metadata |
| 2 | `agent/runtime/_helpers.py:68` `_get_visible_messages` | 再规整成 `message_id` / `author` / `content`，metadata 又丢一次 |
| 3 | `agent/runtime/_helpers.py` `latest_confirmable_plan_message` | 自己**造**一份 metadata，只有 `task_kind` / `awaiting_confirmation` / `source`，没有 `source_prompt` |

结果 `plan_source_prompt()` 永远读不到 `source_prompt`，只能回退到
`latest_non_approval_user_prompt()`。**第一轮修订时看不出问题**——那时"最近一条非确认用户消息"
恰好就是原始需求；从第二轮修订开始，回退到的是上一轮的「再改一下，加上 CSV」，最早的需求被覆盖。

**修法：不再指望模型产出的消息带 metadata，改成把原始需求存在会话上。**

- `store/thread_repo.py`：`threads` 表加 `plan_source_prompt TEXT` 列，建表语句与
  `ALTER TABLE` 迁移都补上；`upsert_thread` 加同名参数，`ON DUPLICATE KEY` 用
  `COALESCE` 语义 —— 不传就保留，传了就覆盖（新方案周期自然重置）
- `store/mysql_store.py`：同名参数透传
- `agent/runtime/planning_router.py`：把 `plan_source`（修订时沿用上一版记录的需求，首轮就是
  用户这一句）一次算好，同时用于写 thread 和拼给模型的内容，两处不再各算一遍
- `agent/runtime/_helpers.py`：`latest_confirmable_plan_message` 读到方案后，从 thread 补上
  `metadata.source_prompt`。这样**两条读取路径同时生效** ——
  `planning_router` 的 `plan_source_prompt()` 和 `task_router.py:343` 的
  `metadata.get("source_prompt")`（"确认实施"时还原完整需求）
- `_thread_plan_source_prompt()` 读库失败只记 warning 返回空串，不让它把方案确认整条链路带崩

**测试**：`tests/test_plan_source_accumulation.py`，6 条。覆盖首轮记录、修订沿用、读取端补 metadata、
老会话无此列时回退、读库失败不崩、方案消息为 `None` 时的回退。
**做了负向对照**：把读取端的注入临时摘掉后，
`test_the_read_path_fills_in_the_missing_metadata` 报
`'再改一下，加上 CSV' == '帮我加个导出功能'` 失败 —— 正是原 bug 的症状，说明这测试抓得住它。
**没覆盖**：真跑一轮两轮修订再对比（需要真模型）。

### 建议不做（1 条）

4. **不动测试里的桩与夹具**：第二节那 6 个只在测试里用的方法，删函数必须同时删测试，净收益为负。

---

## 九、怎么复现这份结论

- vulture 不在项目 venv 里，装在隔离环境：
  `C:\Users\Apolleo\.workbuddy-ai\binaries\python\envs\default\Scripts\vulture.exe`
- 扫描脚本在 `D:\tmp\xihe-deadcode\`：`analyze.py`（vulture 两遍 + 装饰器剥离）、
  `route_coverage.py` / `path_audit.py`（前后端接口双向差集）、
  `scan_frontend.py`（前端 import 图与导出引用计数）。
- 本报告里每条结论都做过 grep 复核，不是工具的原始输出。

**注意**：这类排查会把原本就存在、只是没人看过的缺口一并翻出来（比如
`ReviewerStatsPayload` 这种「前端有类型、后端没字段」的悬空契约）。
它们是**被发现的**，不是这次新产生的。
