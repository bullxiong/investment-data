# -*- coding: utf-8 -*-
"""
统一爬虫编排器 — 一键拉取所有平台所有博主的最新数据。

支持平台:
  - xueqiu: TokenCycler (autoglm + API) → parse_api_response
  - zhihu:  ZhiHuCrawler (API full_content 模式)
  - weixin: 转发即处理模型（占位，不自动爬取）
  - zsxq:   ZsxqScanner (API直接拉取)

用法:
    orchestrator = CrawlerOrchestrator()
    results = orchestrator.crawl_all()
    for uid, posts in results.items():
        print(f"{uid}: {len(posts)} posts")
"""

import json
import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

TZ = timezone(timedelta(hours=8))
DEFAULT_BLOGGERS_PATH = os.path.join(_project_root, "bloggers.json")

logger = logging.getLogger("orchestrator")


class CrawlerOrchestrator:
    """
    统一爬虫编排器。

    加载 bloggers.json，按平台路由到对应爬虫，统一返回结果。

    每爬取一个博主可触发用户侧回调 on_blogger_done(uid, cfg, posts)。
    """

    def __init__(
        self,
        bloggers_path: Optional[str] = None,
        data_dir: Optional[str] = None,
    ):
        """
        Args:
            bloggers_path: bloggers.json 路径
            data_dir:      数据根目录 (默认 project_root/data)
        """
        self.bloggers_path = bloggers_path or DEFAULT_BLOGGERS_PATH
        self.data_dir = data_dir or os.path.join(_project_root, "data")

        with open(self.bloggers_path, encoding="utf-8") as f:
            self.bloggers = json.load(f)

        # 按平台分组（便于批量处理）
        self._by_platform: dict[str, list[tuple[str, dict]]] = {}
        for uid, cfg in self.bloggers.items():
            platform = cfg.get("platform", "")
            self._by_platform.setdefault(platform, []).append((uid, cfg))

        # 爬取结果回调（可选：供 cron_runner 注册保存/分析 hook）
        self._on_blogger_done = None

    def on_blogger_done(self, fn):
        """
        注册回调 fn(uid, cfg, posts: list[dict])。
        每次爬完一个博主后调用。可用于 save_posts + analyze_posts。
        """
        self._on_blogger_done = fn

    # ── 平台爬虫 ────────────────────────────────────────────────────

    def _crawl_xueqiu(self, uid: str, cfg: dict) -> list[dict]:
        """雪球：使用 TokenCycler 拉取。"""
        from src.crawlers.xueqiu.token_cycler import TokenCycler

        cookies_path = os.path.join(self.data_dir, "xueqiu_cookies.json")
        cycler = TokenCycler(cookies_path=cookies_path)

        max_pages = cfg.get("max_pages", 3)
        logger.info(f"[Xueqiu] {cfg.get('name', uid)} (uid={uid}) → {max_pages} 页")

        posts = cycler.fetch_posts(uid, max_pages=max_pages)

        if posts:
            self._save_posts(uid, posts)

        return posts

    def _crawl_zhihu(self, uid: str, cfg: dict) -> list[dict]:
        """知乎：使用 ZhiHuCrawler full_content 模式。"""
        from src.crawlers.zhihu.crawler import ZhiHuCrawler

        slug = cfg.get("url_slug", uid)
        cookies = cfg.get("zhihu_cookies")
        # 也支持从环境变量或 data/zhihu_cookies.json 读取
        crawler = ZhiHuCrawler(url_slug=slug, cookies=cookies)

        logger.info(f"[Zhihu] {cfg.get('name', uid)} (slug={slug}) → full_content")

        posts = crawler.fetch_all(full_content=True)

        if posts:
            self._save_posts(uid, posts)

        return posts

    def _crawl_weixin(self, uid: str, cfg: dict) -> list[dict]:
        """
        微信：转发即处理模型。

        微信公众号反爬极严，不支持自动化爬取。
        当前流程：用户转发文章 → 主控提取内容 → 分析入库。
        这里返回空列表，由其他流程处理。
        """
        logger.info(
            f"[Weixin] {cfg.get('name', uid)} — "
            f"转发即处理模型，跳过自动爬取"
        )
        return []

    def _crawl_zsxq(self, uid: str, cfg: dict) -> list[dict]:
        """
        知识星球：使用 ZsxqScanner API 拉取。

        支持两种配置方式:
        1. cfg 中有 group_id → 直接用它扫描（新博主条目）
        2. cfg 中无 group_id → 从 config.json 读取所有群组遍历

        star_owner_only 从 cfg 读取，用于奥特之父等只看星主的场景。
        """
        from src.zsxq.zsxq_scanner import ZsxqScanner

        config_path = cfg.get(
            "config_path",
            os.path.join(self.data_dir, "zsxq", "config.json"),
        )
        hours = cfg.get("hours", 24)
        star_owner_only = cfg.get("star_owner_only", False)
        group_id = cfg.get("group_id", "")

        # 加载 token
        token = None
        if os.path.exists(config_path):
            with open(config_path, encoding='utf-8') as f:
                zsxq_cfg = json.load(f)
            token = zsxq_cfg.get('zsxq', {}).get('access_token', '')

        if not token:
            logger.warning(f"[ZSXQ] No token found in config")
            return []

        all_posts = []

        if group_id:
            # 新条目：直接使用 blogger 配置中的 group_id
            logger.info(f"[ZSXQ] {cfg.get('name', uid)} group={group_id} → {hours}h")

            scanner = ZsxqScanner(token=token, group_id=group_id)
            if star_owner_only:
                raw_posts = scanner.scan_with_filter(star_owner_only=True)
            else:
                raw_posts = scanner.get_recent_posts(hours=hours)

            for rp in raw_posts:
                all_posts.append(self._normalize_zsxq_post(rp, uid, cfg))

        else:
            # 旧条目：从 config 读取所有群组
            groups = zsxq_cfg.get('zsxq', {}).get('groups', [
                {"group_id": zsxq_cfg['zsxq']['group_id'], "name": "默认群组", "type": "research"}
            ])

            for group in groups:
                gid = group['group_id']
                gname = group.get('name', gid)
                is_star = (gid == '28888221524121')  # 奥特之父

                logger.info(f"[ZSXQ] {gname} ({gid}) → {hours}h")

                try:
                    scanner = ZsxqScanner(token=token, group_id=gid)
                    if is_star:
                        raw_posts = scanner.scan_with_filter(star_owner_only=True)
                    else:
                        raw_posts = scanner.get_recent_posts(hours=hours)

                    for rp in raw_posts:
                        all_posts.append(self._normalize_zsxq_post(rp, uid, cfg))

                except Exception as e:
                    logger.error(f"[ZSXQ] Error scanning {gname}: {e}")

        if all_posts:
            self._save_posts(uid.replace("zsxq_", ""), all_posts)

        return all_posts

    def _normalize_zsxq_post(self, rp: dict, uid: str, cfg: dict) -> dict:
        """将 ZSXQ 原始帖子转为统一格式。"""
        return {
            "post_id": f"zsxq_{rp.get('topic_id', '')}",
            "user_id": uid,
            "author": cfg.get("name", "知识星球"),
            "title": rp.get("title", ""),
            "content": rp.get("content", ""),
            "created_at": rp.get("create_time", ""),
            "source": "zsxq",
            "url": rp.get("url", ""),
            "type": "post",
            "is_retweet": False,
            "reply_count": 0,
            "like_count": 0,
            "stocks": [],
        }

    # ── 存储 ─────────────────────────────────────────────────────────

    def _save_posts(self, uid: str, posts: list[dict], date_str: Optional[str] = None):
        """保存帖子到 data/posts/{uid}/{date}.json。"""
        if date_str is None:
            date_str = datetime.now(TZ).strftime("%Y-%m-%d")

        post_dir = os.path.join(self.data_dir, "posts", uid)
        os.makedirs(post_dir, exist_ok=True)
        filepath = os.path.join(post_dir, f"{date_str}.json")

        existing = []
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        merged = {}
        for p in existing:
            pid = p.get("post_id", "")
            if pid:
                merged[pid] = p
        for p in posts:
            pid = p.get("post_id", "")
            if pid:
                merged[pid] = p

        merged_list = sorted(
            merged.values(), key=lambda p: p.get("created_at", ""), reverse=True
        )

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(merged_list, f, ensure_ascii=False, indent=2)

        logger.info(f"[Save] {uid}: {len(merged_list)} posts → {filepath}")

    # ── 主入口 ────────────────────────────────────────────────────────

    def crawl_all(self, date_str: Optional[str] = None) -> dict:
        """
        拉取所有博主的最新帖子。

        Args:
            date_str: 日期标识 (用于保存文件命名)，默认今天

        Returns:
            {uid: [posts], ...} 按博主分组
        """
        if date_str is None:
            date_str = datetime.now(TZ).strftime("%Y-%m-%d")

        results: dict[str, list[dict]] = {}
        platform_handlers = {
            "xueqiu": self._crawl_xueqiu,
            "zhihu": self._crawl_zhihu,
            "weixin": self._crawl_weixin,
            "zsxq": self._crawl_zsxq,
        }

        total = len(self.bloggers)
        done = 0

        logger.info(f"CrawlAll: {total} bloggers, date={date_str}")

        for uid, cfg in self.bloggers.items():
            platform = cfg.get("platform", "")
            name = cfg.get("name", uid)

            handler = platform_handlers.get(platform)
            if not handler:
                logger.warning(
                    f"[Orchestrator] 未知平台 '{platform}' for {name}, 跳过"
                )
                results[uid] = []
                continue

            try:
                posts = handler(uid, cfg)
                results[uid] = posts
                done += 1
                logger.info(
                    f"[Orchestrator] ✓ {name} ({platform}): {len(posts)} posts"
                )

                # 触发回调
                if self._on_blogger_done:
                    try:
                        self._on_blogger_done(uid, cfg, posts)
                    except Exception as e:
                        logger.error(f"[Orchestrator] 回调异常 for {uid}: {e}")

            except Exception as e:
                logger.error(
                    f"[Orchestrator] ✗ {name} ({platform}): {e}", exc_info=True
                )
                results[uid] = []

            # 博主间延迟
            if done < total:
                time.sleep(2)

        total_posts = sum(len(v) for v in results.values())
        logger.info(
            f"Orchestrator 完成: {done}/{total} bloggers, {total_posts} total posts"
        )

        return results

    def crawl_platform(self, platform: str, date_str: Optional[str] = None) -> dict:
        """
        只爬取指定平台的博主。

        Args:
            platform: "xueqiu" | "zhihu" | "weixin" | "zsxq"
            date_str:  日期标识

        Returns:
            {uid: [posts], ...}
        """
        entries = self._by_platform.get(platform, [])
        results = {}

        platform_handlers = {
            "xueqiu": self._crawl_xueqiu,
            "zhihu": self._crawl_zhihu,
            "weixin": self._crawl_weixin,
            "zsxq": self._crawl_zsxq,
        }
        handler = platform_handlers.get(platform)
        if not handler:
            logger.warning(f"[Orchestrator] 未知平台: {platform}")
            return results

        for uid, cfg in entries:
            try:
                posts = handler(uid, cfg)
                results[uid] = posts
                if self._on_blogger_done:
                    try:
                        self._on_blogger_done(uid, cfg, posts)
                    except Exception as e:
                        logger.error(f"[Orchestrator] 回调异常: {e}")
            except Exception as e:
                logger.error(f"[Orchestrator] 错误 for {uid}: {e}", exc_info=True)
                results[uid] = []

        return results


# ── 便捷函数 ─────────────────────────────────────────────────────────

def crawl_all(date_str: Optional[str] = None) -> dict:
    """便捷函数：一键爬取所有博主。"""
    orchestrator = CrawlerOrchestrator()
    return orchestrator.crawl_all(date_str=date_str)


def crawl_platform(platform: str, date_str: Optional[str] = None) -> dict:
    """便捷函数：只爬取指定平台。"""
    orchestrator = CrawlerOrchestrator()
    return orchestrator.crawl_platform(platform, date_str=date_str)


# ── 命令行 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="统一爬虫编排器")
    parser.add_argument(
        "--platform",
        choices=["xueqiu", "zhihu", "weixin", "zsxq"],
        help="仅爬取指定平台（不指定则爬所有）",
    )
    args = parser.parse_args()

    orc = CrawlerOrchestrator()

    if args.platform:
        results = orc.crawl_platform(args.platform)
    else:
        results = orc.crawl_all()

    print("\n" + "=" * 60)
    print("爬取结果汇总")
    print("=" * 60)
    for uid, posts in results.items():
        print(f"  {uid}: {len(posts)} posts")
