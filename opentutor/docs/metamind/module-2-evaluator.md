# METAMIND 模块 2：Evaluator（学习效果评价）

> 目标：用学生真实作答判断「学习目标是否达成」，为 Reflection 提供可审查证据。
>
> 代码：`apps/api/services/evolution/evaluator.py`、`eval_rubric.py`
> 接口：`POST /api/teaching/evaluate`、`GET /api/teaching/evaluations[/{id}]`、`GET /api/teaching/rubric`
> 表：`teaching_evaluations`

## 1. 三条不可妥协的规则

1. **确定性优先。** 客观题只读 `practice_results.is_correct`（由 `services/practice/answers.py` 判分），模型不参与重新判分。
2. **没有逐字证据就不给分。** 开放题任何 `score >= 1` 必须附至少一条**学生原话的逐字片段**；定位不到就判 `evidence_not_verbatim`，该维度变成 `not_measured`，不会退化成数字。
3. **未测量 ≠ 0 分。** 未测维度从数据库到 API 全程 `NULL`，判定时被跳过而不是当零分惩罚。

另外两条教育学约束在代码里显式实现：

- 学生只说「我懂了 / 明白了」→ 确定性守卫直接判 0（`bare_assertion_detected`），**不调用模型**。
- BKT / `concept_mastery` 只作为 `learner_context` 透出（`used_for_scoring: false`），不参与任何分数。
- 单次评价不声明 Retention：`scores.retention` 恒为 `null`，并在 `rationale.deferred` 里写明需要延迟测验。
- 每次评价都写 `rationale.comparison_warning`：跨 Run 的分数差异不能直接当作教学策略有效的证据。

## 2. 固定 Rubric（`metamind-eval-v1`）

量表：0–2 序数，三个维度。改任何一个字都必须提升 `RUBRIC_VERSION`。

### 维度一 Conceptual Understanding（能否准确解释概念）

| 分 | 标签 | 行为表现 |
|---|---|---|
| 0 | 未体现理解 | 没有给出定义或关键构成；或只重复题面用词；或把两个不同概念混为一谈（例如把「记住偏好」直接说成「自进化」）并给出错误理由。 |
| 1 | 部分理解 | 说出了定义中的一部分（例如只说「会改变自己」），但缺少必要条件中的至少两个（经验积累 / 反思诊断 / 策略持续改变 / 结果验证），或方向正确但表述含糊、无法据以判断。 |
| 2 | 准确理解 | 用学生自己的话准确解释定义，并至少指出三个必要条件；能说明「改变的是以后怎么做，而不是知道什么」这一关键区分。 |

必须能指出的证据：学生原话中解释定义与必要条件的片段（逐字）。

### 维度二 Independent Application（无提示时能否独立运用）

| 分 | 标签 | 行为表现 |
|---|---|---|
| 0 | 未独立完成 | 客观题判分错误；或开放作答明显依赖提示、重复题干、没有给出自己的判断。 |
| 1 | 部分独立 | 客观题判分正确但只覆盖部分目标；或开放作答给出判断但理由不完整（有结论、无依据，或依据与结论不对应）。 |
| 2 | 独立准确 | 客观题在无提示条件下全部判分正确，且开放作答给出判断并附与结论一致的依据。 |

必须能指出的证据：客观题判分结果（题目 id + 是否判对），或开放作答中的判断与依据。
**在本模块的实现里，只要提交了客观题就只走确定性判分**（全部对=2 / 部分对=1 / 全错=0），不叠加模型判断。

### 维度三 Transfer（能否迁移到新情境）

| 分 | 标签 | 行为表现 |
|---|---|---|
| 0 | 未迁移 | 面对新案例给出的判断与正确判断相反；或直接照搬原案例的措辞而没有处理新信息。 |
| 1 | 部分迁移 | 对新案例的判断方向正确，但依据不完整，或漏掉案例中给出的关键条件（例如只说「有策略」而没检查是否被调用与验证）。 |
| 2 | 成功迁移 | 对新案例判断正确，并明确引用案例中的具体条件作为依据；能指出案例缺少哪一条证据链（诊断 / 策略文本 / 持久化版本 / 调用与验证）。 |

必须能指出的证据：学生对新案例的判断原文，以及引用的案例条件（逐字）。

### 总体判定（规则，不由模型决定）

| 条件 | verdict |
|---|---|
| 没有任何维度测得 | `insufficient_evidence` |
| 所有测得维度都为 0 | `not_achieved` |
| 三个维度都测得且都为 2 | `achieved` |
| 其余（含只测得部分维度） | `partially_achieved` |

