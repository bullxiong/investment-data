#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识星球扫描器 v3
职责：拉取帖子全文，提取作者/概念/时间等信息
适配路径：config → data/zsxq/config.json

v3 变更:
- 增强 _parse_topic: 提取 author, key_concepts, raw_text
- 新增 scan_recent(days=N) 方法
- 输出 dict 包含完整 research_record 字段
"""

import requests
import json
import time
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from pathlib import Path


class ZsxqScanner:
    @staticmethod
    def _safe_print(msg: str):
        """安全打印，忽略控制台编码错误（Windows GBK ↔ emoji）"""
        try:
            print(msg)
        except UnicodeEncodeError:
            print(msg.encode('ascii', errors='replace').decode('ascii'))

    def __init__(self, config_path: str = "data/zsxq/config.json",
                 token: str = None, group_id: str = None):
        """
        初始化扫描器。支持两种方式:
        1. 配置文件: ZsxqScanner(config_path='data/zsxq/config.json')
        2. 直接传参: ZsxqScanner(token='xxx', group_id='yyy')
        """
        if token and group_id:
            self.access_token = token
            self.group_id = group_id
            self.request_delay = 3
            self.max_posts = 100
        else:
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            zsxq_config = self.config['zsxq']
            self.access_token = zsxq_config['access_token']
            self.group_id = zsxq_config['group_id']
            self.request_delay = zsxq_config.get('request_delay', 3)
            self.max_posts = zsxq_config.get('max_posts_per_scan', 100)

        self.base_url = "https://api.zsxq.com/v2"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Cookie": f"zsxq_access_token={self.access_token}",
            "Content-Type": "application/json"
        }

        # 加载概念分类法用于关键词匹配
        self._concept_keywords = self._load_concept_keywords()

    def _load_concept_keywords(self) -> Dict[str, List[str]]:
        """加载概念分类法，构建关键词→概念映射"""
        taxonomy_path = Path(__file__).parent.parent.parent / 'data' / 'zsxq' / 'canonical_taxonomy.json'
        try:
            concept_map = {}
            if taxonomy_path.exists():
                with open(taxonomy_path, encoding='utf-8') as f:
                    taxonomy = json.load(f)
                for l1, l2_list in taxonomy.items():
                    for l2 in l2_list:
                        if l2 not in concept_map:
                            concept_map[l2] = l1
                return concept_map
        except Exception:
            pass
        return {}

    def _extract_concepts_from_text(self, text: str) -> List[str]:
        """从文本中通过关键词匹配提取概念标签"""
        found = set()
        text_lower = text.lower()
        for concept in self._concept_keywords:
            if concept.lower() in text_lower:
                found.add(concept)
                # 也添加父概念（L1）
                l1 = self._concept_keywords.get(concept)
                if l1:
                    found.add(l1)
        return sorted(found)

    def _get_group_owner(self, group_id: str = None) -> Optional[str]:
        """
        获取星球星主 user_id。
        调用 groups/{group_id}/profile 获取星主信息。
        """
        gid = group_id or self.group_id
        try:
            time.sleep(self.request_delay)
            url = f"{self.base_url}/groups/{gid}"
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('succeeded'):
                    group = data.get('resp_data', {}).get('group', {})
                    owner = group.get('owner', {})
                    owner_id = owner.get('uid', '') or owner.get('user_id', '')
                    if owner_id:
                        return str(owner_id)
        except Exception as e:
            print(f"[ZSXQ] 获取星主失败 group={gid}: {e}")
        return None

    def scan_with_filter(self, group_id: str = None, star_owner_only: bool = False) -> List[Dict]:
        """
        带过滤的扫描方法（v3.1 新增）。

        Args:
            group_id: 目标群组ID，默认用实例 group_id
            star_owner_only: True 时只保留星主帖子

        Returns:
            帖子列表
        """
        gid = group_id or self.group_id
        # 临时切换 group_id
        original_gid = self.group_id
        self.group_id = gid
        try:
            posts = self.get_recent_posts(hours=24)
        finally:
            self.group_id = original_gid

        if star_owner_only and posts:
            owner_id = self._get_group_owner(gid)
            if owner_id:
                filtered = [p for p in posts
                            if str(p.get('author_info', {}).get('user_id', '')) == owner_id]
                print(f"[ZSXQ] 星主过滤: {len(posts)} → {len(filtered)} posts (owner={owner_id})")
                return filtered
            else:
                print(f"[ZSXQ] 无法获取星主ID，返回全部 {len(posts)} posts")
        return posts

    def scan_recent(self, days: int = 7) -> List[Dict]:
        """
        扫描最近N天的帖子（v3 新增方法，兼容验证脚本）
        """
        return self.get_recent_posts(hours=days * 24)

    def get_recent_posts(self, hours: int = 24) -> List[Dict]:
        """
        获取最近N小时的帖子全文列表
        每个帖子包含: topic_id, title, content, create_time, url, author, key_concepts
        """
        posts = []
        end_time = None
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)

        max_retries = 3
        retry_delay = 10

        while len(posts) < self.max_posts:
            url = f"{self.base_url}/groups/{self.group_id}/topics"
            params = {"count": 20}
            if end_time:
                params["end_time"] = end_time

            # 带重试的请求
            data = None
            for attempt in range(1, max_retries + 1):
                try:
                    time.sleep(self.request_delay)
                    response = requests.get(url, headers=self.headers, params=params, timeout=10)

                    if response.status_code != 200:
                        print(f"[ZSXQ] 请求失败: HTTP {response.status_code} (尝试 {attempt}/{max_retries})")
                        if attempt < max_retries:
                            print(f"[ZSXQ] 等待 {retry_delay}s 后重试...")
                            time.sleep(retry_delay)
                            continue
                        print(f"[ZSXQ] 响应: {response.text[:300]}")
                        return posts

                    resp_data = response.json()

                    if not resp_data.get('succeeded'):
                        error_code = resp_data.get('code', '')
                        error_info = resp_data.get('info', '') or resp_data.get('resp_data', {})
                        print(f"[ZSXQ] API错误 code={error_code} (尝试 {attempt}/{max_retries}): {error_info}")
                        if attempt < max_retries:
                            print(f"[ZSXQ] 等待 {retry_delay}s 后重试...")
                            time.sleep(retry_delay)
                            continue
                        return posts

                    data = resp_data
                    break

                except Exception as e:
                    print(f"[ZSXQ] 获取帖子异常 (尝试 {attempt}/{max_retries}): {e}")
                    if attempt < max_retries:
                        time.sleep(retry_delay)
                    else:
                        return posts

            if not data:
                return posts

            topics = data.get('resp_data', {}).get('topics', [])
            if not topics:
                break

            for topic in topics:
                create_time_str = topic.get('create_time', '')
                if not create_time_str:
                    continue

                create_time = datetime.fromisoformat(create_time_str.replace('Z', '+00:00'))
                if create_time < cutoff_time:
                    return posts

                post_data = self._parse_topic(topic)
                if post_data:
                    posts.append(post_data)

            end_time = topics[-1].get('create_time')

        return posts

    def get_topic_detail(self, topic_id: str) -> Optional[Dict]:
        """
        获取单个帖子详情（如果列表API返回数据不完整，作为fallback）
        """
        try:
            time.sleep(self.request_delay)
            url = f"{self.base_url}/topics/{topic_id}"
            response = requests.get(url, headers=self.headers, timeout=10)

            if response.status_code != 200:
                print(f"[ZSXQ] 获取帖子详情失败: {topic_id} HTTP {response.status_code}")
                return None

            resp_data = response.json()
            if not resp_data.get('succeeded'):
                return None

            topic = resp_data.get('resp_data', {}).get('topic', {})
            if topic:
                return self._parse_topic(topic)

        except Exception as e:
            print(f"[ZSXQ] 获取帖子详情异常 {topic_id}: {e}")

        return None

    def _parse_topic(self, topic: Dict) -> Optional[Dict]:
        """
        解析单个帖子，返回完整研究记录字段
        提取: title, content, author, time, concepts, url
        """
        try:
            topic_id = topic.get('topic_id')
            create_time = topic.get('create_time', '')

            talk = topic.get('talk', {})
            topic_obj = talk.get('topic', {})

            # 标题
            title = topic_obj.get('title', '') or ''
            title_clean = re.sub(r'<[^>]+>', '', title).strip()

            # 正文
            text = talk.get('text', '') or ''
            raw_text = text  # 保留原始HTML文本
            text_clean = re.sub(r'<[^>]+>', '', text).strip()

            # 拼接标题和正文为完整内容
            if title_clean:
                content = f"{title_clean}\n\n{text_clean}".strip()
            else:
                content = text_clean

            if not content:
                return None

            # -- 作者提取 --
            author = ''
            author_info = {}
            owner = talk.get('owner') or topic.get('owner') or {}
            if owner:
                author = owner.get('name', '') or owner.get('screen_name', '') or ''
                author_info = {
                    'name': author,
                    'user_id': owner.get('uid', '') or owner.get('user_id', ''),
                    'avatar_url': owner.get('avatar_url', ''),
                    'description': owner.get('description', ''),
                }

            # -- 概念提取 --
            key_concepts = self._extract_concepts_from_text(content)

            # -- 帖子链接 --
            post_url = f"https://wx.zsxq.com/group/{self.group_id}/topic/{topic_id}"

            return {
                'topic_id': topic_id,
                'title': title_clean,
                'content': content,
                'text': raw_text,           # 原始HTML（爬取原始数据保留用）
                'author': author,
                'author_info': author_info,
                'create_time': create_time,
                'created_at': create_time,   # 别名，兼容验证脚本检查 'created_at'
                'url': post_url,
                'key_concepts': key_concepts,
                'source_type': 'zsxq_post',
            }
        except Exception as e:
            print(f"[ZSXQ] 解析帖子异常: {e}")
            return None

    def get_last_scan_time(self, cache_file: str = "data/zsxq/last_scan.json") -> datetime:
        """获取上次扫描时间"""
        if Path(cache_file).exists():
            try:
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                    return datetime.fromisoformat(data['last_scan_time'])
            except Exception:
                pass
        return datetime.now() - timedelta(days=30)

    def update_scan_time(self, cache_file: str = "data/zsxq/last_scan.json"):
        """更新扫描时间戳"""
        with open(cache_file, 'w') as f:
            json.dump({'last_scan_time': datetime.now().isoformat()}, f)


if __name__ == "__main__":
    scanner = ZsxqScanner()
    print("正在扫描知识星球...")
    posts = scanner.get_recent_posts(hours=24)
    print(f"获取到 {len(posts)} 个帖子")
    for post in posts[:3]:
        print(f"\n标题: {post['title']}")
        print(f"作者: {post['author']}")
        print(f"概念: {post.get('key_concepts', [])}")
        print(f"内容({len(post['content'])}字): {post['content'][:200]}...")
