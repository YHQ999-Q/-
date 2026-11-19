# METAMIND 演示课程（阶段 0.5）

本目录是 METAMIND Evolution Engine 的**演示数据层**，只负责把「自进化 Agent」这门演示课
真实写进 OpenTutor 的 SQLite 数据库，供后续模块（Learner Model / Evaluator /
Reflection / Skill Memory / Tutor Integration）读取。**不包含任何产品逻辑代码。**

## 目录内容

| 文件 | 作用 |
|---|---|
| `demo_spec.json` | 唯一内容源：课程、4 个内容节点、3 个概念、3 条概念关系、3 个学习目标、两套平行测试题（A/B）、开放题与评分要点 |
| `seed_demo_course.py` | 幂等写入 / 只读校验 / 重置学习者状态 / 清除演示数据 |
| `check_spec_parity.py` | 机器校验两套题是否结构平行（题型、难度层、选项数、正确项数、目标、维度） |
| `verify_learner_state.py` | 模块 1 场景回归：空态 → 对/错答 → 快照 → 重置（34 项断言） |
| `check_mastered_band.py` | 验证 `mastered` 档真实可达（连对 4 题） |
| `capture_state_sample.py` | 抓一份真实 API 响应样本做文档留证 |
| `test_evaluator_unit.py` | 模块 2 单元测试（无网络/无模型）：JSON、证据、守卫、判定规则（37 项） |
| `verify_evaluator.py` | 模块 2 场景回归（真实 API + 模型）：六类场景 + 不变量（97 项） |
| `check_gap_namespace.py` | 验证 `mm:` 快照标签不影响速度估计与进度统计（6 项） |
| `capture_eval_sample.py` | 抓评价响应与 rubric 样本 |
| `test_reflection_unit.py` | 模块 3 单元测试（无网络）：门控、校验、重试、脏数据（33 项） |
| `verify_reflection.py` | 模块 3 场景回归（真实 API + 模型）：C1–C5 |
| `capture_reflection_sample.py` | 抓三种状态的反思样本 |
| `debug_reflection.py` | 单次反思的调试脚本（打印失败细节） |
| `verify_skill_memory.py` | 模块 4 验收（真实 API + 模型）：导入幂等/建技能/V1→V2/差异/重启/选择/回落/停用/非法配置/调用追踪/失败不覆盖/1–3 回归（55 项） |
| `capture_skill_sample.py` | 抓技能、版本、diff、Selector、Invocation 真实样本 |
| `reset_skill_library.py` | 把技能库恢复成 5 个 seed 技能 V1，清空 invocations（演示前必跑） |
| `debug_evolution.py` | 单跑 evaluate → reflect → evolve，调试进化链路 |

## 演示数据标识

所有本脚本创建的行都带标记，绝不会和真实学习数据混淆：

- `courses.metadata.metamind_demo = true`，`metadata.demo_key = "metamind_self_evolution_v1"`
- `course_content_tree.metadata` / `knowledge_nodes.metadata` / `study_goals.metadata_json` 同上
- `practice_problems.problem_metadata` 同上，并额外写 `test_set`（A/B）、`goal`、`eval_dimension`
- 所有 id 由 `uuid5(固定命名空间, demo_key + 角色 + slug)` 生成 → **重复执行不会产生重复行**

原有课程「自进化」（`6bd127a2-…`）与用户数据**没有被修改**。新课程独立存在，名称带
「METAMIND 演示」前缀。

## 课程结构

```
课程：METAMIND 演示｜什么是真正的自进化 Agent（标准化测试课）
└── 课程导览：这门课在测什么                (level 0, 说明与评价方式)
    ├── 概念一：自进化 Agent 的定义与四个必要条件      → 目标一
    ├── 概念二：记忆、即时适应与持续性策略进化的三层边界 → 目标二
    └── 概念三：判定新案例的四问清单与证据要求          → 目标三
```

概念关系：`概念一 --prerequisite--> 概念二 --prerequisite--> 概念三`，另有
`概念一 --confused_with--> 概念二`（用于误差诊断与薄弱点提示）。

## 测试题设计

两套题结构完全相同（由 `check_spec_parity.py` 强制校验）：