`achieved` 要求**满覆盖**：只测得 2 个维度就给「已达成」会夸大结论，所以只测得部分维度、即使都是 2 分也只能是 `partially_achieved`。

## 3. 评分流水线

```
① 守卫：回答为空且「被要求作答」→ 0 分（empty_answer_submitted，不调模型）
        纯断言「我懂了 / 不知道」→ 0 分（deterministic_guard，不调模型）
        该维度根本没被评价（未传该维度输入）→ not_measured，不当作 0
② 客观题：practice_results.is_correct 汇总（确定性）
③ 开放题：LLM × rubric 锚点 → Pydantic 校验 → 方向一致性检查 → 逐字证据校验
        证据定位失败 / 结构不符 / 分数与理由冲突 → 1 次纠正重试
        仍失败 → 该维度 not_measured + error_code 落库
④ 判定：compute_verdict（纯规则）
⑤ 落库：teaching_evaluations（含 inputs/hash/原始模型输出/错误码）
```

**方向优先于依据**：如果模型的 `reason` 自己承认「判断方向相反」，却给了 ≥1 分，
该结果被拒并记 `score_reason_direction_conflict`，走重试；rubric 里方向相反应为 0 分。
（这是模块 3 交付前的一致性修复，因为旧实现允许「方向错但理由长」拿到 1 分。）

失败码（写入 `error_code`，同时进 `error_message`）：

`llm_unavailable` / `llm_error` / `invalid_json` / `schema_mismatch` / `evidence_not_verbatim` /
`no_evidence` / `invalid_score` / `rubric_version_mismatch` / `score_reason_direction_conflict`

`status` 取值：

- `completed`：三个维度全部测得，且无错误
- `partial`：有维度测得但覆盖不全，或部分维度失败
- `failed`：一个维度都没测到（例如 rubric 版本不符、全部维度报错）

## 4. 数据库结构（`teaching_evaluations`）

本地 SQLite 由 `services/sqlite_migrations.upgrade_sqlite()` 的 `create_all` 建表；Alembic 侧为
`alembic/versions/20261114_0027_teaching_evaluations.py`（down_revision `20261028_0026`）。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(36) PK | |
| `user_id` | VARCHAR(36) FK users.id CASCADE | student_id |
| `course_id` | VARCHAR(36) FK courses.id CASCADE | |
| `session_id` | VARCHAR(64) null | 会话 id 或演示 run 标签（`run01`） |
| `objective_slug` | VARCHAR(64) null | 学习目标键，如 `g1` |
| `objective_id` | VARCHAR(36) null | `knowledge_nodes.id` |
| `objective_text` | TEXT null | 目标描述快照 |
| `rubric_version` | VARCHAR(32) NOT NULL | 使用的 rubric 版本 |
| `strategy_version` | VARCHAR(64) null | 本次教学使用的策略/Skill 版本 |
| `status` | VARCHAR(20) NOT NULL | completed / partial / failed |
| `verdict` | VARCHAR(32) null | 规则判定的总体结果 |
| `understanding_score` | INTEGER null | **NULL = 未测量** |
| `application_score` | INTEGER null | 同上 |
| `transfer_score` | INTEGER null | 同上 |
| `dimensions_measured` | INTEGER NOT NULL | 测得维度数 |
| `evidence_json` | JSON null | 各维度证据（含逐字引文、题号、practice_result id） |
| `rationale_json` | JSON null | 各维度理由、判定理由、延迟维度说明、对比警告 |
| `inputs_json` | JSON null | 完整输入快照（题目、答案、run 标签、策略版本） |
| `content_hash` | VARCHAR(64) null | 输入归一化后的 sha256 |
| `raw_llm_response` | TEXT null | 原始模型输出（含失败与重试记录） |
| `error_code` / `error_message` | VARCHAR(50) / TEXT | 失败原因 |
| `model_provider` / `model_name` | VARCHAR(64) / VARCHAR(128) | 实际使用的模型 |
| `created_at` | DATETIME NOT NULL | |

索引：`(user_id, course_id, created_at)`、`(user_id, course_id, objective_slug)`。

> 注意：`strategy_version` 由后续模块（Skill Memory / Tutor Integration）写入；本模块只接收并透传。

## 5. API

### `POST /api/teaching/evaluate`

请求（所有字段除 `course_id` 外可选，缺什么就少测一个维度）：

