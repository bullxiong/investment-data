# -*- coding: utf-8 -*-
"""
文本清洗器 — 移除帖子中的 @mention、转发标记、格式噪音。

设计为无状态工具类，可被任意模块 import 使用：
    from src.preprocess.text_cleaner import TextCleaner
"""

import json
import os
import re


class TextCleaner:
    """清洗帖子文本中的社会化噪音。"""

    @staticmethod
    def clean(text):
        """返回清洗后的文本。

        移除：
        - 回复/转发标记（"回复@xxx: ", "//@xxx: "）
        - 独立的 @mention（"@xxx "）
        - 引用标记（"> "）
        - 平台 UI 碎片（"收起", "查看对话"）
        - 多余空白字符

        Parameters
        ----------
        text : str
            原始帖子正文。

        Returns
        -------
        str
            清洗后的文本。
        """
        if not text:
            return ""

        # 移除 "回复@拔刀快斩: " 类回复标记
        text = re.sub(r'回复@\S+[:：]?\s*', '', text)

        # 移除 "//@xxx: " 类转发标记头（保留正文）
        text = re.sub(r'//@\S+[:：]?\s*', '', text)

        # 移除独立的 @mention
        text = re.sub(r'@\S+\s*[:：]?\s*', '', text)

        # 移除引用标记 "> "
        text = re.sub(r'>\s*', '', text)

        # 移除平台 UI 碎片
        text = re.sub(r'收起\s*_?\s*[^\n]*', '', text)
        text = re.sub(r'查看对话\s*', '', text)

        # 压缩空白字符
        text = re.sub(r'\s+', ' ', text).strip()

        return text

    @staticmethod
    def resolve_aliases(text, blogger_name, map_path=None):
        """扫描文本中的博主暗语，追加注解到末尾。

        读取 data/nickname_map.json，找到 blogger_name 对应的暗语表，
        在 text 中匹配暗语，将 "暗语=股票名" 注解追加到文本末尾。

        原文: "我的披萨还不错，dml继续拿着"
        处理后: "我的披萨还不错，dml继续拿着（注：披萨=德明利，dml=德明利）"

        Parameters
        ----------
        text : str
            原始文本。
        blogger_name : str
            博主名称。
        map_path : str, optional
            暗语映射文件路径。

        Returns
        -------
        str
            带注解的文本，或原文（无匹配时）。
        """
        if not text or not blogger_name:
            return text

        if map_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            map_path = os.path.join(project_root, "data", "nickname_map.json")

        if not os.path.exists(map_path):
            return text

        with open(map_path, encoding="utf-8") as f:
            alias_map = json.load(f)

        aliases = alias_map.get(blogger_name, {})
        if not aliases:
            return text

        annotations = []
        seen = set()
        lower_text = text.lower()
        for alias, info in aliases.items():
            if alias.lower() in lower_text and alias not in seen:
                stocks_str = "、".join(info["stocks"]) if info["stocks"] else "(概念)"
                annotations.append(f"{alias}={stocks_str}")
                seen.add(alias)

        if not annotations:
            return text

        return f"{text}（注：{'，'.join(annotations)}）"


# ================================================================
# 自测
# ================================================================

if __name__ == "__main__":
    test_cases = [
        # (input, expected_contains, expected_not_contains)
        ("回复@拔刀快斩: 这个液冷不错", "液冷", "拔刀快斩"),
        ("//@某人: 我觉得可以 这个票不错", "这个票", "某人"),
        ("@张三 今天半导体很强", "半导体", "张三"),
        ("> 引用内容 实际观点", "实际观点", ">"),
        ("某个观点 收起 _ 展开全文", "某个观点", "收起"),
        ("这个不错 查看对话", "这个不错", "查看对话"),
        ("  多余空格  都  清除  ", "多余空格 都 清除", "   "),
        ("", "", None),
    ]

    all_ok = True
    for i, (input_text, expect_contains, expect_not) in enumerate(test_cases):
        result = TextCleaner.clean(input_text)
        ok = True
        if expect_contains and expect_contains not in result:
            ok = False
        if expect_not and expect_not in result:
            ok = False
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  [{status}] 测试 {i+1}: \"{input_text}\" → \"{result}\"")
        if expect_contains:
            print(f"         预期包含: \"{expect_contains}\"")
        if expect_not and not ok:
            print(f"         预期不含: \"{expect_not}\"")

    print(f"\n{'全部通过' if all_ok else '有失败用例'}")