| 配对 | 题型 | 难度层 | 选项 | 正确项 | 目标 | 评价维度 |
|---|---|---|---|---|---|---|
| a1 / b1 | mc | 1 | 4 | 1 | G1 | understanding |
| a2 / b2 | select_all | 2 | 5 | 2 | G2 | understanding |
| a3 / b3 | mc | 2 | 4 | 1 | G2 | understanding |
| a4 / b4 | select_all | 3 | 5 | 2 | G3 | transfer |

- A 集用于第一次教学（策略 V1），B 集用于第二次教学（策略 V2）。**两次运行的表面情境不同，
  结构和难度相同**，避免第二次因为"刚学过"而天然占优。
- 全部为 OpenTutor 原生可确定性判分的题型（`mc` / `select_all`），参考答由
  `services/practice/answers.py` 判分，种子脚本会自检"参考答案判自己为正确"。
- 开放题（`open_ended`）**不写入 `practice_problems`**：它们无法确定性判分，属于模块 2
  Evaluator 的 rubric 评价对象，题目与评分要点保存在 `demo_spec.json` 里，避免污染练习/掌握度数据。

## 使用

在 `apps/api` 下用应用自带解释器执行（不需要激活虚拟环境）：

```bash
# 幂等写入（默认动作），写完自动跑一次校验
.venv-win/Scripts/python.exe scripts/metamind_demo/seed_demo_course.py --seed

# 只读校验：课程、内容、概念、目标、题目、确定性判分、检索召回
.venv-win/Scripts/python.exe scripts/metamind_demo/seed_demo_course.py --verify

# 结构平行性校验
.venv-win/Scripts/python.exe scripts/metamind_demo/check_spec_parity.py

# 重置演示范围的学习者状态（Run01 → Run02 之间必须执行，保证起点相同）
.venv-win/Scripts/python.exe scripts/metamind_demo/seed_demo_course.py --reset-learner --yes

# 清除全部演示数据（只删带 metamind_demo 标记的课程）
.venv-win/Scripts/python.exe scripts/metamind_demo/seed_demo_course.py --purge --yes
```

`--dry-run` 会执行全部写入再回滚，用于演练；`--reset-learner` / `--purge` 必须显式加 `--yes`。

### `--reset-learner` 到底重置了什么

只针对**这门演示课 + 本机用户**，依次清理：

| 表 | 为什么必须清 |
|---|---|
| `practice_results` | 上次的答题记录，否则掌握度有残留 |
| `wrong_answers` | 上次的错题与错误归类 |
| `concept_mastery` | BKT/概念掌握度 |
| `learning_progress` | 课程进度与 FSRS 复习参数 |
| `mastery_snapshots` | 掌握度时间序列（评价前后对比要干净重算） |
| `intervention_outcomes` | 干预效果记录 |
| 由错答自动派生的诊断题（`is_diagnostic=1`） | OpenTutor 提交错答时会调 LLM 派生诊断题，属于演示范围 |
| `chat_sessions` / `chat_message_logs` | 上次对话，否则第二次会带着历史 |
| `agent_kv`（socratic 状态 / tutor_notes / teaching_strategies） | 隐藏的教学状态，会直接改变下一次教学行为 |
| `agent_tasks` / `conversation_memories` / `usage_events` | 后台任务、记忆、用量 |

**不会**清理：演示内容本身（课程、内容节点、概念、目标、题目），以及任何其他课程的数据。

## 回滚方式（三道保险）

1. **内容级**：`--purge --yes` 删掉全部演示行，其余数据不动。已实测：purge 后重新 `--seed`
   会恢复到完全相同的 id（uuid5 确定性），原课程「自进化」始终存在。
2. **数据库级**：seed 之前的完整快照在
   `D:\Hermes\archive\metamind-20261114\opentutor-pre-seed.db`（SQLite backup API 生成，
   非文件拷贝，处于一致状态）。恢复步骤：停服务 → 用该文件覆盖
   `native-data/data/opentutor.db` → 删除同目录 `-wal` / `-shm` → 重启。
3. **代码级**：本次改动全部是新增文件（`apps/api/scripts/metamind_demo/`），
   `git revert` 或直接删除该目录即可，不影响 OpenTutor 运行时。

## 注意

- 本课程是**标准化模拟测试数据**，不是真实学生实验；路演时必须这样表述。
- 提交错答会额外触发 OpenTutor 的「自动派生诊断题」，属于原生行为，非本脚本产生。
- 脚本只读取 `native-data/settings.env` 到进程环境，不打印、不写日志、不提交任何密钥。
