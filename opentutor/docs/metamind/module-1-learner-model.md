# METAMIND 模块 1：Learner Model（学习者模型聚合层）

> 目标：给 Evolution Engine 一份**真实、可追溯**的学生状态，且不重造任何学习科学模型。
>
> 代码：`apps/api/services/evolution/learner_state.py`
> 接口：`GET /api/teaching/learner-state`、`POST /api/teaching/learner-state/snapshot`、`GET /api/teaching/learner-state/history`

## 1. 为什么需要这一层

OpenTutor 的学习状态分散在 6 张表、由 3 条独立写入链路维护：

| 表 | 谁写 | 语义 |
|---|---|---|
| `concept_mastery` | `services/loom_mastery.py`（BKT，题目类型感知 guess/slip） | 概念级掌握度，FIRe 会向先修概念传播 |
| `learning_progress` | `services/progress/tracker.py`（BKT 0.5 + 加权衰减 0.2 + 学习时长 0.3，FSRS 复习调度） | 内容节点级进度/掌握/复习 |
| `practice_results` | `routers/quiz_submission.py` | 每次作答的原始证据 |
| `wrong_answers` | 同上（`services/diagnosis/classifier.py` 归类） | 错题与错误类型 |
| `mastery_snapshots` | `tracker.update_quiz_result()` | 掌握度时间序列（按 content_node） |
| `study_goals` | 目标 API / seed | 学习目标 |

Evolution Engine 需要的是**一个**入口，而不是自己去 join 六张表、自己判断"这算不算学过"。
本模块只做聚合读取，不参与任何写入决策。

## 2. 三个状态，而不是一个分数

最关键的设计：**没有记录 ≠ 掌握度为 0**。

| 概念状态 | 判定条件（all from upstream signals） | `mastery_score` |
|---|---|---|
| `not_assessed` | 无直接练习证据（`concept_mastery.practice_count == 0` 且无 `practice_results`） | **`null`** |
| `assessed_below_mastery` | 有证据，但 `score < 0.8` 或 `direct_attempts < 3` | 实际分数 |
| `mastered` | `score >= 0.8` **且** `direct_attempts >= 3` | 实际分数 |

- 阈值 `0.8` 直接复用 `services/learning_science/velocity_tracker.MASTERY_THRESHOLD`，没有新造常数。
- `direct_attempts >= 3` 对齐 `tracker.update_quiz_result()` 里把 `status` 置为 `mastered` 的既有规则，两个视图不会互相矛盾。
- `state_reason` 会说明卡在哪一条：`no_practice_evidence` / `score_below_mastery_threshold` / `score_met_but_attempts_low` / `score_and_attempts_meet_mastery`。

课程级 `assessment_summary.state` 用另一套词表，避免误读：
`not_assessed`（一个都没评估）/ `partially_assessed`（部分评估或未全部掌握）/ `all_mastered`。
`overall_mastery` 在无任何评估时为 `null`，有评估时为**已评估概念**的均值（`overall_mastery_basis` 说明口径）。

## 3. 快照：复用 `mastery_snapshots`，不建新表

`mastery_snapshots` 的键是 `(user_id, course_id, content_node_id)`，没有 metadata 列。
因此概念级追踪用「概念 → 关联 content_node」映射，并用 `gap_type` 承载命名空间标签：

```
mm:<band>              例：mm:assessed_low
mm:<run_tag>:<band>    例：mm:run01:assessed_low     （run_tag 限 [a-z0-9_-]{1,12}）
```

- 只要 `gap_type` 以 `mm:` 开头就是 METAMIND 写的；`tracker` 写的行不带前缀，两者永不混淆。
- `GET .../history` 默认只返回 `mm:` 行；`include_upstream=true` 可同时看 tracker 的时间序列。
- **`not_assessed` 的概念不写快照** —— 写一行 `0.0` 就是伪造一次从未发生的评估。

## 4. 不修改任何学习者状态

- 读层：只 `SELECT`，无 `flush`/`commit`。
- 写层：只在**显式调用** `POST /learner-state/snapshot` 时追加 `mastery_snapshots` 行。
  不触碰 `concept_mastery` / `learning_progress`（含 BKT 参数与 FSRS 调度字段）。
- 回归证据：`verify_learner_state.py` 会在写快照前后对比 `concept_mastery` 与
  `learning_progress` 的全部关键字段，要求逐字节一致。

## 5. 字段映射（API → 上游来源）

