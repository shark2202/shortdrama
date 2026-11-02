"""资产清单 + 只读静态页：让"这个产物是哪次跑的"能被查到。

为什么不做一个"资产管理 Web 系统"：
本机是单人单机产线，产物天然落在文件里（job 目录 + ComfyUI/output）。
再引入数据库就会出现第二个真相源，和"文件即状态、断点续跑靠目录"的设计
冲突（方案 §8.3）。ComfyUI 0.33 自带 assets API（``--enable-assets``），
但它按 hash 记录模型/输入/输出，不带我们的 prompt/seed/tier 溯源。

所以这里的"资产管理"= 扫一次目录，产出两份东西：
  assets.json    机器可读清单（给脚本 / 给审片 / 给断点续跑比对 hash）
  _assets.html   只读静态页（给人看：图直接渲染，视频内嵌播放）

页面不写任何东西：挑图 / 审片的选择仍写回 job 目录里的文件，
保持"文件即状态"。样式沿用《ComfyUI 使用手册》的观感（纯标准库）。
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# workspace/shortdrama/shortdrama/assets.py -> workspace/
WORKSPACE = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = WORKSPACE / "ComfyUI" / "output"

KIND_BY_EXT = {
    ".mp4": "video", ".webm": "video", ".mov": "video", ".mkv": "video",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".gif": "image",
    ".wav": "audio", ".mp3": "audio", ".flac": "audio",
    ".json": "meta", ".srt": "text", ".vtt": "text", ".txt": "text", ".md": "text",
}
# 目录名 -> 展示名（产物按类型分目录时用）
DIR_LABEL = {
    "video": "视频", "image": "图片", "audio": "音频",
    "meta": "元数据", "text": "文本",
}
HASH_MAX_BYTES = 512 * 1024 * 1024   # 超过这个体积不自动算 sha256（权重动辄 20GB）


@dataclass
class Asset:
    path: str          # 相对 root
    name: str
    ext: str
    kind: str
    size: int
    mtime: float
    sha256: str | None = None
    sidecar: dict[str, Any] | None = None   # 同名 <file>.json 里的元数据


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sidecar(path: Path) -> dict[str, Any] | None:
    """产物旁挂 <file>.json = 该次运行的元数据（render.py 写什么这里就显示什么）。"""
    meta = path.with_name(path.name + ".json")
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else {"value": data}


def scan(root: Path | str = DEFAULT_ROOT, *, hash_all: bool = False) -> list[Asset]:
    """扫目录出一份资产清单（排序稳定，便于 diff）。"""
    root = Path(root).resolve()
    out: list[Asset] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("_"):
            continue
        ext = path.suffix.lower()
        if ext not in KIND_BY_EXT:
            continue
        st = path.stat()
        a = Asset(
            path=str(path.relative_to(root)),
            name=path.name,
            ext=ext,
            kind=KIND_BY_EXT[ext],
            size=st.st_size,
            mtime=st.st_mtime,
        )
        if hash_all or st.st_size <= HASH_MAX_BYTES:
            a.sha256 = _sha256(path)
        a.sidecar = _sidecar(path)
        out.append(a)
    return out


def inventory(root: Path | str = DEFAULT_ROOT, *, hash_all: bool = False) -> dict[str, Any]:
    assets = scan(root, hash_all=hash_all)
    by_kind: dict[str, int] = {}
    for a in assets:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
    return {
        "root": str(Path(root).resolve()),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(assets),
        "total_bytes": sum(a.size for a in assets),
        "by_kind": by_kind,
        "assets": [asdict(a) for a in assets],
    }


# ---------------------------------------------------------------- 静态页

_CSS = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#1b1f24;--muted:#5b6470;--line:#e3e6ea;
--accent:#2f6fed;--code-bg:#f2f4f7;--warn:#b26a00}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,"Segoe UI","Noto Sans CJK SC",sans-serif}
header{padding:18px 22px;background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
h1{margin:0 0 4px;font-size:18px}
.meta{color:var(--muted);font-size:13px}
.bar{margin-top:10px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input[type=search]{padding:6px 10px;border:1px solid var(--line);border-radius:6px;font-size:13px;min-width:260px}
button{padding:6px 12px;border:1px solid var(--line);background:#fff;border-radius:6px;cursor:pointer;font-size:13px}
button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px;padding:18px 22px 60px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.thumb{background:#0d0f12;aspect-ratio:16/9;display:flex;align-items:center;justify-content:center;overflow:hidden}
.thumb img,.thumb video{width:100%;height:100%;object-fit:contain;display:block}
.ph{color:#8b95a3;font-size:28px}
.body{padding:9px 11px;display:flex;flex-direction:column;gap:5px;flex:1}
.nm{font-weight:600;font-size:13px;word-break:break-all}
.sub{color:var(--muted);font-size:12px;display:flex;gap:8px;flex-wrap:wrap}
.tag{background:var(--code-bg);border-radius:4px;padding:1px 6px;font-size:11px}
details{margin-top:2px}
summary{cursor:pointer;color:var(--accent);font-size:12px}
pre{margin:6px 0 0;background:var(--code-bg);padding:8px;border-radius:6px;overflow:auto;font-size:11px;max-height:220px}
.hide{display:none}
"""

