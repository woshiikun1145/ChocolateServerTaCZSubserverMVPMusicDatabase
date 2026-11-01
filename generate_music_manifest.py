#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_index.py —— 自动生成 / 增量更新 index_file.json

规则：
  - 递归扫描，找到所有含音频文件的最末级目录
  - 已存在的条目（按 path.root 匹配）：
      * hash 字段除外：总是重新计算并覆盖
      * 其他字段：旧值非空（且不是占位符 ####）就保留，控制台提示"保留"
      * 缺失字段：用新值补充，控制台提示"补充"
  - 扫描不到的旧条目：原样保留并警告，避免丢失手填信息
  - 新条目：music_name / author 用 #### 占位

依赖：pip install mmh3
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

try:
    import mmh3
except ImportError:
    sys.exit("缺少依赖，请先运行：pip install mmh3")

# ========================= 配置区 =========================
SCRIPT_DIR = Path(__file__).resolve().parent

MUSICS_SUBDIR = SCRIPT_DIR / "musics"
SCAN_DIR = MUSICS_SUBDIR if MUSICS_SUBDIR.is_dir() else SCRIPT_DIR

OUTPUT_PATH = SCRIPT_DIR / "index_file.json"

SERVER = "mc.zgwl.eu.org"
ROOT_PREFIX = "/"
FORMAT_VERSION = 1
PLACEHOLDER = "####"          # 占位符；合并时视为"空"，不当已有值

AUDIO_EXTS = {".mp3", ".ogg", ".wav", ".flac", ".m4a"}
ICON_EXTS  = {".png", ".jpg", ".jpeg", ".webp"}

# hash 字段写法：
#   False -> 写入真实 MurmurHash3_x86_32 哈希值（推荐）
#   True  -> 只写算法名字符串 "MurmurHash3_x86_32"
WRITE_ALGORITHM_NAME_ONLY = False
HASH_ALGORITHM = "MurmurHash3_x86_32"

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".idea", ".vscode"}
# =========================================================


def murmur_hash_file(path: Path) -> str:
    data = path.read_bytes()
    h = mmh3.hash(data, seed=0, signed=False)
    return f"{h:08x}"


def find_audio(folder: Path) -> Path | None:
    candidates = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )
    if not candidates:
        return None
    if len(candidates) > 1:
        print(f"[警告] {folder}: 发现多个音频，使用 {candidates[0].name}")
    return candidates[0]


def find_icon(folder: Path) -> Path | None:
    """任意图片都算封面；优先 icon.*，其次 cover.*，再次按名称排序取第一个。"""
    images = [
        p for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower() in ICON_EXTS
    ]
    if not images:
        return None
    for p in images:
        if p.stem.lower() == "icon":
            return p
    for p in images:
        if p.stem.lower() == "cover":
            return p
    if len(images) > 1:
        print(f"[警告] {folder}: 发现多个图片，使用 {images[0].name}")
    return images[0]


def walk_all_dirs(root: Path):
    for p in sorted(root.iterdir(), key=lambda x: x.name):
        if not p.is_dir() or p.name in SKIP_DIRS:
            continue
        yield p
        yield from walk_all_dirs(p)


def collect_music_dirs(root: Path) -> list[Path]:
    return [d for d in walk_all_dirs(root) if find_audio(d) is not None]


def load_old_index(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[警告] 读取旧索引失败：{e}，按全新生成处理")
        return {}
    result: dict[str, dict] = {}
    for item in data.get("musics", []):
        root = (item.get("path") or {}).get("root")
        if root:
            result[root] = item
    return result


def _is_empty(v) -> bool:
    """None、空字符串、占位符都视为"空"。"""
    return v is None or v == "" or v == PLACEHOLDER


def merge_dict(old: dict, fresh: dict, label: str, prefix: str = "") -> dict:
    """
    把 fresh 合并进 old：
      - hash：总是用 fresh 覆盖，并提示"更新"
      - 其他 dict：递归合并
      - 标量：old 非空则保留（提示"保留"），否则用 fresh（提示"补充"）
      - old 中 fresh 没有的字段：原样保留
    """
    result: dict = {}
    for key, new_val in fresh.items():
        path_key = f"{prefix}{key}"
        old_val = old.get(key)

        if key == "hash":
            if old_val != new_val:
                print(f"[更新] {label}  {path_key}: {old_val} -> {new_val}")
            result[key] = new_val
            continue

        if isinstance(new_val, dict):
            result[key] = merge_dict(old_val or {}, new_val, label, prefix=f"{path_key}.")
            continue

        if not _is_empty(old_val):
            if old_val != new_val:
                print(f"[保留] {label}  {path_key}: 使用已有 {old_val!r}（新值 {new_val!r}）")
            result[key] = old_val
        else:
            if not _is_empty(new_val):
                print(f"[补充] {label}  {path_key} = {new_val!r}")
            result[key] = new_val

    for key, old_val in old.items():
        if key not in fresh:
            result[key] = old_val
    return result


def build_fresh_entry(folder: Path) -> tuple[dict, str]:
    audio = find_audio(folder)
    assert audio is not None
    icon = find_icon(folder)
    rel = folder.relative_to(SCAN_DIR).as_posix()
    root = f"{ROOT_PREFIX}{rel}/"

    if WRITE_ALGORITHM_NAME_ONLY:
        music_hash = HASH_ALGORITHM
        icon_hash = HASH_ALGORITHM if icon else ""
    else:
        music_hash = murmur_hash_file(audio)
        icon_hash = murmur_hash_file(icon) if icon else ""

    fresh = {
        "music_name": PLACEHOLDER,
        "author": PLACEHOLDER,
        "path": {
            "root": root,
            "music": audio.name,
            "icon": icon.name if icon else "",
            "hash": {
                "music": music_hash,
                "icon": icon_hash,
            },
        },
    }
    return fresh, rel


def main() -> None:
    if not SCAN_DIR.is_dir():
        sys.exit(f"扫描目录不存在：{SCAN_DIR}")

    print(f"扫描目录：{SCAN_DIR}")
    print(f"哈希模式：{'算法名' if WRITE_ALGORITHM_NAME_ONLY else '真实值'}\n")

    old_index = load_old_index(OUTPUT_PATH)
    if old_index:
        print(f"已读取旧索引，共 {len(old_index)} 条。\n")

    folders = collect_music_dirs(SCAN_DIR)
    seen_roots: set[str] = set()
    entries: list[dict] = []

    for folder in folders:
        fresh, rel = build_fresh_entry(folder)
        root = fresh["path"]["root"]
        seen_roots.add(root)
        old = old_index.get(root)

        if old is None:
            if fresh["path"]["icon"] == "":
                print(f"[警告] {folder}: 未找到封面")
            print(f"[新增] {rel:40s} -> {fresh['path']['music']}")
            entries.append(fresh)
        else:
            print(f"[合并] {rel:40s}")
            entries.append(merge_dict(old, fresh, label=rel))

    # 扫描不到的旧条目：原样保留
    orphan_roots = [r for r in old_index if r not in seen_roots]
    if orphan_roots:
        print("\n[提示] 以下旧条目未在扫描目录中找到对应文件夹，已原样保留：")
        for r in orphan_roots:
            print(f"  - {r}")
            entries.append(old_index[r])
        print("  如确认不再需要，请手动删除对应条目。\n")

    index = {
        "format_version": FORMAT_VERSION,
        "updated_at": date.today().isoformat(),
        "server": SERVER,
        "musics": entries,
    }

    OUTPUT_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已生成：{OUTPUT_PATH}")
    print(f"共 {len(entries)} 首音乐。")


if __name__ == "__main__":
    main()
