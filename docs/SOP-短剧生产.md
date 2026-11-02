# SOP · 短剧生产（本机产线）

> 一次成功的实践：**D202611_001《凌晨三点的便利店》**（6 镜 / 31 秒 / 2 角色）。
> 本文是**实操版**（怎么敲、踩了什么坑），设计依据见 `自动化短剧平台建设方案.html`。
> agent 侧用法见 `../skills/shortdrama-production/SKILL.md`。

## 0. 产线组成（一句话）

```
题材 → [pi-agent 写剧本] → story.json → [Z-Image 出定妆图] → cast/*.png
     → [H3 ref2va 逐镜出镜] → clips/*.mp4 → [ffmpeg 拼接/放大/烧字幕/限幅] → episode.mp4
     → [ffmpeg 质检] → report.json
```

- **谁干什么**：pi-agent 当编剧 + 调度 + 挑图 + 质检；ComfyUI 当画 + 拍；ffmpeg 当剪。
- **不重造轮子**：ComfyUI 客户端用 `h3turing` 包，工作流用 `minimax-h3-turing/workflows/api/` 里的现成 JSON，
  放大用 `minimax-h3-turing/scripts/upscale_video.py`。
- **不引入数据库**：一集 = 一个目录，目录即状态，任何阶段可单独重跑。

## 1. 开工前自检

```bash
curl -s http://127.0.0.1:8188/system_stats | head -c 200   # ComfyUI 在？
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```
ComfyUI 没起：`bash workspace/minimax-h3-turing/scripts/h3_launch.local.sh`（**必须**带
`--reserve-vram 2.5 --vram-headroom 0.5 --disable-pinned-memory`，否则出黑屏）。

## 2. 六阶段操作

```bash
EP=/home/a/ai-work/duanju/D202611_001
EPY=/home/a/ai-work/.venv/bin/python          # 里面有 h3turing + imageio-ffmpeg
SCRIPT=workspace/shortdrama/shortdrama/episode.py
```

| 阶段 | 命令 | 产出 | 实测耗时 |
|---|---|---|---|
| P1 剧本 | pi-agent 按 `prompts/story.md` 写 `$EP/story.json` | 中英双轨剧本 | 人工/模型，秒级 |
| P2 定妆 | `$EPY $SCRIPT $EP cast` → 看图 → 写 `cast/selection.json` → `$EPY $SCRIPT $EP pick` | `cast/<id>.png` + `<id>_alt.png` | **10 s/张**（首张 20 s，含模型加载） |
| P3 分镜 | `$EPY $SCRIPT $EP shots` | `shots.json`（含 H3 提示词） | 秒级 |
| P4 出镜 | `$EPY $SCRIPT $EP render` | `clips/01..06.mp4` | **210 s/镜**（640×352 / 124 帧 @24fps / 5.17 s / 带音轨） |
| P5 后期 | `$EPY $SCRIPT $EP assemble` | `episode.mp4`（1920×1056 / 24fps / 31.06 s） | ~1 分钟 |
| P6 质检 | `$EPY $SCRIPT $EP qc` | `report.json` | ~30 s |

**一集（6 镜）总耗时 ≈ 25 分钟 GPU + 5 分钟 agent 交互。**

## 3. 每阶段的"人/agent 该做什么"

### P1 剧本
- schema 见 `prompts/story.md`。中英双轨：`prompt_en`/`sfx_en` 英文喂 H3，其余中文给人看。
- **每镜一个 `primary` 角色**——它决定这一镜用谁的定妆图当参考。

### P2 定妆图（这一步最值钱）
- 出 3 张候选 → **必须真的看图挑**（agent 用 read 工具直接看 PNG），别省这一步。
- 挑法：主图 = 脸最正、最清楚、五官无遮挡；备图 = 同一张脸但角度略不同（给 ref2va 一点变化余地）。
- 选择写进 `cast/selection.json`：`{"c1": {"main": "cand1", "alt": "cand2"}}`，再跑 `pick`。
- ⚠️ **描述里不能有遮脸的元素**（见 §5）。

### P3 分镜
- 句式固定，代码拼好，别手改：
  `<Picture 1> and <Picture 2> show the same character. <动作/镜头/光线>, <风格>, natural ambient audio: <环境音>`
- `<Picture 1>` / `<Picture 2>` = 同一角色的主图 + 备图（ref2va 的一致性来源）。

### P4 出镜
- 后台跑（21 分钟），`clips/NN.mp4` 已存在就跳过 → **单镜粒度续跑**。
- 失败自动换 seed 重试 ≤3 次（`SEED + idx + attempt`）。
- **成片只用可复现档**（ref2va bypass，无缓存加速）。缓存档（FirstBlockCache/TeaCache/T8）会 fork 采样轨迹，同 seed 出不同画面。

