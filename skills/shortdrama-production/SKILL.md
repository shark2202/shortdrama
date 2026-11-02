---
name: shortdrama-production
description: 在本机（RTX 2080Ti 22G + ComfyUI :8188 + MiniMax-H3 W4A8 + Z-Image）用 pi-agent 当编剧、ComfyUI 出画出片，一句话题材产出短剧成片（剧本/定妆图/分镜/成片 MP4/质检报告），并沉淀 SOP 与知识资产。触发词：短剧、出片、剧本→成片、shot、episode.mp4、Z-Image 定妆、H3 出镜、跑一集短剧。
---

# 短剧生产（本机产线）

**一句话题材 → 本地出成片。** pi-agent 当编剧+调度+质检，ComfyUI 当画+拍。

## 组件与路径

| 用途 | 用什么 | 路径 |
|---|---|---|
| 渲染执行体 | ComfyUI v0.33.1 | http://127.0.0.1:8188（启动：`workspace/minimax-h3-turing/scripts/h3_launch.local.sh`） |
| 出图（定妆照） | Z-Image Turbo int8 | `workspace/minimax-h3-turing/workflows/api/zimage_turbo_t2i_api.json` |
| 出镜（视频） | MiniMax-H3 W4A8 ref2va | `workspace/minimax-h3-turing/workflows/api/h3_w4a8_ref2va_api.json` |
| 驱动脚本 | 本 skill 的流水线 | `workspace/shortdrama/shortdrama/episode.py` |
| 3x 放大 | 复用已有脚本 | `workspace/minimax-h3-turing/scripts/upscale_video.py` |
| ComfyUI 客户端 | 已装包 | `h3turing`（上传/提交/轮询/下载/重试都在里面） |

Python 一律用 `/home/a/ai-work/.venv/bin/python`（里面有 `h3turing` + `imageio-ffmpeg`）。

## 六阶段（每阶段可单独重跑）

```bash
EP=/home/a/ai-work/duanju/<episode-id>      # 一集 = 一个目录
EPY=/home/a/ai-work/.venv/bin/python
SCRIPT=/home/a/ai-work/workspace/shortdrama/shortdrama/episode.py

$EPY $SCRIPT $EP cast      # 出定妆候选图 → $EP/cast/<id>_cand{1,2,3}.png
#   ↓ agent 看图挑人（read 工具直接看 PNG），写 $EP/cast/selection.json
$EPY $SCRIPT $EP pick      # → $EP/cast/<id>.png（主）+ <id>_alt.png（备）
$EPY $SCRIPT $EP shots     # story.json → shots.json（自动加 <Picture 1> 前缀）
$EPY $SCRIPT $EP render    # 逐镜出镜 → $EP/clips/NN.mp4（约 200s/镜，单镜粒度续跑）
$EPY $SCRIPT $EP assemble  # 拼接→3x 放大→烧字幕+统一响度 → $EP/episode.mp4
$EPY $SCRIPT $EP qc        # 黑帧/静音/削波/时长/分辨率 → $EP/report.json（阻断项退出码非 0）
```

## agent 在每一步该干什么

1. **写剧本**（P1）：按 `workspace/shortdrama/prompts/story.md` 的 schema 写 `$EP/story.json`。
   中英双轨；`prompt_en` 必须是英文；每镜一个 `primary` 角色。
2. **出定妆图**（P2）：跑 `cast`，然后**用 read 工具逐张看候选图**再挑：
   主图 = 脸最正、最清楚、五官不被遮挡的一张；备图 = 同一张脸但角度略不同。
   把选择写进 `cast/selection.json` 再跑 `pick`。
3. **分镜**（P3）：`shots` 阶段自动拼句式，不要手改 shots.json。
4. **出镜**（P4）：`render` 直接后台跑（6 镜 ≈ 20 分钟），失败自动换 seed 重试。
5. **后期**（P5）：`assemble`。
6. **质检**（P6）：`qc` + **自己看片**（抽帧看人脸是否一致、有没有黑帧），写进 SOP。

## 踩过的坑（别再踩）

- **定妆描述不能挡脸**：写"帽檐压低遮住眼睛"→ Z-Image 直接画出黑色眼罩，定妆图作废。
  改写成"帽檐压低"以外的中性描述，或直接不写遮挡物。
- **H3 只吃英文提示词**：中文写进 `prompt_en` 效果差（文本编码器是英文的）。
- **句式固定**：`<Picture 1> and <Picture 2> show the same character. <动作/镜头/光线>, <风格>, natural ambient audio: <环境音>`
  不要让模型自由发挥结构，也不要删掉 `natural ambient audio`（否则该镜没声音）。
- **seed 每镜递增**：ComfyUI 对完全相同的 prompt 会静默去重（prompt_id 由输入决定）。
  `episode.py` 已按 `SEED + idx` 处理，重试时再 +1。
- **成片只跑可复现档**（ref2va bypass，无缓存加速）：缓存档（FirstBlockCache/TeaCache/T8）
  会 fork 采样轨迹，同 seed 出不同画面，只能拿来做草稿。
- **字幕先放大再烧**：640×352 上烧字再放大 = 糊；在 1920×1056 上烧更清楚。
  `episode.py assemble` 已按这个顺序。
- **H3 源音轨会削波**（实测 02/05 镜峰值 0 dBFS）：后期必须挂 `alimiter`（`episode.py assemble` 已带
  `loudnorm + alimiter=limit=0.891`）。镜头级削波记 warn，成片级削波才阻断。
- **扩散模型写不好招牌文字**：霓虹牌上的汉字是乱码。构图避开大面积可读文字，或后期贴真字。
- **本机没有 ffprobe**：`episode.py` 用 `ffmpeg -i` 解析 stderr 拿时长/分辨率/帧率。
- **显存是唯一硬瓶颈**：本地 LLM（13–17GB）和 H3（≈20GB）不能同时驻留，
  流水线必须阶段串行；渲染期间别启别的 GPU 任务。

## 知识资产沉淀（每次跑完都要做）
## 知识资产沉淀（每次跑完都要做）

- 剧本/分镜/定妆图/成片 → 留在 `$EP`（目录即状态）。
- 新踩的坑 → 追加到本 SKILL 的"踩过的坑"。
- 流程有变动 → 同步 `workspace/shortdrama/docs/SOP-短剧生产.md` 与 `workspace/shortdrama/README.md`。

## 参考实践

- **D202611_001《凌晨三点的便利店》**（`/home/a/ai-work/duanju/D202611_001/`）：
  6 镜 / 31 s / 2 角色，全链路跑通。实测：定妆 10 s/张、出镜 210 s/镜、整集 ≈ 25 min；
  质检通过（2 条提示：源镜头削波）。含剧本、抽帧 contact sheet、成片。
