# shortdrama

一句话题材 → 本地出成片。**薄编排层**：不改动 `minimax-h3-turing` / `ComfyUI` / `ai-2080ti22g` 三个既有仓库，只在这里新增编排代码。

- 建设方案（权威设计文档）：[`docs/自动化短剧平台建设方案.html`](docs/自动化短剧平台建设方案.html)
- 目标机器：RTX 2080 Ti 22GB (sm_75) · 32GB RAM · 1TB NVMe，全本机模型
- 关键红线：单卡显存唯一硬瓶颈 → 全流程阶段串行 + 显存门控；成片档只允许 `tiers.reproducible_only()`

## 目录

| 目录 | 内容 |
|---|---|
| `docs/` | 建设方案 HTML（设计权威源） |
| `shortdrama/` | 主代码包：`llm.py` `story.py` `cast.py` `shots.py` `render.py` `assemble.py` `qc.py` `job.py` `gate.py` `config.py` |
| `prompts/` | 提示词模板（剧本 / 分镜 / 字幕） |
| `jobs/` | 任务目录，一集一个子目录（`job.json` / `story.json` / `shots.json` / `cast/` / `clips/` / `episode.mp4` / `report.json` / `timing.json`） |
| `logs/` | 运行日志 |

## 复用（不重复造轮子）

| 需求 | 直接用 |
|---|---|
| ComfyUI 客户端 / 档位 / 工作流 | `../minimax-h3-turing` 的 `h3turing` 包 |
| 多镜驱动 / 拼接 | `../minimax-h3-turing/scripts/shortvideo.py` |
| 1080p 上采样 | `../minimax-h3-turing/scripts/upscale_video.py` |
| 中文出图 → H3 桥 | `../minimax-h3-turing/scripts/zimage_to_h3.py` |
| 渲染执行体 | `../ComfyUI`（:8188） |

## 里程碑（详见方案 §12）

| 里程碑 | 工期 | 交付物 |
|---|---|---|
| M0 | 0.5 天 | 基线数据表（`h3turing doctor` 全绿 + bench 基线） |
| M1 | 1 天 | `render.py`：3 镜 clips、重试、单镜粒度续跑 |
| M2 | 1–1.5 天 | `llm.py` + `story.py` + `shots.py` + `prompts/`：10 题材 ≥8 份 schema 合法 |
| M3 | 1 天 | `cast.py`：定妆图候选挑选，3 镜人脸/服装一致 |
| M4 | 1 天 | `assemble.py`：1920×1056 / 24fps / 带字幕 / 不削波 |
| M5 | 1–1.5 天 | `job.py` + `gate.py` + `sd` CLI：一条命令出片 + 显存门控 |
| M6 | 1 天 | `qc.py` + `sd batch`：质检黑帧/静音/削波/缺镜，批量 5 集 |
| P2（可选） | 2–3 天 | 本地 TTS 配音 + 对轨 |

## 常用命令

```bash
# 开工自检
../minimax-h3-turing/scripts/h3turing.local.sh doctor --models-dir ../models

# 渲染执行体（防黑屏参数固定）
bash ../minimax-h3-turing/scripts/h3_launch.local.sh

# 查 ComfyUI 队列 / GPU
curl -s http://127.0.0.1:8188/queue | head -c 400
nvidia-smi --query-gpu=name,temperature.gpu,memory.used,memory.total,utilization.gpu --format=csv
```

## 静态服务（方案文档）

```bash
```bash
bash docs/serve.sh          # → http://<本机IP>:8091/
PORT=8092 bash docs/serve.sh
```

8090 端口是 `ai-2080ti22g/docs/serve.sh`，那份服务也覆盖本方案（旧 URL 仍可用）。
