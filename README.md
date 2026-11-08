# shortdrama

一句话题材 → 本地出成片。**薄编排层**：不改动 `minimax-h3-turing` / `ComfyUI` / `ai-2080ti22g` 三个既有仓库，只在这里新增编排代码。

- 建设方案（权威设计文档）：[`docs/自动化短剧平台建设方案.html`](docs/自动化短剧平台建设方案.html)
- 目标机器：RTX 2080 Ti 22GB (sm_75) · 32GB RAM · 1TB NVMe，全本机模型
- 关键红线：单卡显存唯一硬瓶颈 → 全流程阶段串行 + 显存门控；成片档只允许 `tiers.reproducible_only()`

## 目录

| 目录 | 内容 |
|---|---|
| `docs/` | 建设方案 HTML（设计权威源）+ `SOP-短剧生产.md`（实操 SOP） |
| `shortdrama/` | 主代码包：`episode.py`（一集七阶段驱动）、`assets.py`（产物清单/静态页）、`studio.py`/`board.py`（审片 Web / 看板）、`loudness.py`/`grade.py`/`bgm.py`（响度/亮度/BGM）、`facecheck.py`/`textcheck.py`（角色与剧本检查）、`tview.py`（终端看图） |
| `prompts/` | 提示词模板（剧本 schema / 定妆出图） |
| `tools/` | 运维与发布工具：`make_voice.py`（TTS 配音对轨）、`make_release.py`（封面/元数据/发布清单）、`build_ass.py`（精确安全区字幕）、`check_discipline.py`（提示词纪律机器检查）、`apply_patches.py`（审片结论落数据）、`compare_old_new.py`/`rollback_shots.py`（重出新旧复审与回滚）、`gpu_watch*.py`（显卡值班）、`release_all.sh`（一键发布流水线） |
| `patches/` | 归档的补丁与审片结论（改词/补字幕/逐镜结论表），供复现 |
| `skills/` | pi-agent skill：`shortdrama-production/SKILL.md`（已软链到 `~/.pi/agent/skills/`） |
| `jobs/` | 预留的任务目录（**实际任务在 `/home/a/ai-work/duanju/`**：一集一个子目录，含 `story.json` / `shots.json` / `cast/` / `frames/` / `clips/` / `voice/` / `episode.mp4` / `report.json` / `qc/review.md` / `cover_*.jpg` / `release_meta.json`） |
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

**当前状态（2026-11-05 实测）**：M1–M6 的能力已由 `shortdrama/episode.py` 一条流水线覆盖
（cast/pick/shots/frames/render/review/assemble/qc 八阶段），另有 `tools/` 提供 TTS 配音、
发布打包、纪律检查；**已实跑产出 3 集 × 35 镜 × 两画幅 = 6 个成片**（`/home/a/ai-work/duanju/`）。
P2（本地 TTS 配音）**已做**（Kokoro-82M，见 `tools/make_voice.py`）。
未做：批量 5 集的自动排期、GPU 显存门控（靠"渲染前停 LLM 进程"的纪律代替）。

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

## 跑一集（已实践：D202611_001）

一集 = 一个目录（目录即状态）。六阶段都能单独重跑，渲染是**单镜粒度**续跑。

```bash
EP=/home/a/ai-work/duanju/D202611_001
EPY=/home/a/ai-work/.venv/bin/python
SCRIPT=workspace/shortdrama/shortdrama/episode.py

$EPY $SCRIPT $EP lint      # 剧本预检（毁集级错误在秒级挡下）
$EPY $SCRIPT $EP cast      # 定妆候选图（Z-Image 中文 t2i，约 10s/张）
#   ↓ agent 看图挑人 → 写 $EP/cast/selection.json
$EPY $SCRIPT $EP pick      # → cast/<id>.png（主）+ <id>_alt.png（备）
#   ↓ P2 台词：代码选镜、LLM 只写字（合并不覆盖）；只改文本 → 不用重渲
$EPY tools/add_dialogue.py --root /home/a/ai-work/duanju --episode E01
$EPY $SCRIPT $EP shots     # story.json → shots.json（自动加 <Picture 1> 前缀）
$EPY $SCRIPT $EP frames    # 每镜 1 张关键帧图（约 10s/镜）→ frames/NN.png
#   ↓ agent 看 qc/keyframes_sheet.png 审片：构图/人物/光线对不对（改图 10s vs 改视频 210s）
$EPY $SCRIPT $EP render    # 逐镜出镜（H3 ref2va/t2v，约 210s/镜）
$EPY $SCRIPT $EP review    # 关键帧拼图 + 视频抽帧拼图 + clips/manifest.json（一屏审片）
$EPY $SCRIPT $EP assemble  # 拼接 → 3x 放大 → 烧字幕 + BGM + 统一响度/亮度 → episode.mp4
$EPY $SCRIPT $EP qc        # 黑帧/静音/削波/时长/分辨率/字幕覆盖率 → report.json
```

