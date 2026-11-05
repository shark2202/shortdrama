---
name: shortdrama-production
description: 在本机（RTX 2080Ti 22G + ComfyUI :8188 + MiniMax-H3 W4A8 + Z-Image）用 pi-agent 当编剧、ComfyUI 出画出片，一句话题材产出短剧成片（剧本/定妆图/分镜/成片 MP4/质检报告），并沉淀 SOP 与知识资产。触发词：短剧、出片、剧本→成片、shot、episode.mp4、Z-Image 定妆、H3 出镜、跑一集短剧、任务看板、sd board。
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

## 七阶段（每阶段可单独重跑）

```bash
EP=/home/a/ai-work/duanju/<episode-id>      # 一集 = 一个目录
EPY=/home/a/ai-work/.venv/bin/python
SCRIPT=/home/a/ai-work/workspace/shortdrama/shortdrama/episode.py

$EPY $SCRIPT $EP lint      # 剧本预检（毁集级错误在这里挡下，秒级）
#   ↓ P2 台词：代码选镜、LLM 只写字（合并不覆盖）；只改文本 → 不用重渲
$EPY /home/a/ai-work/workspace/shortdrama/tools/add_dialogue.py --root /home/a/ai-work/duanju --episode <E01>
$EPY $SCRIPT $EP cast      # 出定妆候选图 → $EP/cast/<id>_cand{1,2,3}.png
#   ↓ agent 看图挑人（read 工具直接看 PNG），写 $EP/cast/selection.json
$EPY $SCRIPT $EP pick      # → $EP/cast/<id>.png（主）+ <id>_alt.png（备）
$EPY $SCRIPT $EP shots     # story.json → shots.json（自动加 <Picture 1> 前缀）
$EPY $SCRIPT $EP frames    # 每镜 1 张关键帧图（10s/镜）→ $EP/frames/NN.png
#   ↓ agent 看 $EP/qc/keyframes_sheet.png 审片：构图/人物/光线对不对
$EPY $SCRIPT $EP render    # 逐镜出镜 → $EP/clips/NN.mp4（约 210s/镜，单镜粒度续跑）
$EPY $SCRIPT $EP review    # 关键帧拼图 + 视频抽帧拼图 + clips/manifest.json（一屏审片）
$EPY $SCRIPT $EP assemble  # 拼接→3x 放大→烧字幕+BGM+响度/亮度 → $EP/episode.mp4
$EPY $SCRIPT $EP qc        # 黑帧/静音/削波/时长/分辨率 → $EP/report.json（阻断项退出码非 0）
```

## agent 在每一步该干什么

1. **写剧本**（P1）：按 `workspace/shortdrama/prompts/story.md` 的 schema 写 `$EP/story.json`。
   中英双轨；`prompt_en` 必须是英文；每镜一个 `ref` 角色；每镜加 `prompt_cn`（中文画面描述，供关键帧用）。
2. **预检**：`lint`（秒级）——毁集级的错（中文 prompt_en、角色引用错、镜数不符）在这里挡下。
3. **补台词**（P2）：`tools/add_dialogue.py`。**选镜在代码、LLM 只写字、合并不覆盖**；
   覆盖率 <15% 的集要返工。台词是短剧的主信息通道，不是装饰（实测初版 105 镜只有 17 条对白）。
4. **出定妆图**（P3）：跑 `cast`，然后**用 read 工具逐张看候选图**再挑：
   主图 = 脸最正、最清楚、五官不被遮挡的一张；备图 = 同一张脸但角度略不同。
   把选择写进 `cast/selection.json` 再跑 `pick`。
5. **分镜**（P4）：`shots` 阶段自动拼句式，不要手改 shots.json。
6. **关键帧**（P4）：`frames`（10s/镜）→ 看 `qc/keyframes_sheet.png`：
   **构图/人物/光线不对，现在改是 10s，等出完视频再改是 210s**。
7. **出镜**（P5）：`render` 直接后台跑（35 镜 ≈ 2 小时），失败自动换 seed 重试。
   要构图完全可控就换 `render-i2v`（关键帧当首帧，代价是运镜自由度下降）。
8. **审片**（P5）：`review` → `qc/review.md`（**文字审核单**：每镜一行 + 结论列）+ `qc/frames_sheet.png` + `clips/manifest.json`；废镜在这里重出。
   **TUI 里人看不到图**：让 agent 看图并把结论写进 `review.md` 的「结论」列，人只拍板；
   人自己要看图用 `python3 shortdrama/tview.py <job>/qc/frames_sheet.png`（终端 ANSI 彩色块）
   或 `./sd studio <剧集根>`（浏览器：看板 + 审片台，图+视频）。
9. **整集**（P6）：`assemble`（中间件会自动复用，重出 1 镜不必重拼重放大）。
10. **质检**（P7）：`qc` + **自己看片**，把新坑写进 SOP。

## 任务看板（Web 模式：阶段卡片 + 一键触发）

一集渲染 ≈ 2 小时，人守着黑终端判断不了"是没在跑还是卡住了"。看板解决这件事：
**卡片状态 = 产物在不在**，网页只做投影与触发，不执行 GPU/ffmpeg 动作（方案 §4.3 / D18）。

```bash
./sd board /home/a/ai-work/duanju --episode E01            # 看这一集的阶段卡片
./sd board /home/a/ai-work/duanju --episode E01 --run render # 触发一个阶段（后台跑）
./sd board /home/a/ai-work/duanju --episode E01 --stop render
./sd studio /home/a/ai-work/duanju --port 8095               # 网页：任务看板 + 审片台
```

