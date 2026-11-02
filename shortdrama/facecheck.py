"""人物造型检查：挡住"大众脸"。

短视频里观众记不住剧情，只记住脸。AI 出图默认给一张标准化的"AI 美女脸"，
对称、无记忆点 —— 这就是"大众脸"。定妆图是全剧人物一致性的天花板
（方案 §9.2），所以 desc_cn 必须在**造型层**具体到可画：脸型、不对称特征、
年龄痕迹、具体衣着、职业物件。

只检查文本，不做图像判断 —— 图像质量交给人眼看（cast/selection.json 是人挑的）。
"""

from __future__ import annotations

import re

# 空泛词：出现即说明没在写造型，模型会退回默认网红脸
VAGUE_TERMS = ["漂亮", "美丽", "帅哥", "美女", "网红", "大众脸", "五官精致",
               "气质出众", "阳光帅气", "温柔", "好看", "清秀", "英俊", "甜美",
               "儒雅", "气质好", "长得好看"]

# 造型具体度标记：脸型/五官/不对称/年龄痕迹/衣着/物件
FACE_SHAPE = ["方脸", "长脸", "圆脸", "心形脸", "菱形脸", "鹅蛋脸", "脸型", "下颌",
              "颧骨", "下颌线", "下颌角", "脸型偏", "长颌", "尖下巴", "宽下颌"]
HAIR = ["刘海", "发尾", "发丝", "银丝", "白发", "马尾", "寸头", "卷发", "短发", "长发",
        "盘发", "辫", "鬓", "发缝", "中分", "偏分", "碎发", "发际"]
ASYMMETRY = ["不对称", "单侧", "左眉", "右眉", "一高一低", "痣", "疤", "疤印", "酒窝",
             "龅牙", "歪", "豁牙", "不对称", "褶"]
AGE_MARK = ["法令纹", "鱼尾纹", "抬头纹", "颈纹", "细纹", "皱纹", "粗糙", "干裂",
            "发际线", "白头发", "鬓角", "眼袋", "络腮胡", "胡茬", "毛孔"]
WARDROBE = ["围裙", "制服", "工服", "衬衫", "风衣", "针织衫", "T恤", "毛衣", "外套",
            "大衣", "夹克", "工牌", "挂绳", "眼镜", "镜框", "老花镜", "耳机", "帽"]
OBJECT = ["笔", "本", "杯", "手机", "钥匙", "包", "表", "绳", "牌", "袋", "伞", "烟",
          "工牌", "挂绳", "纸", "照片"]


def check(desc_cn: str) -> list[str]:
    """返回造型描述的问题清单（空 = 通过）。"""
    problems: list[str] = []
    desc = (desc_cn or "").strip()
    if not desc:
        return ["造型描述为空"]
    if len(desc) < 30:
        # 短不一定是坏：可能各维度都齐了只是写得精简。长度只在"短"时作为提示，
        # 不作为阻断项——维度缺失才是真问题。
        problems.append(f"造型描述偏短（{len(desc)} 字，<30）：建议再补细节撑住定妆图")

    hits = [t for t in VAGUE_TERMS if t in desc]
    if hits:
        problems.append(f"出现空泛词 {hits}：这类词会让模型出标准化的网红脸，要换成具体描述")

    if not any(k in desc for k in FACE_SHAPE):
        problems.append("没有脸型/骨相描述（方脸/下颌角/颧骨…），模型只能给默认脸型")
    if not any(k in desc for k in HAIR):
        problems.append("没有发型细节（刘海/中分/寸头/银丝…），发型是最强的辨识点")
    if not any(k in desc for k in ASYMMETRY):
        problems.append("没有不对称特征（痣/疤/单侧眉…）：对称脸就是大众脸的主要来源")
    if not any(k in desc for k in AGE_MARK):
        problems.append("没有年龄痕迹（法令纹/眼袋/胡茬/发际线…），年龄写数字等于没写")
    if not any(k in desc for k in WARDROBE):
        problems.append("没有衣着细节（款式+颜色，不是'穿着朴素'）")
    if not any(k in desc for k in OBJECT):
        problems.append("没有物件细节（工牌/挂绳/笔/本…），人物就没有生活质感")
    return problems


def check_characters(characters: list[dict]) -> list[str]:
    out = []
    for c in characters:
        cid = c.get("id", "?")
        for p in check(c.get("desc_cn", "")):
            out.append(f"{cid} {c.get('name', '')}：{p}")
    return out


def style_pollution(story: dict) -> list[str]:
    """定妆提示词不能混入风格词——霓虹/低照度/胶片颗粒会染脏参考图的光。"""
    suffix = story.get("cast_prompt_cn", "")
    bad = [w for w in ("霓虹", "洋红", "低照度", "胶片颗粒", "冷调", "青蓝", "浅景深")
           if w in suffix]
    out = []
    if bad:
        out.append(f"cast_prompt_cn 混入风格词 {bad}：定妆图要中性光，否则出网红脸且污染参考图")
    return out
