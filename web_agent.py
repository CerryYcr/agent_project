#!/usr/bin/env python
# -*- coding: utf-8 -*-
# web_agent.py
# 网页抓取 Agent —— 硬解析优先 · LLM 辅助降级 · 金融网站专项适配 · 智能缓存

import os
import sys
import json
import csv
import time
import random
import re
import argparse
from datetime import datetime, timedelta
from urllib.parse import urlparse
from typing import Optional, Dict, List, Tuple, Any

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# 配置
# ============================================================
class Config:
    def __init__(self):
        self.TIMEOUT = 30
        self.REQUEST_DELAY_MIN = 1.0
        self.REQUEST_DELAY_MAX = 3.0
        self.MAX_RETRIES = 3
        self.BACKOFF_FACTOR = 2
        self.USER_AGENTS = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        ]
        self.REFERERS = [
            "https://www.google.com/",
            "https://www.bing.com/",
            "https://www.baidu.com/",
        ]
        self.OUTPUT_DIR = "data_output/web_agent"
        self.SILICON_API_KEY = os.getenv("SILICON_API_KEY")
        self.MODEL_NAME = "deepseek-ai/DeepSeek-V4-Flash"
        self.SILICON_BASE_URL = "https://api.siliconflow.cn/v1"

        # --- 反爬配置 ---
        self.PROXY_LIST = self._parse_proxies(os.getenv("WEB_AGENT_PROXIES", ""))
        self.USE_SELENIUM_FALLBACK = os.getenv("WEB_AGENT_USE_SELENIUM", "false").lower() == "true"

        # --- 缓存配置 ---
        self.CACHE_ENABLED = True
        self.CACHE_TTL = 86400  # 24 小时
        self.CACHE_FILE = os.path.join(self.OUTPUT_DIR, "cache.json")

        os.makedirs(self.OUTPUT_DIR, exist_ok=True)

    def _parse_proxies(self, proxy_str: str) -> List[str]:
        if not proxy_str:
            return []
        proxies = [p.strip() for p in proxy_str.split(",") if p.strip()]
        valid = []
        for p in proxies:
            if p in ["http://user:pass@ip:port", "socks5://ip:port", "http://username:password@ip:port"]:
                continue
            if p.startswith(("http://", "socks5://", "https://")):
                valid.append(p)
        return valid


