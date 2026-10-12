# -*- coding: utf-8 -*-
"""历史会话查看器 — 读取 .codex 会话记录与 DSH 会话档案 (只读预览)

数据源:
  1. ~/.codex/sessions/rollout-*.jsonl          (Codex 明文 JSONL)
  2. ~/.dsh/sessions/<workspace>/<session>/session.jsonl.zstd (DSH zstd 会话)
返回统一结构: [{source, file, title, mtime, size_mb, messages}]
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

CODEX_DIR = Path.home() / ".codex" / "sessions"
DSH_DIR = Path.home() / ".dsh" / "sessions"

_TEXT_KEYS = ("text", "content", "message", "payload", "data")
_ROLE_KEYS = ("role", "type", "kind")


def _extract_text(obj) -> str:
    """从任意 JSON 节点提取文本片段 (递归, 有界)。"""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        parts = [_extract_text(x) for x in obj[:8]]
        return " ".join(p for p in parts if p)
    if isinstance(obj, dict):
        # 直接文本字段优先
        for k in ("text", "content_text", "message", "reasoning"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v
        # 常见嵌套: content: [...], message: {...}
        for k in ("content", "message", "payload"):
            v = obj.get(k)
            if isinstance(v, (list, dict)):
                t = _extract_text(v)
                if t:
                    return t
        # 兜底: 全部值首字符取短
        for k, v in obj.items():
            if k in ("timestamp", "id", "session_id", "seq"):
                continue
            t = _extract_text(v)
            if t:
                return t
    return ""


def _role_of(obj) -> str:
    if isinstance(obj, dict):
        for k in ("role", "type", "kind"):
            v = obj.get(k)
            if isinstance(v, str):
                v = v.lower()
                if "user" in v or "human" in v:
                    return "user"
                if "assistant" in v or "agent" in v or "ai" in v:
                    return "assistant"
                if "tool" in v or "function" in v:
                    return "tool"
        return _role_of(obj.get("message", obj.get("payload", "")))
    return ""


def parse_codex_jsonl(path: Path, max_messages: int = 30) -> list[dict]:
    """解析 codex rollout JSONL, 返回 [{role, text}]"""
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                role = _role_of(o)
                if role not in ("user", "assistant"):
                    continue
                txt = _extract_text(o)
                if txt:
                    out.append({"role": role, "text": txt[:1200]})
                if len(out) >= max_messages:
                    break
    except Exception:
        pass
    return out


def parse_dsh_zstd(path: Path, max_messages: int = 30) -> list[dict]:
    """解析 DSH session.jsonl.zstd (zstandard 可选)。"""
    out = []
    try:
        import zstandard
        with open(path, "rb") as f:
            data = zstandard.ZstdDecompressor().stream_reader(f).read()
        for line in data.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            if t == "user/message":
                content = o.get("data", {}).get("content")
                txt = _extract_text(content) if isinstance(content, (list, dict)) else str(content or "")
                if txt:
                    out.append({"role": "user", "text": txt[:1200]})
            elif t == "assistant/message":
                msg = o.get("data", {}).get("message", {})
                txt = _extract_text(msg.get("content"))
                if txt:
                    out.append({"role": "assistant", "text": txt[:1200]})
            if len(out) >= max_messages:
                break
    except Exception:
        pass
    return out


_SESS_CACHE: dict = {"key": None, "at": 0.0, "rows": None}
_SESS_TTL = 5.0          # 秒; 每次重建页面都重新扫全树不值得
_SESS_LIMIT = 400        # 条数上限: 几百行塞 Listbox 也是成本


def _dirs_fingerprint() -> tuple:
    out = []
    for d in (CODEX_DIR, DSH_DIR):
        try:
            st = d.stat()
            out.append((st.st_mtime_ns, st.st_size))
        except Exception:
            out.append(None)
    return tuple(out)


def list_sessions(use_cache: bool = True, limit: int = _SESS_LIMIT) -> list[dict]:
    """列出全部可查看会话 (按修改时间倒序)。

    性能坑 (2026-09-11, 由主线程看门狗抓出):
        原实现用 `DSH_DIR.rglob("session.jsonl.zstd")` 做**全树递归**, 然后再对每个
        结果 `p.stat()` 两次。~/.dsh/sessions 下实测 497 个会话, 暖盘 112ms、
        冷盘 0.5~1.4s。而设置页在**页面构建时**用 `after(300, ...)` 在主线程直接调它
        —— 哪怕设置页压根没显示, 启动后主线程也会被摁住半秒到一秒多
        (看门狗实测停滞 540 / 956 / 1367 ms)。这才是"启动后卡一下"的真凶。
    现在:
        ① 深度受控的显式两层 glob (`*/*/session.jsonl.zstd`) —— 实测与 rglob
           结果完全一致 (497 条), 112ms → 10ms;
        ② 每个条目只 stat 一次 (原来是 mtime/size 各 stat 一遍);
        ③ 目录指纹 + 短 TTL 缓存, 主题切换重建页面不重复扫;
        ④ 条数上限, 不把几百行一次性塞进 Listbox。
    调用方还有一条义务: **别在主线程里调** (见 settings_page._sess_load 的后台线程)。
    """
    now = time.monotonic()
    key = _dirs_fingerprint()
    if use_cache and _SESS_CACHE["rows"] is not None:
        if _SESS_CACHE["key"] == key and (now - _SESS_CACHE["at"]) < _SESS_TTL:
            return list(_SESS_CACHE["rows"])

    rows = []
    if CODEX_DIR.exists():
        for p in CODEX_DIR.glob("*.jsonl"):
            try:
                st = p.stat()
            except Exception:
                continue
            rows.append({"source": "codex", "file": str(p), "title": p.stem[:60],
                         "_ts": st.st_mtime, "size_mb": round(st.st_size / 1e6, 1)})
    if DSH_DIR.exists():
        # 定深 glob 取代 rglob: 目录结构固定为 <sessions>/<项目>/<会话>/session.jsonl.zstd
        for p in DSH_DIR.glob("*/*/session.jsonl.zstd"):
            try:
                st = p.stat()
            except Exception:
                continue
            rows.append({"source": "dsh", "file": str(p),
                         "title": p.parent.parent.name.replace("--C-Users-1--", "root").replace("--", " "),
                         "_ts": st.st_mtime, "size_mb": round(st.st_size / 1e6, 1)})
    rows.sort(key=lambda r: r["_ts"], reverse=True)
    if limit and len(rows) > limit:
        rows = rows[:limit]
    for r in rows:
        r["mtime"] = datetime.fromtimestamp(r.pop("_ts")).strftime("%m-%d %H:%M")

    _SESS_CACHE.update(key=key, at=now, rows=list(rows))
    return rows


def preview(path: str, max_messages: int = 30) -> list[dict]:
    """按来源解析并返回会话消息预览。"""
    p = Path(path)
    if p.suffix == ".jsonl":
        return parse_codex_jsonl(p, max_messages)
    return parse_dsh_zstd(p, max_messages)


if __name__ == "__main__":
    import sys
    rows = list_sessions()
    print(f"共 {len(rows)} 个会话记录:")
    for r in rows[:15]:
        print(f"  [{r['source']}] {r['mtime']} {r['size_mb']}MB {r['title'][:40]}")