### P5 后期（顺序有讲究）
1. concat demuxer 逐样本 copy（各镜参数一致，音轨不重编码）
2. 3x 放大（`upscale_video.py --scale 3`，lanczos）→ 1920×1056
3. **烧字幕**（libass + Noto Sans CJK SC，白字黑边，`FontSize=42, MarginV=56`）
4. 音频：`loudnorm=I=-16:TP=-1.5:LRA=11, alimiter=limit=0.891`（**限幅不能省**，见 §5）
5. H.264 + AAC 48kHz / CRF 18 / faststart

> **与建设方案 §9.5 的唯一差异**：先放大再烧字幕（方案是先烧后放大）。
> 640×352 上烧字再放大 = 糊；1920×1056 上烧清楚得多。字幕时间轴按**实际**每镜时长排。

### P6 质检
`episode.py qc` 自动查：黑帧（blackdetect）/ 静音（silencedetect）/ 削波（volumedetect）/
时长偏差 / 分辨率 / 帧率 / 缺镜 → `report.json`；有阻断项时退出码非 0。

**自动质检不代替人眼**：出片后一定要抽帧看（`qc/contact_sheet.png` 那招）：
```bash
for i in 01 02 03 04 05 06; do $FF -y -ss 2.5 -i clips/$i.mp4 -frames:v 1 qc/f$i.png; done
$FF -y -framerate 1 -pattern_type glob -i 'qc/f*.png' -vf tile=3x2 -frames:v 1 qc/contact_sheet.png
```
看三件事：**人脸是不是同一个人**、**有没有穿帮/畸变**、**镜头类型对不对**。

## 4. 质检分级（阻断 vs 提示）

| 级别 | 项目 | 处置 |
|---|---|---|
| 阻断 | 交付分辨率≠1920×1056、帧率≠24、缺镜、黑帧、**成片**削波 | 必须重出/重压 |
| 提示 | 单镜时长偏差 >0.5s、无音轨、静音段、**源镜头**削波 | 后期兜底或可接受 |

源镜头的削波（如本次 02/05 镜 max 0.0 dBFS）由后期 limiter 压住，成片级不再阻断。

## 5. 本次实践踩到的坑

1. **定妆描述遮脸 → 黑眼罩**。
   写"帽檐压低**遮住眼睛**"→ Z-Image 照着画出黑色眼罩，脸全废（c2 前三张全废，重出才修好）。
   规则：`desc_cn` 不写任何遮挡五官的词；需要"神秘"就靠**打光**（半边脸在阴影里）而不是物理遮挡。
2. **H3 源音轨会削波**：本次 02/05 镜 max_volume 到 0 dBFS。→ 后期必须挂 `alimiter`。
3. **扩散模型写不好招牌文字**：便利店霓虹牌上的汉字是乱码（本次可见）。
   规避：镜头构图避开大面积可读文字，或后期贴真实字。
4. **H3 只吃英文**：中文进 `prompt_en` 效果差（不是慢一点，是明显更差）。
5. **同一 prompt 会被静默去重**：ComfyUI 的 prompt_id 由输入决定 → seed 必须每镜递增。
6. **本机没有 ffprobe**：`ffmpeg -i` 解析 stderr 拿时长/分辨率/帧率。
7. **显存是唯一硬瓶颈**：本地 LLM（13–17GB）与 H3（≈20GB）不能同驻，流水线阶段必须串行；渲染期间别开别的 GPU 任务。

## 6. 目录约定

```
duanju/D202611_001/
├── story.json      # P1 剧本（中英双轨）
├── shots.json      # P3 分镜（H3 提示词）
├── cast/           # P2 定妆：<id>_candN.png + <id>.png(主) + <id>_alt.png(备) + selection.json
├── clips/          # P4 每镜一个 mp4（01..NN）
├── episode.mp4     # P5 成片 1920×1056/24fps/带字幕
├── subtitles.srt   # P5 字幕
├── report.json     # P6 质检报告
└── qc/             # 抽帧与 contact sheet（人工审片证据）
```

## 7. 规模化怎么排（往 12 镜一集走）

- 渲染是瓶颈：**12 镜 ≈ 42 分钟**。单卡串行，加机器没用（只有 1 张卡）。
- 提速选项：草稿档（i2v-draft，103 s/镜）看构图，锁定后再用 bypass 档出片——**草稿不能直接进成片**。
- 一集一集排（阶段串行），不要并行开两集（显存不够）。
- 下一步（未做）：本地 TTS 配音 + 对轨（方案 P2）；H3 音轨与 latent 帧数绑定，插帧不会延长声音。