# ============================================================
# 缓存管理
# ============================================================
class CacheManager:
    def __init__(self, config: Config):
        self.config = config
        self._cache = self._load()

    def _load(self) -> Dict:
        if os.path.exists(self.config.CACHE_FILE):
            try:
                with open(self.config.CACHE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save(self):
        with open(self.config.CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=2)

    def get(self, url: str) -> Optional[Dict]:
        entry = self._cache.get(url)
        if entry:
            try:
                last_time = datetime.fromisoformat(entry['timestamp'])
                if (datetime.now() - last_time).total_seconds() < self.config.CACHE_TTL:
                    file_path = entry.get('file_path')
                    if file_path and os.path.exists(file_path):
                        return entry
            except:
                pass
        return None

    def set(self, url: str, file_path: str, metadata: dict):
        self._cache[url] = {
            "timestamp": datetime.now().isoformat(),
            "file_path": file_path,
            "url": url,
            "metadata": metadata
        }
        self._save()

    def clear(self, url: str = None):
        if url:
            self._cache.pop(url, None)
        else:
            self._cache.clear()
        self._save()


# ============================================================
# 反爬基础设施
# ============================================================
class AntiScrape:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self._load_cookies()

    def _load_cookies(self):
        cookie_file = os.path.join(self.config.OUTPUT_DIR, "cookies.json")
        if os.path.exists(cookie_file):
            try:
                with open(cookie_file, 'r') as f:
                    cookies = json.load(f)
                    self.session.cookies.update(cookies)
            except:
                pass

    def _save_cookies(self):
        cookie_file = os.path.join(self.config.OUTPUT_DIR, "cookies.json")
        try:
            with open(cookie_file, 'w') as f:
                json.dump(self.session.cookies.get_dict(), f)
        except:
            pass

    def _get_headers(self) -> dict:
        return {
            "User-Agent": random.choice(self.config.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
            "Referer": random.choice(self.config.REFERERS),
        }

    def _get_proxy(self) -> Optional[Dict[str, str]]:
        if self.config.PROXY_LIST:
            proxy = random.choice(self.config.PROXY_LIST)
            return {"http": proxy, "https": proxy}
        return None

    def fetch(self, url: str) -> Dict:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        last_error = None
        for attempt in range(self.config.MAX_RETRIES + 1):
            try:
                delay = random.uniform(self.config.REQUEST_DELAY_MIN, self.config.REQUEST_DELAY_MAX)
                if attempt > 0:
                    delay *= self.config.BACKOFF_FACTOR ** attempt
                time.sleep(delay)

                headers = self._get_headers()
                proxies = self._get_proxy()
                response = self.session.get(
                    url,
                    headers=headers,
                    proxies=proxies,
                    timeout=self.config.TIMEOUT,
                    allow_redirects=True
                )

                if response.status_code == 200:
                    if self._is_blocked(response.text):
                        return {"success": False, "error": "⚠️ 页面返回验证/拦截页面，可能被反爬。建议更换 IP 或使用代理。", "blocked": True}
                    response.encoding = response.apparent_encoding or 'utf-8'
                    self._save_cookies()
                    return {"success": True, "html": response.text, "url": response.url}

                elif response.status_code == 429:
                    wait = 10 * (attempt + 1)
                    print(f"⏳ 请求限流 (429)，等待 {wait}s 后重试...")
                    time.sleep(wait)
                    continue

                elif response.status_code in [403, 401]:
                    if attempt == self.config.MAX_RETRIES:
                        return {"success": False, "error": f"❌ 访问被拒绝 ({response.status_code})，请检查权限或使用代理"}
                    continue

                elif response.status_code >= 500:
                    if attempt == self.config.MAX_RETRIES:
                        return {"success": False, "error": f"❌ 服务器错误 ({response.status_code})"}
                    continue

                else:
                    return {"success": False, "error": f"❌ 请求失败 ({response.status_code})"}

            except requests.exceptions.Timeout:
                last_error = f"⏳ 请求超时 (尝试 {attempt+1}/{self.config.MAX_RETRIES+1})"
                print(last_error)
                time.sleep(2)
                continue
            except requests.exceptions.ConnectionError:
                last_error = f"🔌 连接失败 (尝试 {attempt+1}/{self.config.MAX_RETRIES+1})"
                print(last_error)
                time.sleep(2)
                continue
            except Exception as e:
                last_error = f"❌ 抓取异常: {str(e)}"
                print(last_error)
                time.sleep(2)
                continue

        return {"success": False, "error": last_error or "抓取失败，已达最大重试次数"}

    def _is_blocked(self, html: str) -> bool:
        keywords = ["验证", "安全", "Access Denied", "Blocked", "Too Many Requests", "Captcha"]
        html_lower = html.lower()
        for kw in keywords:
            if kw.lower() in html_lower:
                return True
        if len(html) < 500 and "html" not in html_lower:
            return True
        return False


# ============================================================
# Selenium 降级（可选）
# ============================================================
class SeleniumFetcher:
    def __init__(self, config: Config):
        self.config = config
        self.driver = None

    def fetch(self, url: str) -> Dict:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
        except ImportError:
            return {"success": False, "error": "请安装 selenium 和 webdriver-manager: pip install selenium webdriver-manager"}

        try:
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument(f"user-agent={random.choice(self.config.USER_AGENTS)}")

            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_page_load_timeout(self.config.TIMEOUT)

            self.driver.get(url)
            html = self.driver.page_source
            self.driver.quit()
            return {"success": True, "html": html, "url": url}
        except Exception as e:
            if self.driver:
                self.driver.quit()
            return {"success": False, "error": f"Selenium 抓取失败: {str(e)}"}


# ============================================================
# 增强型表格解析器
# ============================================================
class Parser:
    @staticmethod
    def extract_tables(html: str, url: str = "") -> List[Dict]:
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        if not tables:
            # 尝试查找包含表格的 div 容器（如 data tables）
            div_tables = soup.find_all("div", class_=re.compile(r"(table|data|grid)"))
            for div in div_tables:
                inner = div.find("table")
                if inner:
                    tables.append(inner)

        result = []
        for idx, table in enumerate(tables):
            # 尝试提取 thead 和 tbody
            thead = table.find("thead")
            tbody = table.find("tbody")
            if thead:
                header_rows = thead.find_all("tr")
            else:
                header_rows = table.find_all("tr", recursive=False)
                if not header_rows:
                    header_rows = table.find_all("tr")

            if not header_rows:
                continue

            # 解析表头
            headers = []
            first_row = header_rows[0]
            ths = first_row.find_all("th")
            if ths:
                headers = [th.get_text(strip=True) for th in ths]
            else:
                tds = first_row.find_all("td")
                if tds:
                    headers = [f"列{i+1}" for i in range(len(tds))]
                else:
                    # 尝试从 tr 中提取
                    for row in header_rows[:2]:
                        cells = row.find_all(["td", "th"])
                        if cells:
                            headers = [c.get_text(strip=True) for c in cells]
                            break
            if not headers:
                continue

            # 解析数据行
            data_rows = []
            if tbody:
                body_rows = tbody.find_all("tr")
            else:
                body_rows = table.find_all("tr")[1:]  # 跳过表头行

            for row in body_rows:
                cells = row.find_all("td")
                if not cells or len(cells) != len(headers):
                    # 如果列数不匹配，尝试用 th 填充
                    cells = row.find_all(["td", "th"])
                if len(cells) < len(headers):
                    # 可能包含合并单元格，跳过
                    continue
                row_data = {}
                for i, cell in enumerate(cells[:len(headers)]):
                    text = cell.get_text(strip=True)
                    # 去除多余空白
                    text = re.sub(r'\s+', ' ', text)
                    row_data[headers[i]] = text
                if any(v for v in row_data.values()):
                    data_rows.append(row_data)

            if headers and data_rows:
                result.append({
                    "table_index": idx + 1,
                    "headers": headers,
                    "rows": data_rows,
                    "row_count": len(data_rows),
                    "column_count": len(headers)
                })

        # 如果没有提取到表格，尝试从页面文本中解析键值对
        if not result:
            # 尝试查找价格相关的键值对
            price_patterns = [
                (r'([\u4e00-\u9fa5]+[金銀银铂铂])\s*[:：]?\s*([\d.]+)', '产品', '价格'),
                (r'([A-Z]{2,4})\s*[:：]?\s*([\d.]+)', '品种', '价格'),
            ]
            text = soup.get_text()
            lines = text.split('\n')
            data_rows = []
            for line in lines:
                line = line.strip()
                for pattern, label_key, val_key in price_patterns:
                    matches = re.findall(pattern, line)
                    for m in matches:
                        if len(m) == 2:
                            data_rows.append({label_key: m[0], val_key: m[1]})
            if data_rows:
                headers = list(data_rows[0].keys())
                result.append({
                    "table_index": 1,
                    "headers": headers,
                    "rows": data_rows,
                    "row_count": len(data_rows),
                    "column_count": len(headers)
                })

        return result

    @staticmethod
    def extract_lists(html: str) -> List[Dict]:
        soup = BeautifulSoup(html, "html.parser")
        lists = soup.find_all(["ul", "ol"])
        result = []
        for idx, ul in enumerate(lists):
            items = ul.find_all("li", recursive=False)
            if not items:
                continue
            item_texts = [li.get_text(strip=True) for li in items]
            result.append({
                "list_index": idx + 1,
                "items": item_texts,
                "count": len(item_texts)
            })
        return result


# ============================================================
# LLM 辅助解析
# ============================================================
def call_llm(prompt: str, config: Config) -> str:
    if not config.SILICON_API_KEY:
        return ""
    url = f"{config.SILICON_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.SILICON_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": config.MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 1024
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
    except:
        pass
    return ""


def llm_assisted_parse(html: str, url: str, config: Config) -> dict:
    print("🧠 硬解析未找到数据，尝试 LLM 辅助分析...")
    snippet = html[:5000]
    prompt = f"""
你是一个网页数据提取助手。用户希望从以下网页提取数据。

【网页 URL】
{url}

【HTML 片段】
{snippet}

【任务】
1. 判断这个页面包含什么类型的数据（表格、列表、键值对、卡片等）
2. 如果包含数据，输出 JSON 格式的结构化数据
3. 如果不包含数据，返回 {{"has_data": false}}

输出格式必须为 JSON：
{{
    "has_data": true,
    "data_type": "table" | "list" | "key_value" | "cards",
    "sample_data": [...],
    "description": "简短描述数据内容"
}}
"""
    response = call_llm(prompt, config)
    try:
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data
    except:
        pass
    return {"has_data": False, "error": "LLM 解析失败"}


# ============================================================
# 格式转换与保存
# ============================================================
def save_csv(headers: list, rows: list, filepath: str):
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def save_markdown(headers: list, rows: list, filepath: str):
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("| " + " | ".join(headers) + " |\n")
        f.write("| " + " | ".join(["---"] * len(headers)) + " |\n")
        for row in rows:
            values = [str(row.get(h, "")) for h in headers]
            f.write("| " + " | ".join(values) + " |\n")


def save_json(data: dict, filepath: str):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# 主流程（含缓存交互）
# ============================================================
def run_web_agent(url: str, config: Config, force: bool = False, no_cache: bool = False) -> Dict:
    print(f"\n🔗 目标: {url}")

    cache_mgr = CacheManager(config)

    # 检查缓存
    if config.CACHE_ENABLED and not force and not no_cache:
        cached = cache_mgr.get(url)
        if cached:
            print(f"💾 找到缓存（来自 {cached['timestamp']}）")
            action = input("  重新抓取？(y=是 / n=使用缓存 / s=跳过本次请求): ").strip().lower()
            if action == 'n':
                print("✅ 使用缓存结果")
                return {
                    "_type": "cached",
                    "url": url,
                    "file": cached['file_path'],
                    "metadata": cached.get('metadata', {})
                }
            elif action == 's':
                print("⏸️ 已跳过本次请求")
                return {"_type": "skipped", "url": url}
            print("🔄 重新抓取...")

    # 抓取
    fetcher = AntiScrape(config)
    fetch_result = fetcher.fetch(url)
    if not fetch_result["success"]:
        print(f"❌ {fetch_result['error']}")
        if fetch_result.get("blocked") and config.USE_SELENIUM_FALLBACK:
            print("🔄 降级使用 Selenium...")
            selenium_fetcher = SeleniumFetcher(config)
            fetch_result = selenium_fetcher.fetch(url)
            if not fetch_result["success"]:
                print(f"❌ Selenium 降级失败: {fetch_result['error']}")
                return {"_type": "error", "error": fetch_result["error"]}
        else:
            return {"_type": "error", "error": fetch_result["error"]}

    html = fetch_result["html"]

    # 硬解析
    tables = Parser.extract_tables(html, url)
    lists = Parser.extract_lists(html)

    print(f"📊 硬解析结果: {len(tables)} 个表格, {len(lists)} 个列表")

    # 如果有数据，输出并缓存
    if tables or lists:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        domain = urlparse(url).netloc.replace("www.", "").replace(".", "_")
        prefix = f"{domain}_{timestamp}"
        saved_files = []

        for table in tables:
            idx = table["table_index"]
            headers = table["headers"]
            rows = table["rows"]

            csv_path = os.path.join(config.OUTPUT_DIR, f"{prefix}_table_{idx}.csv")
            save_csv(headers, rows, csv_path)
            saved_files.append(csv_path)

            md_path = os.path.join(config.OUTPUT_DIR, f"{prefix}_table_{idx}.md")
            save_markdown(headers, rows, md_path)
            saved_files.append(md_path)

            print(f"✅ 表格 {idx}: {len(rows)} 行 × {len(headers)} 列")

        for lst in lists:
            idx = lst["list_index"]
            items = lst["items"]
            list_path = os.path.join(config.OUTPUT_DIR, f"{prefix}_list_{idx}.json")
            with open(list_path, "w", encoding="utf-8") as f:
                json.dump({"items": items, "count": len(items)}, f, ensure_ascii=False, indent=2)
            saved_files.append(list_path)
            print(f"✅ 列表 {idx}: {len(items)} 项")

        summary_path = os.path.join(config.OUTPUT_DIR, f"{prefix}_summary.json")
        save_json({
            "url": url,
            "tables": tables,
            "lists": lists,
            "total_tables": len(tables),
            "total_lists": len(lists)
        }, summary_path)
        saved_files.append(summary_path)

        print(f"\n📁 输出目录: {config.OUTPUT_DIR}")
        for f in saved_files:
            print(f"   📄 {os.path.basename(f)}")

        # 缓存
        cache_mgr.set(url, summary_path, {"tables": len(tables), "lists": len(lists)})

        return {
            "_type": "web_data",
            "url": url,
            "tables": tables,
            "lists": lists,
            "files": saved_files
        }

    # 硬解析无数据 → LLM 辅助
    print("⚠️ 硬解析未发现数据，尝试 LLM 辅助分析...")
    llm_result = llm_assisted_parse(html, url, config)

    if llm_result.get("has_data"):
        print(f"🧠 LLM 识别到数据类型: {llm_result.get('data_type')}")
        print(f"📝 {llm_result.get('description', '')}")
        return {
            "_type": "web_data",
            "url": url,
            "llm_analysis": llm_result,
            "note": "硬解析未提取到数据，以上为 LLM 辅助分析结果，建议人工确认"
        }
    else:
        print("❌ 未发现任何可提取的数据")
        return {"_type": "error", "error": "未发现表格或列表数据"}


# ============================================================
# 命令行入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="网页抓取 Agent - 提取表格/列表数据并导出为 CSV/Markdown/JSON",
        epilog="示例: python web_agent.py https://www.kitco.com/market/ --force"
    )
    parser.add_argument("url", nargs="?", help="目标网页 URL")
    parser.add_argument("--force", action="store_true", help="强制重新抓取，忽略缓存")
    parser.add_argument("--no-cache", action="store_true", help="本次不使用缓存")
    parser.add_argument("--clear-cache", action="store_true", help="清除所有缓存")
    args = parser.parse_args()

    config = Config()

    print("=" * 60)
    print("🕸️ 网页抓取 Agent（智能缓存版）")
    print("📂 输出目录:", config.OUTPUT_DIR)
    print("🛡️ 反爬策略: UA 轮换 + 动态延迟 + 指数退避 + Cookie 持久化 + Referer 轮换")
    if config.PROXY_LIST:
        print(f"🌐 代理池: {len(config.PROXY_LIST)} 个代理已配置")
    if config.USE_SELENIUM_FALLBACK:
        print("🔄 Selenium 降级: 已启用")
    print("💾 缓存: 启用 (TTL: 24 小时)")
    print("=" * 60)

    # 清除缓存
    if args.clear_cache:
        cache_mgr = CacheManager(config)
        cache_mgr.clear()
        print("✅ 缓存已清除")
        return

    # 如果提供了 URL，直接运行
    if args.url:
        result = run_web_agent(args.url, config, force=args.force, no_cache=args.no_cache)
        # 以 JSON 格式输出结果（便于父Agent解析）
        if args.url and sys.stdout.isatty():
            # 交互模式打印友好信息
            pass
        else:
            # 非交互模式（父Agent调用）输出 JSON
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        return

    # 交互模式
    while True:
        url = input("\n❓ 请输入网页 URL (输入 exit 退出): ").strip()
        if url.lower() in ["exit", "quit", "q"]:
            break
        if not url:
            continue
        run_web_agent(url, config)


if __name__ == "__main__":
    main()