| API 字段 | 来源 | 说明 |
|---|---|---|
| `concepts[].mastery_score` | `concept_mastery.mastery_score` | 未评估时为 `null` |
| `concepts[].mastery_source` | 派生 | `concept_mastery` / `bkt_recomputed_from_results` / `inferred_only_no_direct_practice` / `none` |
| `concepts[].direct_attempts` | `max(concept_mastery.practice_count, len(practice_results))` | 直接练习次数，另给 `direct_attempts_basis` 说明取的是哪一边 |
| `concepts[].correct` / `wrong` / `accuracy` | **仅** `practice_results` | 分子分母同源；`accuracy_basis = "practice_results"` |
| `concepts[].practice_result_counts` / `concept_mastery_counts` | 两张表各自原始计数 | 不做 `max()` 混合，便于发现两边不一致 |
| `concepts[].counts_consistent` | 派生 | 两边计数是否一致（不一致时以上两者为准，不要相信单一 `accuracy`） |
| `concepts[].last_practiced_at` / `next_review_at` / `stability_days` | `concept_mastery`（FSRS） | |
| `concepts[].error_categories` | `wrong_answers.error_category` | 真实分类器输出 |
| `concepts[].evidence_practice_result_ids` | `practice_results.id`（最多 5 条） | 证据可回溯 |
| `concepts[].progress.*` | `learning_progress` | 原样透出，不改写 |
| `weak_concepts` | 派生 | 只含**已评估**且未达掌握的概念，按分数升序 |
| `review_due` | `concept_mastery.next_review_at <= now` | |
| `goals[]` | `study_goals` + `knowledge_nodes.metadata.goal_slug` 关联 | |
| `error_profile` | `wrong_answers` 聚合 | `open_wrong_answers` = `mastered=false` 计数 |
| `activity.*` | `learning_progress` 汇总 | 无记录时 `accuracy` 为 `null` |
| `evidence.sources` | 固定清单 | 本次读了哪些表 |

## 6. 已知的上游行为（不是本模块的 bug）

1. **一次答对，分数仍然很低**：MCQ 的 BKT `guess=0.25`，单次正确只能把先验从 0.1 推到约 0.36；
   `learning_progress` 的公式里还有 0.3 权重给了学习时长（演示中为 0）。所以
   「1 题答对 → `concept_mastery=0.10` / `learning_progress=0.48`」是既有模型的真实输出。
   实测连对 4 次才跨过 0.8 进入 `mastered`。
2. **两个掌握度不完全一致**：`concept_mastery`（纯 BKT）与 `learning_progress`（BKT+衰减+时长混合）
   是两条独立链路，本模块**同时透出**两者，不做统一，避免掩盖上游差异。
3. **只有图传播、没有直接练习的概念**会被标为 `not_assessed` 并给出 warning，
   因为 FIRe/LOOM 的传播只是推断，不等于评估过这个学生。

## 7. 验证

```bash
# 场景回归（需要 API 在跑）：空态 → 对/错答 → 快照 → 重置，共 34 项断言
.venv-win/Scripts/python.exe scripts/metamind_demo/verify_learner_state.py

# 三档状态可达性（特别验证 mastered 不是死代码）
.venv-win/Scripts/python.exe scripts/metamind_demo/check_mastered_band.py

# 抓一份真实响应样本到 D:\Hermes\archive\metamind-20261114\
.venv-win/Scripts/python.exe scripts/metamind_demo/capture_state_sample.py
```

## 8. 快照标签与外部的隔离

`mastery_snapshots` 被另外两处业务读取，均已在模块 2 阶段加了最小过滤（`~coalesce(gap_type,'').like('mm:%')`）：

| 读取点 | 用途 | 处理 |
|---|---|---|
| `services/learning_science/velocity_tracker.compute_velocity` | 学习速度 | 过滤 `mm:` 行，避免评价标记被当成学习事件（实测速度值前后完全一致） |
| `services/agent/teaching_strategies.score_strategy_effectiveness` | 策略置信度打分 | 同上 |
| `routers/quiz_submission.mastery_history` | 原始快照时间序列 | **不过滤**（设计如此：这是原始 feed）。`MasterySnapshotResponse.gap_type` 会原样返回 `mm:…`，前端若要展示需按前缀区分 |
| `services/progress/analytics` gap_type_breakdown | 进度总览 | 读的是 `learning_progress.gap_type`（另一列），天然不含 `mm:` |

## 9. 回滚

- 代码：删除 `services/evolution/`、`routers/teaching.py`、`schemas/teaching.py`，
  回退 `services/router_registry.py` 的两行新增即可（`git revert` 单个提交）。
- 数据：`mastery_snapshots` 里只新增了 `mm:` 前缀行，
  `DELETE FROM mastery_snapshots WHERE gap_type LIKE 'mm:%'` 即可清除，不影响其他数据。
  演示数据整体清理仍用 `seed_demo_course.py --purge --yes`。
