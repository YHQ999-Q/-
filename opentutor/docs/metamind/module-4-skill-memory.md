# METAMIND 模块 4：Skill Memory + Strategy Selector（教学技能记忆 + 策略选择器）

状态：实现完成，模块内 12 组验收 55/55 通过（真实 API + 真实 SQLite + 真实 DeepSeek）。
范围：只做"技能存得住、版本管得了、选得准、调用留痕"，**不做** Tutor 注入（模块 5）、**不做**前端（模块 6）。

---

## 1. 这一层解决的问题

Reflection（模块 3）产出的只是"一次性的改进建议"。如果不落库，它下一次就消失了。
模块 4 把建议变成**可检索、可版本化、可停用、可回滚的教学技能**，并在上课前由一个确定性打分器
决定"这堂课该用哪个技能、用它的哪个版本"，或者"没有合适的，回落原生 Tutor"。

一句话：模块 3 让 Agent 想明白，模块 4 让 Agent 记得住、选得准。

---

## 2. 三层数据模型（SQLite，随主库）

新增三张表，迁移文件 `alembic/versions/20261114_0029_teaching_skill_memory.py`（down_revision = `20261114_0028`）。

### `teaching_skills`（技能身份）
| 字段 | 说明 |
|---|---|
| `id` | UUID 主键 |
| `slug` | 稳定标识（`socratic_questioning`、`compose_analogies_erroranalysis_understanding`），唯一 |
| `name` / `description` | 展示用名称与来历 |
| `origin` | `seed`（仓库自带 markdown 技能）/ `reflection`（反思生成）/ `manual` |
| `source_reflection_id` / `source_evaluation_id` | 溯源：哪个反思、哪次评价催生了它 |
| `enabled` | 是否允许被 Selector 选中（**默认关闭，靠验证挣来**） |
| `current_version` / `version_count` | 当前生效版本号 / 历史版本总数 |
| `seed_path` | seed 技能对应的 markdown 路径（便于对照原文件） |
| `metadata_json` | 场景标签等扩展位（如 `scenes`） |

### `skill_versions`（版本，只追加、不可变）
| 字段 | 说明 |
|---|---|
| `version` | 1,2,3…；**同一版本号永不重写** |
| `status` | `draft`（校验未过）/ `validated`（校验过但未启用）/ `active`（当前生效）/ `superseded`（被新版本取代） |
| `trigger_conditions[]` | 触发条件（"学生把记忆当自进化""两个概念混淆"…） |
| `applicable_objectives[]` | 适用学习目标 slug |
| `target_dimensions[]` | 目标维度：`understanding` / `application` / `transfer` / `multiple` |
| `steps[]` | 教学步骤（可执行的具体动作，禁止"多练习"这类空话） |
| `expected_effect` | 预期效果 |
| `verification{}` | 验证方案（metric / comparison / success_criteria / failure_criteria / uses_parallel_items / next_run_procedure） |
| `reused_skills[]` | 组合型技能的来源技能 |
| `origin_kind` | `reuse_existing` / `compose_existing` / `new_skill` |
| `change_summary` | 这一版相对上一版改了什么（人话） |
| `content_hash` | 内容哈希，用于识别"内容没变就不要造新版本" |
| `validation_json` | 校验结果（`{"ok": bool, "problems": [...]}`） |

### `skill_invocations`（调用留痕）
| 字段 | 说明 |
|---|---|
| `skill_id` / `version_id` / `version` | 精确到"哪个技能的哪一版" |
| `session_id` / `objective_slug` / `scene` | 哪一堂课、哪个目标、什么场景 |
| `selection_json` | 打分快照：候选、分数、命中的触发条件、落选者 |
| `match_score` | 最终得分 |
| `status` | `selected` → `injected` → `evaluated`（三态，见 §5） |
| `selected_at` / `injected_at` / `evaluated_at` | 三个独立时间戳 |
| `evaluation_id` / `outcome_json` | 挂上这次教学的评价与结果（`target_dimension_score`、`success`） |

约束：`(skill_id, version)` 唯一；`skill_versions(skill_id, version)` 唯一。

---

## 3. 版本机制