```json
{
  "course_id": "51f5b4ce-…",
  "objective_slug": "g1",
  "session_id": "sample_good",
  "strategy_version": "metamind.teaching-strategy.v1",
  "problem_ids": ["3f194ba4-…", "99796a66-…"],
  "open_answer": "学生原话…",
  "open_question": "题目（可选，用于给模型上下文）",
  "transfer_case": "新案例文本（可选）",
  "transfer_answer": "学生对新案例的判断（可选）",
  "rubric_version": "metamind-eval-v1（可选；不符则 failed）"
}
```

真实响应（`D:\Hermes\archive\metamind-20261114\evaluation-good.json`）：

```json
{
  "evaluation_id": "430922e3-74bd-450f-831d-10c1915bc7ad",
  "objective_slug": "g1",
  "rubric_version": "metamind-eval-v1",
  "status": "partial",
  "verdict": "partially_achieved",
  "scores": {"understanding": 2, "application": 2, "transfer": null, "retention": null},
  "dimensions_measured": 2,
  "unmeasured_dimensions": ["transfer"],
  "evidence": {
    "dimensions": {
      "application": {"instrument": "objective_items", "attempts": 2, "correct": 2,
        "accuracy": 1.0, "graded_by": "services.practice.answers (deterministic)"},
      "understanding": {"instrument": "open_answer", "score": 2,
        "evidence_quotes": [
          "自进化指一个 Agent 能从自己的交互经历中提取经验，并把经验转化为对自身策略的持续更新。",
          "它和长期记忆的区别在于，记忆改变的是它知道什么，自进化改变的是它以后怎么教。"
        ],
        "graded_by": "llm_rubric_with_verbatim_evidence_check"}
    },
    "verified_quotes_only": true,
    "learner_context": {"concept_mastery": 0.1032, "used_for_scoring": false}
  },
  "rationale": {
    "verdict_reason": "all measured dimensions scored 2 but only 2/3 were measured (missing: ['transfer'])",
    "deferred": {"retention": "本次不评价 Retention（延迟保持）…"},
    "comparison_warning": "这是单次评价结果。跨 Run 的分数差异不能直接作为教学策略有效的证据…"
  },
  "content_hash": "…",
  "model": {"provider": "OpenAIClient", "name": "deepseek-chat"}
}
```

### 其它端点

- `GET /api/teaching/evaluations?course_id=…&objective_slug=g1&session_id=run01&limit=20`
- `GET /api/teaching/evaluations/{evaluation_id}`
- `GET /api/teaching/rubric`（机器可读 rubric + 判定规则，用于前端与评测对照）

## 6. 已知边界

1. **单次评价不测保持。** `retention` 恒 `null`；需要延迟复测才能谈保持。
2. **开放题评分仍依赖模型语义判断。** 我们只能保证「证据逐字存在」「结构与档位合法」「同答案重复评价一致（实测 2/2 相同）」，不能保证模型对边界答案的人类等价判断。这也是为什么每行都要存 `raw_llm_response` 与 `content_hash`。
3. **`achieved` 很严格**：必须三维全测且全 2。演示时若只测两维，结论会是 `partially_achieved`，这是有意设计，不要为了让面板好看而放宽。
4. **`session_id` 是自由文本**（会话 uuid 或 `run01`），不与 `chat_sessions` 建外键，避免演示流程依赖聊天持久化。
5. `invalid_score` 目前由 Pydantic（`ge=0, le=2`）覆盖，作为独立错误码保留给将来放宽为 0–3 量表的场景。

## 7. 验证

```bash
# 单元级（无网络、无模型）：37 项
.venv-win/Scripts/python.exe scripts/metamind_demo/test_evaluator_unit.py

# 场景级（真实 API + 真实模型）：97 项
.venv-win/Scripts/python.exe scripts/metamind_demo/verify_evaluator.py

# 'mm:' 快照标签隔离：6 项
.venv-win/Scripts/python.exe scripts/metamind_demo/check_gap_namespace.py

# 抓真实响应样本
.venv-win/Scripts/python.exe scripts/metamind_demo/capture_eval_sample.py
```

## 8. 回滚

- 代码：`git revert` 模块 2 的提交即可删除 `services/evolution/{eval_rubric,evaluator}.py`、
  `models/teaching_evaluation.py`、`alembic/versions/20261114_0027_*.py`，
  以及 `routers/teaching.py`、`schemas/teaching.py`、`models/__init__.py` 的新增片段。
- 数据：`DROP TABLE teaching_evaluations;`（或保留表、`DELETE FROM teaching_evaluations WHERE course_id = '<演示课>'`）。
  演示数据整体清理仍用 `seed_demo_course.py --purge --yes`；`--reset-learner` 已包含评价记录，
  保证 Run01/Run02 不串味。
- 注意：回滚本模块不会影响模块 1 的 learner-state 端点。