- 「运行中」= `logs/stage-<key>.pid` 里的 pid 还活着；输出在 `logs/stage-<key>.log`（心跳）。
- 执行者两种：`cli`（本地命令跑确定性阶段）/ `agent`（起 `pi --print --mode json` 无头 session，
  把任务卡当提示词交给 agent 内核跑内容决策阶段）。
- **看板不存状态**：一切回读 job 目录（文件即状态）。

## 无图环境（SSH/TUI）怎么评审

专家的循环是 **假设 → 取证 → 归因 → 取舍**，只有"取证"需要眼睛。把取证自动化，
人在 TUI 里只做归因与取舍：

```bash
python3 shortdrama/evidence.py <job>     # 证据表：qc/evidence.md（35 镜 ≈ 4 秒）
```

每镜给出：亮度 / 饱和 / 静止秒数 / 关键帧vs成片 PSNR / 与上一镜相似度，并排出
"**先看这 N 镜**"（亮度跳变、过暗过曝 > 重复镜、画面静止 > 关键帧可疑）。

每轮只做三件事：① qc 报的阻断项 ② 证据表排前 N 镜 ③ 随机抽 3 镜（防只挑有问题的看）。
拍板用最小动作集：通过 / 重出 / 改词 / 降级 → 写回 `review.json`。
**别用不可信的证据下结论**：证据表里"关键帧 vs 成片 PSNR 中位 12 dB"意味着
关键帧整体不能代表成片（实跑用它误判了 29 镜）。细节见 SOP §8.5。
## 踩过的坑（别再踩）
- **字幕：相邻重复是噪音，隔得远的重复是回环**。同一句在 ≤2 镜内重复 → 成片只烧一次；
  隔 ≥3 镜再出现 → **必须留**（E01 的"你该回来了。"在 s11/s23 是同一场戏演两遍，第二次才有分量）。
  **别做全局去重**，会抹掉回环设计。
- **关键帧先行**：出图 10s/张 vs 出镜 210s/镜。6 镜关键帧 70 秒就能判断整集观感，
  实测质量与 ref2va 成片相当 → 出镜前先出关键帧 + 审片。
- **提示词 5 条纪律**（三集 105 镜实测，见 SOP §2.7）：
  ① 唇语/口型镜必错 → 改写"正在说话，嘴唇张开"；
  ② 闪回/抽象/倒影镜必错 → 换成具体可见的物件状态；
  ③ 画面出现第二个人 → 身份必漂移（t2i 无参考图）→ 写死第二人的辨识特征或改单人镜；
  ④ 否定约束无效 → 只写存在什么，别写"空无一人"；
  ⑤ **物件驱动的镜头最可靠** → 剧本骨架挂在物件上（照片/钥匙/箭头/时间戳），不挂情绪。
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
  `loudnorm + alimiter`）。镜头级削波记 warn，成片级削波才阻断。
- **响度链三个坑（三集实测，都会静默出错）**：
  ① 限幅上限要 **-2.0 dBFS**（`limit=0.794`）：AAC 编码过冲 +0.5~0.6 dB，限到 -1.0 → 成片 -0.6 直接阻断；
  ② 限幅 **attack=0.1ms**：H3 音轨有 ~4ms 瞬态突发，attack=1ms 时突发头部样本按原电平通过，
  AAC 重构越过 0 dBFS → 成片里出现被削平的样本（volumedetect 报 max_volume 0.0 dB）；
  ③ **逐镜归一**（`loudnorm=I=-20`）再做整片两遍 loudnorm：整片 LRA 从 20.8 降到 7.4 LU。
  三个参数缺一个，qc 都会报"峰值超标"。
- **BGM 混音必须走 `-filter_complex`**：`-af` 是单输入链，装不下第二路（`amix` 在 `-af` 里会丢一路）。
  `amix` 要 `normalize=0`，否则两路等比缩放，音乐盖过环境音。
- **亮度测量会被日志级别吞掉**：`metadata=print` 是 info 级日志，`-loglevel error` 会把它扔掉 → 亮度测出 0。
  亮度均值要除以**样本数**（不是样本数×段长）。三集平均亮度差 25% → 统一到 YAVG 90。
- **扩散模型写不好招牌文字**：霓虹牌上的汉字是乱码。构图避开大面积可读文字，或后期贴真字。
- **本机没有 ffprobe**：`episode.py` 用 `ffmpeg -i` 解析 stderr 拿时长/分辨率/帧率。
- **显存是唯一硬瓶颈**：本地 LLM（13–17GB）和 H3（≈20GB）不能同时驻留，
  流水线必须阶段串行；渲染期间别启别的 GPU 任务。

## 知识资产沉淀（每次跑完都要做）

- 剧本/分镜/定妆图/成片 → 留在 `$EP`（目录即状态）。
- 新踩的坑 → 追加到本 SKILL 的"踩过的坑"。
- 流程有变动 → 同步 `workspace/shortdrama/docs/SOP-短剧生产.md` 与 `workspace/shortdrama/README.md`。
- 平台能力有变动（新增看板/阶段/接口）→ 同步 `workspace/shortdrama/docs/自动化短剧平台建设方案.html`
  （方案是设计权威源：§4.4 任务卡、§6 决策、§12 里程碑状态、§15.8 落点表）。

## 参考实践

- **D202611_001《凌晨三点的便利店》**（`/home/a/ai-work/duanju/D202611_001/`）：
  6 镜 / 31 s / 2 角色，全链路跑通。实测：定妆 10 s/张、出镜 210 s/镜、整集 ≈ 25 min；
  质检通过（2 条提示：源镜头削波）。含剧本、抽帧 contact sheet、成片。