- **V1 从哪来**：仓库原有 `apps/api/skills/*.md`（5 个技能：analogies、error_analysis、socratic_questioning、spaced_retrieval、worked_examples）。
  `POST /api/teaching/skills/import` 幂等导入：已存在且内容一致 → 不动；内容变化 → 追加新版本，不覆盖。
- **V2 从哪来**：Reflection 的 `improvement_proposal`。`POST /api/teaching/skills/from-reflection` 读提案 →
  定位目标技能（提案里 `reuses_skills` 的第一个 / 或显式 `skill_slug`）/ 或新建复合技能 → 落一条新版本。
- **幂等**：同一个 reflection 对同一个技能只产出一个版本（`source_reflection_id` 去重），重复调用返回 `duplicate`。
- **内容相同不造版本**：与当前版本 steps/triggers/dimensions 完全一致 → `skipped`。
- **失败不覆盖**：校验不过 → 版本存为 `draft`，`current_version` 指针不动，旧版本继续 `active`。
- **回滚**：`POST /api/teaching/skills/{slug}/rollback {"to_version": 1}` = 只移动指针，不删历史。
- **启用门槛**：`enable_if_valid` 且 `validation.ok` 且"反思确实暴露了该维度的短板"
  （`_dimension_score(evaluation, target_dimension) <= 1`，即 2 分制里没拿满分）。仅"校验通过"不足以启用。

真实 V1 → V2 差异（`socratic_questioning`，由真实 Reflection 生成）：

```
changed_fields: change_summary, expected_effect, origin_kind, reused_skills,
                steps, target_dimensions, trigger_conditions
steps        : +6 / −5
triggers     : +3 / −15   （从泛化条件收敛到"概念混淆/记忆当进化"这类可判定条件）
dimensions   : multiple → understanding
```

---

## 4. Selector：怎么选

`services/evolution/selector.py`，纯确定性打分为主，无模型调用（可复现、可解释、可测试）。

**入参 `SelectionContext`**：`course_id`、`objective_slug`、`message`（学生当前话语）、`error_type`、
`scene`、`weakness_dimension`、`concept_names/concept_ids`。

**打分**（`_score_candidate`）：

```
score = 2.0 * 命中触发条件数 / 条件总数          # 适用性，主项
      + 目标匹配 + 维度匹配 + 场景匹配 + 错误类型匹配   # 结构匹配
      + 0.6 * 历史该版本在同一维度的平均分           # 经验加成（过去真的有用）
      − 0.3 （若该技能在该学生身上历史平均分很低）      # 惩罚
命中条件数 < MIN_TRIGGER_HITS(=1) → 直接淘汰（不进候选）
```

**回落规则（重要）**：没有候选 → 返回 `None` + `fallback_to_native_tutor=true` + 原因文案，
交回 OpenTutor 原生教学。**不强行选一个"最像的"**，也不因为"目标相同"就算匹配。
自动带出的薄弱点只用于加分，不算触发命中（否则任何提到该知识点的技能都会看起来"适用"）。

真实一次选择（Run 01 场景，学生说"记住我喜欢例子就是自进化吗"）：

```
selected : socratic_questioning V2   score 5.3333   触发 2/3（+ 目标/维度/错误类型/场景 4 项全中）
runner-up: compose_erroranalysis_analogies_understanding V1  4.9333（仅场景未中）
           error_analysis V1  1.9667 | spaced_retrieval V1  1.4538
```

---

## 5. 三态调用追踪：选 ≠ 注入 ≠ 被执行

| 状态 | 何时写入 | 意义 |
|---|---|---|
| `selected` | Selector 返回候选 | "打算用"。落 `selection_json`，此时 `injected_at` 为空 |
| `injected` | 模块 5 把技能内容拼进 Tutor prompt 之后 | "已经进了模型上下文" |
| `evaluated` | 挂了这次教学的评价 | "结果回来了"，写 `outcome_json` |

为什么分开：沿用模块 2 的教训（"模型说注入成功" ≠ "策略真的被执行"）。
如果一次评价的维度里没有该技能的目标维度分，invocation 停在 `injected` 并写 note，**不伪造成功**。

真实一条（`capture_skill_sample.py` 产出，`samples/invocation-evaluated.json`）：

