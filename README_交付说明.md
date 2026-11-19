# MetaMind 三版合一 · 交付包（2026-11-15）

## 包含什么

- `frontend/`：React 网页源码（黄紫主题），已构建产物在 `web/`。
- `web/`：可直接托管的静态网页（构建结果，需要后端代理，见下）。
- `opentutor/`：OpenTutor 8000，自进化底座（评价 / 反思 / 技能版本 / 调用证据）。
- `teaching/`：教学 Agent 5001（课堂采集、知识图谱、3 题自测、策略 v9、液态图谱数据）。
- `bridge/`：Bee 第二大脑 → 教学库的同步桥。
- `memory-site/`：旧版记忆站镜像（5002）。
- `second-brain/`：Bee 语料与演示数据。
- `hardware/hack_esp32s3_watch/`：ESP32-S3-CAM 固件源码、接线表、编译/烧录脚本（不含 build 产物）。
- 数据库：`teaching/metamind.db`、`opentutor/native-data/data/opentutor.db`、`second-brain/data/demo.db`，均带演示数据。

## 启动

本机已经有环境时：

1. 双击 `启动全部.bat`
2. 浏览器打开 `http://127.0.0.1:5173/#/agent`

换一台新机器：

1. 先双击 `一键安装依赖.bat`（需要 Python 3.12、uv、Node.js 18+）
2. 再双击 `启动全部.bat`

## 端口

| 端口 | 服务 |
| --- | --- |
| 5173 | React 前端（学习助手、第二大脑、课堂知识库、液态图谱） |
| 8000 | OpenTutor（自进化的评价/反思/技能版本） |
| 5001 | 教学 Agent（课堂采集、知识图谱、策略版本） |
| 5002 | 旧版记忆站镜像 |

## 硬件

- 目录：`hardware/hack_esp32s3_watch/`
- 编译：ESP-IDF 5.2.x，双击 `一键编译.bat`；烧录：`一键烧录.bat`
- 接线和现场排查：`接线表.txt`、`硬件联调清单.md`、`比赛现场救命清单.txt`
- 注意：ESP-IDF 不认中文路径，务必把 `hack_esp32s3_watch` 放在纯英文路径下再编译。

## 安全提醒

`teaching/api_key.txt` 和 `opentutor/native-data/settings.env` 里有本机 API key。
这个包只给自己/团队内部使用，**不要上传到公开仓库或公开网盘**。

## 诚实边界（答辩时别说过头）

- run01 / run02 两次评价分数持平：只能证明闭环跑通，不能证明学习效果提升。
- V2 技能版本：`prompt_injection_verified=true`，但 `execution_state=unverified`，系统只确认“策略进了提示词”，没有自动认证每一步都被执行。
- 课堂策略 v9：切换前正确率 0.33，切换后还没有复测数据（`new=null`）。
- 第二大脑里的洞察来自 Bee 语料整理，不等于学习效果实验。
