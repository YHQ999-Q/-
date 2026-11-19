# METAMIND · 自进化教学系统

METAMIND 是一个以 **OpenTutor 为自进化底座**构建的自进化教学系统：AI 学习助手 + 课堂采集 Agent + 第二大脑知识库三版融合的完整产品。它让 AI 不仅"教"，还能根据真实的评价、反思和学习者状态**自主改进教学策略**，并形成可追踪、可回放、可回滚的"技能版本"闭环。

> 📌 本仓库是 METAMIND 的**公开源码仓库**（黑客松项目正式化）。原黑客松交付 ZIP 与路演 PPT 保留在仓库 `main` 分支的历史/其他分支中作为资料。**本仓库公开，严禁提交任何真实密钥或个人数据**（详见「安全红线」）。

---

## 一、它是什么

| 模块 | 端口 | 作用 | 真实边界 |
| --- | --- | --- | --- |
| **React 前端** | 5173 | 学习助手 / 第二大脑 / 学习画像 / 待办 / 成长，黄紫主题 | 完整可运行 |
| **OpenTutor 底座** | 8000 | 自进化：评价、反思、技能版本、调用证据追踪 | 完整可运行 |
| **教学 Agent** | 5001 | 课堂采集（拍照/录音/3 题自测）、知识图谱、策略版本回滚 | 完整可运行 |
| **第二大脑 Bee** | — | 语料 → 事实/洞察/待办 → 注入教学库 | 完整可运行 |
| **同步桥 bridge** | — | Bee → 教学库 幂等同步 | 完整可运行 |
| **旧版记忆站** | 5002 | 旧版本镜像 | 已停用（保留代码） |

**核心闭环（P0）**：评价当前策略 → 对比新旧策略 → 反思 → 生成改进后的技能版本 v2 → 下一轮调用 v2 → 留下可追踪证据。`verify_integration.py` 当前 **17/17 通过**。

> ⚠️ 诚实边界：演示数据仅证明**闭环跑通**，不证明学习效果提升；V2 只确认"策略进了提示词"，不自动认证每步都被执行。请勿在答辩/宣传中夸大。

---

## 二、快速开始

### 依赖
- Python 3.12+（推荐由 uv 安装管理）
- Node.js 18+（前端推荐 22/24）

### 本机已有环境
```bash
# Windows：双击 启动全部.bat（含后端、前端、同步）
# 然后浏览器打开：
open http://127.0.0.1:5173/#/agent
```

### 从零安装（新机器）
```bash
# 1. 一键安装依赖（Python + npm）
#    Windows: 双击 一键安装依赖.bat
#    或手动：
cd frontend && npm install
# OpenTutor 后端用 uv 建 venv 并装依赖（见 opentutor/README.md）

# 2. 启动全部
双击 启动全部.bat
```

### 关键页面地址
| 页面 | 地址 |
| --- | --- |
| 主入口 | `http://127.0.0.1:5173/#/` |
| 学习助手（问答+课堂采集） | `http://127.0.0.1:5173/#/agent` |
| 学习画像（SOLO/掌握度/策略版本/证据链） | `http://127.0.0.1:5173/#/learner-model` |
| 第二大脑 | `http://127.0.0.1:5173/#/second-brain` |
| 教学页直开 | `http://127.0.0.1:5001/teach` |
| OpenTutor API 文档 | `http://127.0.0.1:8000/docs` |

---

## 三、目录结构

```
├── frontend/            # React 网页（黄紫主题，Vite）
├── web/                 # 前端构建产物（可直接托管，含 serverless 后端 index.js）
├── opentutor/           # OpenTutor 自进化底座（FastAPI，端口 8000）
│   ├── apps/api/services/evolution/   # 评价/反思/选择/技能进化/技能注入
│   └── apps/api/scripts/metamind_demo/# 演示与验证脚本
├── teaching/            # 教学 Agent（Flask，端口 5001）
├── second-brain/        # 第二大脑 Bee（语料→事实/洞察/待办）
├── bridge/              # bee_sync.py：Bee → 教学库 幂等同步
├── memory-site/         # 旧版记忆站镜像（5002，已停用）
├── hardware/hack_esp32s3_watch/  # ESP32-S3 硬件（需 ESP-IDF 5.2.x）
├── verify_integration.py# P0 集成验收脚本（17/17）
├── 启动全部.bat / 停止全部.bat / 一键安装依赖.bat
└── README_交付说明.md / README_合并说明.md   # 原始交付/合并文档
```

---

## 四、开发工作流（Git）

```bash
# 常规迭代：基于 dev 分支开发，合回 main 后自动部署
git checkout dev
# ...修改代码，本地 `启动全部.bat` 验证...
git add -A && git commit -m "feat: ..."
git push origin dev          # 触发 dev 预览部署
# 验收通过后：
git push # 或发 PR 合回 main，触发生产部署
```

**部署**：合并/推送到 `main` 后，CI 自动构建前端并部署（前端 Vercel，后端按实际栈云部署）。详见 `.github/workflows/`。

---

## 五、安全红线（务必阅读）

**本仓库公开。以下内容绝不允许提交：**

- ❌ 真实 API Key（`api_key.txt`、任何 `.env` 中的真实值）
- ❌ 真实数据库（`*.db`，含用户会话、认证凭据、聊天记录、教学记录）
- ❌ `native-data/`、`uploads/`、`private-data/`、`evidence/`、`logs`
- ❌ 个人学习记录、录音、真实语料

**做法**：所有密钥一律放 `.env`（不入 Git），仓库只提交 `.env.example` 模板。根 `.gitignore` 已兜底屏蔽以上路径。提交前请自检：`git status` 确认没有 `*.db` / `api_key` / `settings.env` / `native-data`。

---

## 六、License & Attributions

- OpenTutor 及其上游依赖遵循各自开源许可（见 `opentutor/LICENSE` 及各子模块）。
- 硬件固件基于 ESP-IDF，遵循其许可。
- 本项目代码的归属与许可以各文件头部声明为准；如用于商业或二次分发，请确认 OpenTutor 上游许可条款。

---

*METAMIND · 以 OpenTutor 为自进化底座 · 三版融合交付项目*
