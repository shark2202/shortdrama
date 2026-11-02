"""人物造型检查：挡住"大众脸"和"性别/年龄丢失"。

短视频里观众记不住剧情，只记住脸。AI 出图默认给一张标准化的"AI 美女脸"，
对称、无记忆点 —— 这就是"大众脸"。定妆图是全剧人物一致性的天花板
（方案 §9.2），所以 desc_cn 必须在**造型层**具体到可画：脸型、不对称特征、
年龄痕迹、具体衣着、职业物件。

更要命的是**身份锚点**：不给性别/年龄/种族，模型会自己填，而默认填男性。
实测：三个角色（两女一男）全部出成男性 —— 所以性别/年龄/人种是硬性要求。

只检查文本，不做图像判断 —— 图像质量交给人眼看（cast/selection.json 是人挑的）。
"""

from __future__ import annotations

import re

# 空泛词：出现即说明没在写造型，模型会退回默认网红脸
VAGUE_TERMS = ["漂亮", "美丽", "帅哥", "美女", "网红", "大众脸", "五官精致",
               "气质出众", "阳光帅气", "温柔", "好看", "清秀", "英俊", "甜美",
               "儒雅", "气质好", "长得好看"]

# 身份锚点：性别 / 年龄 / 人种 —— 缺一个模型就自己填（默认填男性）
GENDER_TOKENS = ["女", "男"]
AGE_TOKENS = ["岁", "中年", "青年", "老年", "少女", "少年", "儿童"]
ETHNIC_TOKENS = ["亚洲", "中国人", "东亚", "白人", "黑人", "混血", "黄种"]

# 造型具体度标记
FACE_SHAPE = ["方脸", "长脸", "圆脸", "心形脸", "菱形脸", "鹅蛋脸", "脸型", "下颌",
              "颧骨", "下颌线", "下颌角", "脸型偏", "长颌", "尖下巴", "宽下颌"]
HAIR = ["刘海", "发尾", "发丝", "银丝", "白发", "马尾", "寸头", "卷发", "短发", "长发",
        "盘发", "辫", "鬓", "发缝", "中分", "偏分", "碎发", "发际"]
ASYMMETRY = ["不对称", "单侧", "左眉", "右眉", "一高一低", "痣", "疤", "疤印", "酒窝",
             "龅牙", "歪", "豁牙", "褶"]
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
        problems.append(f"造型描述偏短（{len(desc)} 字，<30）：建议再补细节撑住定妆图")

    hits = [t for t in VAGUE_TERMS if t in desc]
    if hits:
        problems.append(f"出现空泛词 {hits}：这类词会让模型出标准化的网红脸，要换成具体描述")

    if not any(k in desc for k in GENDER_TOKENS):
        problems.append("没有性别（女/男）：模型会自己填，默认填男性——实测会出成男的")
    if not any(k in desc for k in AGE_TOKENS):
        problems.append("没有年龄（X岁/中年/青年…）：年龄决定脸，缺了模型给不出年龄感")
    if not any(k in desc for k in ETHNIC_TOKENS):
        problems.append("没有种族/人种（亚洲/东亚…）：中文出图必带人种锚点")

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

def check_character(ch: dict) -> list[str]:
    """检查一个角色：身份锚点（identity_cn）+ 造型（desc_cn）。"""
    ident = (ch.get("identity_cn") or "").strip()
    desc = (ch.get("desc_cn") or "").strip()
    out: list[str] = []
    if not ident:
        out.append("缺 identity_cn（性别+年龄+人种）：不给性别模型默认出男性")
    else:
        if not any(k in ident for k in GENDER_TOKENS):
            out.append(f"identity_cn 没有性别：{ident!r}")
        if not any(k in ident for k in AGE_TOKENS):
            out.append(f"identity_cn 没有年龄：{ident!r}")
        if not any(k in ident for k in ETHNIC_TOKENS):
            out.append(f"identity_cn 没有人种：{ident!r}")
    # 造型维度看 desc_cn；身份维度已经被 identity_cn 覆盖，不重复报
    problems = check(desc)
    out += [p for p in problems if not p.startswith(("没有性别", "没有年龄", "没有种族"))]
    return out


def check_characters(characters: list[dict]) -> list[str]:
    out = []
    for c in characters:
        cid = c.get("id", "?")
        for p in check_character(c):
            out.append(f"{cid} {c.get('name', '')}：{p}")
    return out


def cast_prompt(ch: dict, story: dict) -> str:
    """定妆图提示词 = 身份 + 造型 + 定妆后缀（不含风格词）。"""
    parts = [ch.get("identity_cn", ""), ch.get("desc_cn", ""),
             story.get("cast_prompt_cn", "")]
    return "，".join(p.strip("，。 ") for p in parts if p and p.strip())


def style_pollution(story: dict) -> list[str]:
    """定妆提示词不能混入风格词——霓虹/低照度/胶片颗粒会染脏参考图的光。"""
    suffix = story.get("cast_prompt_cn", "")
    bad = [w for w in ("霓虹", "洋红", "低照度", "胶片颗粒", "冷调", "青蓝", "浅景深")
           if w in suffix]
    out = []
    if bad:
        out.append(f"cast_prompt_cn 混入风格词 {bad}：定妆图要中性光，否则出网红脸且污染参考图")
    return out
