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

$EPY $SCRIPT $EP lint      # 剧本预检（毁集级错误在这里挡下，秒级）
$EPY $SCRIPT $EP cast      # 出定妆候选图 → $EP/cast/<id>_cand{1,2,3}.png
#   ↓ agent 看图挑人（read 工具直接看 PNG），写 $EP/cast/selection.json
$EPY $SCRIPT $EP pick      # → $EP/cast/<id>.png（主）+ <id>_alt.png（备）
$EPY $SCRIPT $EP shots     # story.json → shots.json（自动加 <Picture 1> 前缀）
$EPY $SCRIPT $EP frames    # 每镜 1 张关键帧图（10s/镜）→ $EP/frames/NN.png
#   ↓ agent 看 $EP/qc/keyframes_sheet.png 审片：构图/人物/光线对不对
$EPY $SCRIPT $EP render    # 逐镜出镜 → $EP/clips/NN.mp4（约 210s/镜，单镜粒度续跑）
$EPY $SCRIPT $EP review    # 关键帧拼图 + 视频抽帧拼图 + clips/manifest.json（一屏审片）
$EPY $SCRIPT $EP assemble  # 拼接→3x 放大→烧字幕+统一响度 → $EP/episode.mp4
$EPY $SCRIPT $EP qc        # 黑帧/静音/削波/时长/分辨率 → $EP/report.json（阻断项退出码非 0）
```

## agent 在每一步该干什么

1. **写剧本**（P1）：按 `workspace/shortdrama/prompts/story.md` 的 schema 写 `$EP/story.json`。
   中英双轨；`prompt_en` 必须是英文；每镜一个 `ref` 角色；每镜加 `prompt_cn`（中文画面描述，供关键帧用）。
2. **预检**：`lint`（秒级）——毁集级的错（中文 prompt_en、角色引用错、镜数不符）在这里挡下。
3. **出定妆图**（P2）：跑 `cast`，然后**用 read 工具逐张看候选图**再挑：
   主图 = 脸最正、最清楚、五官不被遮挡的一张；备图 = 同一张脸但角度略不同。
   把选择写进 `cast/selection.json` 再跑 `pick`。
4. **分镜**（P3）：`shots` 阶段自动拼句式，不要手改 shots.json。
5. **关键帧**（P3.5）：`frames`（10s/镜）→ 看 `qc/keyframes_sheet.png`：
   **构图/人物/光线不对，现在改是 10s，等出完视频再改是 210s**。
6. **出镜**（P4）：`render` 直接后台跑（6 镜 ≈ 21 分钟），失败自动换 seed 重试。
   要构图完全可控就换 `render-i2v`（关键帧当首帧，代价是运镜自由度下降）。
7. **审片**（P4.5）：`review` → `qc/review.md`（**文字审核单**：每镜一行 + 结论列）+ `qc/frames_sheet.png` + `clips/manifest.json`；废镜在这里重出。
   **TUI 里人看不到图**：让 agent 看图并把结论写进 `review.md` 的「结论」列，人只拍板；
   人自己要看图用 `python3 shortdrama/tview.py <job>/qc/frames_sheet.png`（终端 ANSI 彩色块）
   或 `./sd assets serve <job-dir> --port 8092`（浏览器，图+视频）。
8. **后期**（P5）：`assemble`（中间件会自动复用，重出 1 镜不必重拼重放大）。
9. **质检**（P6）：`qc` + **自己看片**，把新坑写进 SOP。

## 踩过的坑（别再踩）
- **字幕：相邻重复是噪音，隔得远的重复是回环**。同一句在 ≤2 镜内重复 → 成片只烧一次；
  隔 ≥3 镜再出现 → **必须留**（E01 的"你该回来了。"在 s11/s23 是同一场戏演两遍，第二次才有分量）。
  **别做全局去重**，会抹掉回环设计。
- **关键帧先行**：出图 10s/张 vs 出镜 210s/镜。6 镜关键帧 70 秒就能判断整集观感，
  实测质量与 ref2va 成片相当 → 出镜前先出关键帧 + 审片。
- **TUI 里没有图片/视频预览**：别让审核依赖"人肉眼看图"。
  结论落成文字（`review.md` 的结论列），agent 看图、人拍板；人要看图用 `tview.py`（ANSI 彩色块）或资产页。
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
