# -*- coding: utf-8 -*-
"""
暗语解析器 — 将博主暗语翻译为股票名和概念名。
从 data/nickname_map.json 加载暗语映射表进行文本扫描和注解。
"""

import json
import os


class AliasResolver:
    """博主暗语→股票+概念解析器。"""

    def __init__(self, map_path=None):
        if map_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            map_path = os.path.join(project_root, "data", "nickname_map.json")
        with open(map_path, encoding="utf-8") as f:
            self.map = json.load(f)

    def resolve(self, blogger_name, text):
        """扫描文本中的暗语，返回匹配的股票+概念列表。"""
        aliases = self.map.get(blogger_name, {})
        matches, seen = [], set()
        for alias, info in aliases.items():
            if alias.lower() in text.lower() and alias not in seen:
                matches.append({
                    "alias": alias,
                    "stocks": info["stocks"],
                    "concept": info.get("concept", ""),
                })
                seen.add(alias)
        return matches

    def enrich_text(self, blogger_name, text):
        """将暗语注解追加到文本末尾，如：披萨=德明利，dml=德明利。"""
        matches = self.resolve(blogger_name, text)
        if not matches:
            return text
        annotations = []
        for m in matches:
            s = "、".join(m["stocks"]) if m["stocks"] else "(概念)"
            annotations.append(f"{m['alias']}={s}")
        return f"{text}（注：{'，'.join(annotations)}）"

    def get_all_bloggers(self):
        """返回已标注的博主名称列表。"""
        return [k for k in self.map if not k.startswith("_")]


if __name__ == "__main__":
    r = AliasResolver()
    print("博主:", r.get_all_bloggers())
    print("白河愁暗语:", len(r.map.get("白河愁博士", {})), "个")
    print("派大星暗语:", len(r.map.get("派大星皮皮", {})), "个")
    text = "我的披萨还不错dml继续拿着"
    print("匹配:", r.resolve("白河愁博士", text))
    print("增补:", r.enrich_text("白河愁博士", text))