- 剧本由 **pi-agent** 按 `prompts/story.md` 写（不用本地 LLM 服务，省掉一整套起停与解析）。
- 详细步骤与踩坑：`docs/SOP-短剧生产.md`；agent 用法：`skills/shortdrama-production/SKILL.md`。
- 审片 + 任务看板走本地 Web（`./sd studio /home/a/ai-work/duanju --port 8095`）：选图 / 逐镜判定 / 看成片 / 逐阶段触发。
  终端等价物：`./sd board /home/a/ai-work/duanju --episode E01`（看阶段）、`--run render`（触发）。
- 方案 v2（2026-11-04，按 3 集实跑修订）：`docs/自动化短剧平台建设方案.html` §4.3 Web 模式（pi-agent 作 agent 内核）、§4.4 阶段任务卡 T0–T7、§15.7 实测数据。
- 实操流程：`docs/SOP-短剧生产.md`（七阶段 T0–T7 + 任务看板 + 交付审批）。
- 交付审批：`sd approval <job>`（提交/批准/驳回；批准时客观门禁重跑，成片改动后批准自动失效）。

## 审片服务（独立常驻 · systemd）

审片台（审片 + 任务看板 + 放映 + 交付审批）做成 **systemd user service**：
独立于任何会话、重启自动拉起、崩溃自动重拉，开机自启（Linger=yes）。

```bash
bash tools/serve_review.sh install    # 安装 + 启动（首次生成固定令牌）
bash tools/serve_review.sh url       # 打印访问 URL
bash tools/serve_review.sh status    # 看状态
bash tools/serve_review.sh logs      # 看日志
bash tools/serve_review.sh stop      # 停
```

- 令牌固定存 `~/.config/shortdrama/studio.token`（600），重启后 URL 不变。
- 写操作（挑图 / 逐镜判定 / 审批）必须带令牌；读操作（看片）局域网开放。
- 想只给外部人看片：用静态资产页 `./sd assets serve <job> --port 8092`（纯只读、无令牌）。

## 静态服务（方案文档）

```bash
bash docs/serve.sh          # → http://<本机IP>:8091/
PORT=8092 bash docs/serve.sh
```

8090 端口是 `ai-2080ti22g/docs/serve.sh`，那份服务也覆盖本方案（旧 URL 仍可用）。

## 资产可见性（已实现）

产物在文件系统里，**不引入数据库**（方案 D9/D11：文件即状态，避免第二个真相源）。给的是"只读视图"：

```bash
./sd assets ls                  # 终端列出产物（含 sha256 / 体积 / 时间）
./sd assets json --out a.json   # 机器可读清单
./sd assets html                # 生成只读静态页（默认 <root>/_assets.html）
./sd assets serve --port 8092   # 生成 + 起本地服务 → http://127.0.0.1:8092/_assets.html
```

- 默认根目录 `../ComfyUI/output`；也可指向某个 `jobs/<job-id>/`
- 产物旁挂 `<file>.json`（sidecar）时，页面直接显示该次运行的 seed / tier / 耗时
- **页面不写任何东西**：挑图、审片的选择仍写回 job 目录里的文件

测试（**必须用 .venv 的 python**：部分测试要 imageio-ffmpeg 提供的 ffmpeg，系统 python3 没装会报错）：
`/home/a/ai-work/.venv/bin/python -m unittest discover -s shortdrama/tests`