_JS = """
function q(s){return document.querySelector(s)}
let kind='';
function filt(){
  const t=q('#q').value.toLowerCase();
  let n=0;
  document.querySelectorAll('.card').forEach(c=>{
    const okT=!t||c.dataset.name.includes(t)||c.dataset.path.includes(t);
    const okK=!kind||c.dataset.kind===kind;
    c.classList.toggle('hide',!(okT&&okK)); if(okT&&okK)n++;
  });
  q('#shown').textContent=n;
}
q('#q').addEventListener('input',filt);
document.querySelectorAll('#kind button').forEach(b=>b.addEventListener('click',()=>{
  const v=b.dataset.v;
  const on=(kind===v)?'' : v;
  kind=on;
  document.querySelectorAll('#kind button').forEach(x=>x.classList.toggle('on',x.dataset.v===on));
  filt();
}));
filt();
"""


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n}"


def _sidecar_html(meta: dict[str, Any]) -> str:
    rows = []
    for k, v in meta.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        rows.append(f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>")
    return ("<details><summary>运行元数据</summary><table>"
            + "".join(rows) + "</table></details>")


def render_html(assets: list[Asset], *, root: Path | str, out_dir: Path | str,
                title: str = "资产清单") -> str:
    """生成只读静态页。产物用相对路径引用，因此页面要跟 root 一起被服务。"""
    root = Path(root).resolve()
    out_dir = Path(out_dir).resolve()
    kinds = sorted({a.kind for a in assets})
    by_kind = {k: sum(1 for a in assets if a.kind == k) for k in kinds}

    cards = []
    for a in assets:
        rel = os.path.relpath(root / a.path, out_dir).replace(os.sep, "/")
        if a.kind == "image":
            media = f'<img src="{html.escape(rel)}" alt="{html.escape(a.name)}" loading="lazy">'
        elif a.kind == "video":
            media = (f'<video src="{html.escape(rel)}" controls preload="metadata" '
                     f'onclick="this.play()"></video>')
        elif a.kind == "audio":
            media = f'<audio src="{html.escape(rel)}" controls></audio>'
        else:
            icon = {"meta": "{}", "text": "≡"}.get(a.kind, "·")
            media = f'<div class="ph">{icon}</div>'
        tags = [f'<span class="tag">{DIR_LABEL.get(a.kind, a.kind)}</span>',
                f'<span class="tag">{_human(a.size)}</span>',
                f'<span class="tag">{time.strftime("%m-%d %H:%M", time.localtime(a.mtime))}</span>']
        if a.sha256:
            tags.append('<span class="tag">sha256</span>')
        side = _sidecar_html(a.sidecar) if a.sidecar else ""
        cards.append(
            f'<div class="card" data-kind="{a.kind}" data-name="{html.escape(a.name.lower())}" '
            f'data-path="{html.escape(a.path.lower())}">'
            f'<div class="thumb">{media}</div>'
            f'<div class="body"><div class="nm">{html.escape(a.name)}</div>'
            f'<div class="sub">{"".join(tags)}</div>{side}</div></div>'
        )

    total = sum(a.size for a in assets)
    kind_btns = "".join(
        f'<button data-v="{k}">{DIR_LABEL.get(k, k)} ({by_kind[k]})</button>' for k in kinds)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{_CSS}</style></head>
<body>
<header>
<h1>{html.escape(title)}</h1>
<div class="meta">共 {len(assets)} 个产物 · {_human(total)} · 根目录 <code>{html.escape(str(root))}</code> ·
只读视图（选择结果仍写回 job 目录的文件）</div>
<div class="bar">
  <input id="q" type="search" placeholder="按文件名 / 路径过滤…">
  <span id="kind" style="display:flex;gap:6px;flex-wrap:wrap">{kind_btns}</span>
  <span class="meta">显示 <b id="shown">{len(assets)}</b> 个</span>
</div>
</header>
<main>{"".join(cards)}</main>
<script>
{_JS}
</script>
</body></html>"""


def write_html(root: Path | str = DEFAULT_ROOT, *, out: Path | str | None = None,
               hash_all: bool = False, title: str = "资产清单") -> Path:
    """扫描并写出静态页；默认落在 root 里（这样相对路径引用才成立）。"""
    root = Path(root).resolve()
    out = Path(out).resolve() if out else root / "_assets.html"
    assets = scan(root, hash_all=hash_all)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(assets, root=root, out_dir=out.parent, title=title), encoding="utf-8")
    return out
