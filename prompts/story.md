# 剧本模板（pi-agent 当编剧）

> 输入：题材一句话 + 风格 + 时长 + 镜数。
> 输出：**严格的 JSON**（下面这份 schema），落盘为 `<job>/story.json`。
> 校验不过就带着报错信息重写（≤2 次）；仍不过 → 人工兜底。

## 硬约束

1. **中英双轨**：`beat` / `subtitle_cn` / `sfx` / `desc_cn` / `prompt_cn` 用中文（给人看），
   `prompt_en` / `sfx_en` / `style_en` 用英文（喂 H3，它的文本编码器是英文的）。
   **中文不许进 H3 提示词**，效果差。
2. **镜数与时长对齐**：`shot_count` 镜 × `duration_s` ≈ `duration_s` 总时长。
   H3 每镜固定 124 帧 @24fps = 5.17s，所以默认 `duration_s: 5.2`。
3. **英文提示词三件套**：`prompt_en` 必须同时写清
   **动作** + **镜头运动/景别** + **光线**；环境音写进 `sfx_en`（H3 靠它出声）。
4. **每镜一个主角**：`ref` 指这一镜用谁的定妆图当参考（跨镜人脸锁靠它）；
   镜内无人（物镜/空镜）时 `ref` 写 `null`，走 t2v 路线。
5. **定妆描述不能挡脸**：`desc_cn` 里不要写"帽檐压低遮住眼睛""蒙面""背对镜头"这类
   遮住五官的描述——Z-Image 会照着画出黑眼罩/黑纱，定妆图直接废掉。
6. **每镜加 `prompt_cn`**：中文画面描述（景别 + 构图 + 光线）。
   它是"关键帧先行"那一步的输入——出镜前 10s 一张图就能审片，改图比改视频便宜 20 倍。

## schema

```json
{
  "title": "凌晨三点的便利店",
  "genre": "都市悬疑",
  "style_cn": "电影感，浅景深，冷调青蓝加霓虹洋红，低照度，胶片颗粒",
  "style_en": "cinematic, shallow depth of field, cold teal and neon magenta palette, low-key lighting, film grain",
  "duration_s": 31,
  "shot_count": 6,
  "cast_prompt_cn": "人物定妆照，胸部以上，正面，中性柔光，纯灰背景，高细节，电影感",
  "characters": [
    {"id": "c1", "name": "苏晚", "desc_cn": "亚洲年轻女性，24岁，黑色长直发中分，淡妆，米色针织衫配深色围裙，神情疲惫", "voice": "女声偏低"}
  ],
  "scenes": [
    {
      "id": "s1", "idx": 1,
      "chars": ["c1"], "ref": "c1",
      "beat": "开场：深夜空荡的便利店，雨打玻璃，收银台后的女主在擦杯子",
      "prompt_cn": "深夜空荡的便利店内部，货架纵深排列，雨点打在玻璃窗上，收银台后一名年轻女店员低头擦拭玻璃杯，冷青色顶光，玻璃反光，广角镜头",
      "duration_s": 5.2,
      "shot_type": "wide",
      "prompt_en": "A young woman stands behind the counter of an empty convenience store at night, slowly wiping a glass with a cloth, viewed through a rain-streaked window, slow dolly in, cold teal light from the ceiling panels",
      "sfx": "雨打窗、冰箱低鸣",
      "sfx_en": "steady rain on the window and a low refrigerator hum",
      "dialogue_cn": "整座城市只剩这一家还亮着灯。",
      "subtitle_cn": "整座城市只剩这一家还亮着灯。"
    }
  ],
  "ending": "悬念收尾，不揭示照片真相"
}
```

## 提示词模板（给 pi-agent 的任务描述）

```text
你是短剧编剧。题材：{题材}；风格：{风格}；总时长 {时长}s；{镜数} 个镜头；{角色数} 个角色。
按下面这份 JSON schema 写一集短剧，**只输出 JSON**，不要任何解释文字。

要求：
- 前 3 秒抓人，最后一镜留钩子（悬念/反转/情绪爆点），中间是递进。
- 每镜一个明确的动作 + 一个镜头运动（push in / dolly in / static / handheld slight shake…）+ 一句光线描述。
- prompt_en 全英文，sfx_en 全英文；中文字段只给人看。
- 每个角色都要有可复用的 desc_cn（发型/脸型/年龄/服装/气质），后面要拿它出定妆照。
- 字幕每句 ≤ 20 个汉字，念得出来。
```

## 验收

- `shot_count` == `len(scenes)`，镜头 idx 连续。
- 每镜 `prompt_en` 非空且含 `<Picture 1>` 引用（由 `episode.py shots` 阶段自动加前缀）。
- 每个角色 `desc_cn` 非空且**不遮脸**。