```
version 2 | match_score 5.3333 | session run01
selected_at  2026-11-14T07:00:09
injected_at  2026-11-14T07:00:11.288586
evaluated_at 2026-11-14T07:00:13.368985
outcome: target_dimension=understanding, score=0, verdict=not_achieved, success=false
```

`success = score >= 2`（2 分制）。

---

## 6. API（全部挂在 `/api/teaching`，独立 router `routers/teaching_skills.py`）

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/skills/import` | 幂等导入 markdown 种子技能 |
| GET | `/skills` | 列出技能（含 enabled / current_version / origin） |
| GET | `/skills/{slug}` | 单个技能 + 当前版本 + 调用统计 |
| GET | `/skills/{slug}/versions` | 全部历史版本 |
| GET | `/skills/{slug}/diff?from_version=&to_version=` | 两版差异（新增/删除/变更字段） |
| POST | `/skills/{slug}/toggle` | 启停 |
| POST | `/skills/{slug}/rollback` | 指针回退到旧版本 |
| POST | `/skills/from-reflection` | 由反思生成/升级技能 |
| POST | `/selector/select` | 按当前情况选技能（返回候选、分数、落选者） |
| GET | `/invocations` | 调用记录（按课程/技能/状态过滤） |
| POST | `/invocations/{id}/inject` | 标记已注入 |
| POST | `/invocations/{id}/evaluate` | 挂评价，计算 outcome |

安全边界：技能内容只能来自 Reflection 的结构化字段，`validate_version_payload` 逐字段校验
（触发条件非空、≥2 步、单步不得过短、禁止"多练习/认真学"这类空话、必须有验证标准）；
系统不执行模型生成的代码或 SQL。

---

## 7. 验证

```bash
cd apps/api
# 模块 4 验收（12 组，真实 API + 真实模型）
.venv-win/Scripts/python.exe scripts/metamind_demo/verify_skill_memory.py

# 抓真实样本（版本/差异/选择/调用）
.venv-win/Scripts/python.exe scripts/metamind_demo/capture_skill_sample.py

# 把技能库恢复成 5 个 seed 技能 V1（演示前重置）
.venv-win/Scripts/python.exe scripts/metamind_demo/reset_skill_library.py --yes
```

当前结果：**PASSED 55, FAILED 0**（连跑两次一致）。

覆盖点：幂等导入 / 由反思建技能 / V1→V2 / 版本差异 / 重启后仍读到 V2 / 选择器匹配 /
无匹配回落 / 停用后不再被选 / 非法配置无法启用 / invocation 精确到版本 / 失败更新不覆盖 /
模块 1–3 端点回归。

---

## 8. 已知问题与边界

1. **Selector 只看"当前情况"**：`message / objective / error_type / scene / concept_names`。
   学生历史薄弱点只加分，不构成入选理由。若 `message` 很模糊，可能一个技能都选不中——这是设计意图（回落原生 Tutor），不是 bug。
2. **无最低分阈值**：命中 1 条触发条件即可入选（分数可能只有 1.x）。模块 5 若要更保守，可加 `MIN_SCORE`。
3. **技能不跨课程隔离**：技能库是用户级全局的，`applicable_objectives` 用目标 slug 约束。
   多课程下 slug 语义重叠时，靠 `objective_slug` 精确匹配避坑。
4. **未做 reranker / 向量检索**：故意不引入向量库（黑客松范围内收益低、成本高）。
5. **前端无入口**：本模块只有 API，Teaching / Evolution Panel 界面在模块 6。
6. **`skills/*.md` 仍是纯文本种子**：DB 是唯一真相源，改 markdown 后需重新 `import` 才会进库。

---

## 9. 回滚

```bash
# 代码（未提交时）
git checkout -- apps/api/services/evolution/selector.py ...

# 数据：技能库回到 5 个 seed 技能 V1，清空 invocations
cd apps/api && .venv-win/Scripts/python.exe scripts/metamind_demo/reset_skill_library.py --yes

# 彻底删表（仅开发库）
DROP TABLE skill_invocations; DROP TABLE skill_versions; DROP TABLE teaching_skills;
```
