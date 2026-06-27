#!/usr/bin/env python
# -*- coding: utf-8 -*-
# info_agent.py
# 信息检索 Agent - 智能版
# 功能：查询改写 | 智能摘要（强制带来源） | 结果缓存 | ReAct 追问（自动补充搜索） | 异步搜索

import os
import sys
import json
import time
import asyncio
import aiohttp
import hashlib
import re
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv


# ============================================================
# 配置
# ============================================================
class Config:
    def __init__(self):
        self.TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
        self.SILICON_API_KEY = os.getenv("SILICON_API_KEY")
        self.TAVILY_BASE_URL = "https://api.tavily.com"
        self.LLM_BASE_URL = "https://api.siliconflow.cn/v1"
        self.MODEL_NAME = "deepseek-ai/DeepSeek-V4-Flash"
        self.TAVILY_TIMEOUT = 30
        self.LLM_TIMEOUT = 60
        self.CACHE_TTL = 3600 * 24 * 7
        self.MAX_SEARCH_THREADS = 3


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(env_path)


# ============================================================
# 缓存管理
# ============================================================
class SearchCache:
    def __init__(self, cache_file: str = "search_cache.json"):
        self.cache_file = cache_file
        self.data = self._load()

    def _load(self) -> Dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save(self):
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get(self, query: str) -> Optional[Dict]:
        key = hashlib.md5(query.encode()).hexdigest()
        if key in self.data:
            entry = self.data[key]
            cached_time = datetime.fromisoformat(entry.get("cached_at", "2000-01-01T00:00:00"))
            if datetime.now() - cached_time < timedelta(seconds=Config().CACHE_TTL):
                print(f"💾 命中缓存: {query[:30]}...")
                return entry.get("result")
        return None

    def set(self, query: str, result: Dict):
        key = hashlib.md5(query.encode()).hexdigest()
        self.data[key] = {
            "query": query,
            "result": result,
            "cached_at": datetime.now().isoformat()
        }
        self._save()


# ============================================================
# Tavily 搜索
# ============================================================
class TavilySearch:
    def __init__(self, config: Config):
        self.config = config

    def search(self, query: str, max_results: int = 5) -> Dict:
        return self._search_sync(query, max_results)

    async def search_async(self, query: str, max_results: int = 5) -> Dict:
        return await self._search_async(query, max_results)

    def _search_sync(self, query: str, max_results: int) -> Dict:
        if not self.config.TAVILY_API_KEY:
            return {"_type": "error", "error": "TAVILY_API_KEY 未配置"}

        url = f"{self.config.TAVILY_BASE_URL}/search"
        headers = {"Content-Type": "application/json"}
        payload = {
            "api_key": self.config.TAVILY_API_KEY,
            "query": query,
            "max_results": max_results,
            "include_answer": True,
            "include_raw_content": False
        }

        for attempt in range(2):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=self.config.TAVILY_TIMEOUT)
                if response.status_code == 200:
                    data = response.json()
                    return self._format_results(data, query)
                elif response.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    print(f"⏳ 限流，等待 {wait}s 后重试...")
                    time.sleep(wait)
                    continue
                else:
                    return {"_type": "error", "error": f"搜索失败: {response.status_code}"}
            except requests.exceptions.Timeout:
                if attempt == 1:
                    return {"_type": "error", "error": "搜索超时"}
                time.sleep(2)
            except Exception as e:
                if attempt == 1:
                    return {"_type": "error", "error": str(e)}
                time.sleep(2)
        return {"_type": "error", "error": "搜索失败"}

    async def _search_async(self, query: str, max_results: int) -> Dict:
        if not self.config.TAVILY_API_KEY:
            return {"_type": "error", "error": "TAVILY_API_KEY 未配置"}

        url = f"{self.config.TAVILY_BASE_URL}/search"
        headers = {"Content-Type": "application/json"}
        payload = {
            "api_key": self.config.TAVILY_API_KEY,
            "query": query,
            "max_results": max_results,
            "include_answer": True,
            "include_raw_content": False
        }

        for attempt in range(2):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, headers=headers,
                                            timeout=aiohttp.ClientTimeout(total=self.config.TAVILY_TIMEOUT)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return self._format_results(data, query)
                        elif resp.status == 429:
                            wait = 2 ** (attempt + 1)
                            print(f"⏳ 限流，等待 {wait}s 后重试...")
                            await asyncio.sleep(wait)
                            continue
                        else:
                            return {"_type": "error", "error": f"搜索失败: {resp.status}"}
            except asyncio.TimeoutError:
                if attempt == 1:
                    return {"_type": "error", "error": "搜索超时"}
                await asyncio.sleep(2)
            except Exception as e:
                if attempt == 1:
                    return {"_type": "error", "error": str(e)}
                await asyncio.sleep(2)
        return {"_type": "error", "error": "搜索失败"}

    def _format_results(self, data: Dict, query: str) -> Dict:
        results = data.get("results", [])
        answer = data.get("answer", "")
        formatted = []
        for item in results[:10]:
            formatted.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "score": item.get("score", 0)
            })
        return {
            "_type": "search_results",
            "query": query,
            "answer": answer,
            "results": formatted,
            "total_results": len(formatted)
        }


# ============================================================
# LLM 工具
# ============================================================
def call_llm(messages: List[Dict], config: Config) -> str:
    url = f"{config.LLM_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.SILICON_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": config.MODEL_NAME,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1024
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        return f"❌ LLM 调用失败: {response.status_code}"
    except Exception as e:
        return f"❌ LLM 调用异常: {str(e)}"


def rewrite_query(user_query: str, config: Config) -> str:
    prompt = f"""
你是一个搜索关键词优化专家。用户的问题是："{user_query}"
请提取 2-3 个最核心的搜索关键词，用空格分隔，不要包含标点符号，不要有多余的说明文字。
"""
    messages = [{"role": "user", "content": prompt}]
    result = call_llm(messages, config)
    if result.startswith("❌"):
        return user_query
    return result.strip()


def generate_summary_with_sources(query: str, search_results: Dict, config: Config) -> str:
    """
    生成带来源标注的智能摘要。如果 LLM 生成失败，使用规则组装结构化摘要。
    """
    if search_results.get("_type") == "error":
        return f"搜索失败：{search_results.get('error')}"

    results_list = search_results.get("results", [])
    if not results_list:
        return "没有找到相关信息。"

    # 构建来源文本
    sources_text = ""
    for i, item in enumerate(results_list[:5], 1):
        sources_text += f"### 来源 {i}: {item['title']}\n{item['content']}\n链接: {item['url']}\n\n"

    # 构建来源列表（用于末尾）
    source_list = "\n\n---\n\n**📚 来源列表**\n\n"
    for i, item in enumerate(results_list[:5], 1):
        source_list += f"{i}. [{item['title']}]({item['url']})\n"

    prompt = f"""
你是一个信息整理专家。用户搜索了："{query}"

以下是搜索到的相关文章摘要（每篇已标注编号）：

{sources_text}

请根据以上信息，生成一份结构清晰的信息简报，要求：
1. **核心结论**（1-2句话概括）
2. **主要观点**（3-4个要点，每条观点末尾用 [来源 N] 标注出处）
3. **建议**（如果有的话）

格式要求：使用 Markdown，条理清晰。观点必须基于所提供的文章内容，不要编造。
"""
    messages = [{"role": "user", "content": prompt}]
    result = call_llm(messages, config)

    # 如果 LLM 生成成功，追加来源列表
    if not result.startswith("❌"):
        return result + source_list

    # 降级：使用规则组装
    print("⚠️ LLM 摘要生成失败，使用规则组装...")
    fallback = f"## 📄 关于「{query}」的信息汇总\n\n"
    for i, item in enumerate(results_list[:5], 1):
        fallback += f"### {i}. {item['title']}\n"
        fallback += f"{item['content']}\n"
        fallback += f"🔗 {item['url']}\n\n"
    fallback += source_list
    return fallback


# ============================================================
# ReAct 追问（自动补充搜索）
# ============================================================
def follow_up_with_search(question: str, previous_results: Dict, config: Config) -> Dict:
    """
    处理追问 - ReAct 循环：
    1. LLM 判断是否需要搜索
    2. 如果需要，执行搜索并更新上下文
    3. 最终生成回答（附来源）
    """
    results_list = previous_results.get("results", [])
    if not results_list:
        print("🔍 没有历史结果，直接发起新搜索...")
        search_result = run_enhanced_search(question, config, use_cache=True, use_llm=True)
        return {
            "_type": "follow_up_result",
            "need_search": True,
            "answer": search_result.get("llm_summary", "无摘要"),
            "search_performed": True,
            "search_result": search_result
        }

    # 构建上下文
    context = ""
    source_map = {}
    for i, item in enumerate(results_list[:5], 1):
        context += f"来源 {i}: {item['title']}\n{item['content'][:500]}\n链接: {item['url']}\n\n"
        source_map[str(i)] = {"title": item['title'], "url": item['url']}

    # Step 1: Thought - LLM 判断是否需要搜索
    thought_prompt = f"""
你是一个智能信息分析助手。用户之前搜索了，现在提出了追问。

【已有搜索结果】
{context}

【用户追问】
{question}

请判断：仅凭已有搜索结果，能否充分回答用户的追问？
- 如果已有信息足够回答，请输出：{{"action": "answer", "answer": "你的回答内容"}}
- 如果已有信息不足，需要搜索更多资料，请输出：{{"action": "search", "query": "建议的搜索关键词"}}

输出必须是纯 JSON 格式，不要包含其他文字。
"""
    messages = [{"role": "user", "content": thought_prompt}]
    thought_response = call_llm(messages, config)

    if thought_response.startswith("❌"):
        print("⚠️ LLM 思考失败，降级为直接搜索...")
        search_result = run_enhanced_search(question, config, use_cache=True, use_llm=True)
        return {
            "_type": "follow_up_result",
            "need_search": True,
            "answer": search_result.get("llm_summary", "无摘要"),
            "search_performed": True,
            "search_result": search_result
        }

    # 解析 JSON
    try:
        json_match = re.search(r'\{.*\}', thought_response, re.DOTALL)
        if json_match:
            decision = json.loads(json_match.group())
        else:
            if "需要搜索" in thought_response or "搜索" in thought_response:
                decision = {"action": "search", "query": question}
            else:
                decision = {"action": "answer", "answer": thought_response}
    except:
        decision = {"action": "search", "query": question}

    # Step 2: Action - 执行决策
    if decision.get("action") == "search":
        search_query = decision.get("query", question)
        print(f"🔍 LLM 决定发起新搜索: {search_query}")
        search_result = run_enhanced_search(search_query, config, use_cache=True, use_llm=True)

        if search_result.get("_type") == "error":
            return {
                "_type": "follow_up_result",
                "need_search": True,
                "answer": f"搜索失败：{search_result.get('error')}",
                "search_performed": True,
                "search_result": None
            }

        # 从新搜索结果中提取来源列表
        new_results = search_result.get("results", [])
        source_list = "\n\n---\n\n**📚 来源列表**\n\n"
        for i, item in enumerate(new_results[:5], 1):
            source_list += f"{i}. [{item.get('title', '无标题')}]({item.get('url', '#')})\n"

        # Step 3: Observation + Final Answer
        final_prompt = f"""
用户之前搜索了，现在追问："{question}"

以下是新搜索到的相关信息：

{search_result.get("llm_summary", "无摘要")}

请根据以上新信息回答用户的问题。如果仍然没有相关信息，请明确告知。
"""
        final_messages = [{"role": "user", "content": final_prompt}]
        final_answer = call_llm(final_messages, config)
        if final_answer.startswith("❌"):
            final_answer = search_result.get("llm_summary", "无法获取更多信息。")

        # 在回答末尾附加来源列表
        final_answer_with_sources = final_answer + source_list

        return {
            "_type": "follow_up_result",
            "need_search": True,
            "answer": final_answer_with_sources,
            "search_performed": True,
            "search_result": search_result
        }
    else:
        answer = decision.get("answer", "无法回答。")

        # 从已有结果中提取来源列表
        source_list = "\n\n---\n\n**📚 来源列表**\n\n"
        for i, item in enumerate(results_list[:5], 1):
            source_list += f"{i}. [{item.get('title', '无标题')}]({item.get('url', '#')})\n"

        answer_with_sources = answer + source_list

        return {
            "_type": "follow_up_result",
            "need_search": False,
            "answer": answer_with_sources,
            "search_performed": False
        }


# ============================================================
# 并行搜索
# ============================================================
async def search_multiple_async(keywords: List[str], config: Config, max_results: int = 3) -> List[Dict]:
    tavily = TavilySearch(config)
    tasks = [tavily.search_async(kw, max_results) for kw in keywords]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    final = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            final.append({"_type": "error", "error": str(result), "query": keywords[i]})
        else:
            final.append(result)
    return final


# ============================================================
# 意图识别
# ============================================================
def classify_intent(user_input: str, config: Config, session) -> Dict:
    if not session.last_result:
        return {"intent": "new_search", "query": user_input}

    prompt = f"""
你是一个对话理解专家。用户之前搜索了："{session.last_query}"

现在用户说："{user_input}"

请判断用户是想：
1. 开始一个全新的搜索（new_search）
2. 对上一次搜索结果进行追问（follow_up）

只输出 JSON 格式：{{"intent": "new_search"}} 或 {{"intent": "follow_up", "question": "用户的问题"}}
"""
    messages = [{"role": "user", "content": prompt}]
    try:
        response = call_llm(messages, config)
        if response.startswith("❌"):
            return {"intent": "new_search", "query": user_input}
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            if data.get("intent") == "follow_up":
                return {"intent": "follow_up", "question": data.get("question", user_input)}
            else:
                return {"intent": "new_search", "query": user_input}
        else:
            return {"intent": "new_search", "query": user_input}
    except:
        return {"intent": "new_search", "query": user_input}


# ============================================================
# 主搜索逻辑
# ============================================================
def run_enhanced_search(user_query: str, config: Config, use_cache: bool = True, use_llm: bool = True) -> Dict:
    cache = SearchCache()

    search_keywords = user_query
    if use_llm and config.SILICON_API_KEY:
        print("🧠 正在优化搜索关键词...")
        rewritten = rewrite_query(user_query, config)
        if not rewritten.startswith("❌"):
            search_keywords = rewritten
            print(f"   ✏️ 关键词优化: {search_keywords}")

    if use_cache:
        cached = cache.get(search_keywords)
        if cached:
            if use_llm and config.SILICON_API_KEY and not cached.get("llm_summary"):
                print("🧠 缓存命中，但缺少摘要，补充生成...")
                summary = generate_summary_with_sources(user_query, cached, config)
                cached["llm_summary"] = summary
                cache.set(search_keywords, cached)
            return cached

    tavily = TavilySearch(config)
    keywords = [kw.strip() for kw in search_keywords.split() if kw.strip()]
    if len(keywords) > 1:
        print(f"🔍 并行搜索 {len(keywords[:3])} 个关键词...")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            search_results = loop.run_until_complete(search_multiple_async(keywords[:3], config, max_results=3))
            loop.close()
            all_results = []
            all_answer = ""
            for sr in search_results:
                if sr.get("_type") != "error":
                    all_results.extend(sr.get("results", []))
                    if sr.get("answer"):
                        all_answer += sr.get("answer", "") + "\n"
            seen_urls = set()
            unique_results = []
            for item in all_results:
                url = item.get("url", "")
                if url not in seen_urls:
                    seen_urls.add(url)
                    unique_results.append(item)
            result = {
                "_type": "search_results",
                "query": user_query,
                "answer": all_answer.strip(),
                "results": unique_results[:10],
                "total_results": len(unique_results)
            }
        except Exception as e:
            print(f"⚠️ 异步搜索失败，回退到同步搜索: {e}")
            result = tavily.search(search_keywords, max_results=5)
    else:
        print(f"🔍 正在搜索: {search_keywords}")
        result = tavily.search(search_keywords, max_results=5)

    if result.get("_type") == "error":
        return result

    if use_llm and config.SILICON_API_KEY:
        print("🧠 正在生成智能摘要...")
        summary = generate_summary_with_sources(user_query, result, config)
        if not summary.startswith("❌"):
            result["llm_summary"] = summary

    if use_cache and result.get("_type") != "error":
        cache.set(search_keywords, result)

    return result


def print_result(result: Dict):
    if result.get("_type") == "error":
        print(f"❌ {result.get('error')}")
        return

    if result.get("llm_summary"):
        print("\n" + "=" * 60)
        print("📄 **智能简报**")
        print("=" * 60)
        print(result["llm_summary"])
        print("=" * 60)
    else:
        answer = result.get("answer", "")
        if answer:
            print(f"\n📌 {answer}")
        print("\n📋 **详细来源**")
        for i, item in enumerate(result.get("results", [])[:5], 1):
            print(f"{i}. {item['title']}")
            print(f"   {item['content'][:150]}...")
            print(f"   🔗 {item['url']}\n")


def print_help():
    print("\n📖 可用指令:")
    print("   - 直接输入问题进行搜索")
    print("   - 输入追问内容，系统自动判断是否追问或发起新搜索")
    print("   - /clear 清除会话历史")
    print("   - /exit 退出")


# ============================================================
# 会话管理
# ============================================================
class Session:
    def __init__(self):
        self.last_query = None
        self.last_result = None
        self.history = []


# ============================================================
# 主程序
# ============================================================
def main():
    load_env()
    config = Config()
    session = Session()

    print("=" * 60)
    print("🔍 信息检索 Agent (五合一智能版)")
    print("=" * 60)
    print(f"   ✅ 查询改写 | 智能摘要 | 结果缓存 | ReAct 追问 | 异步搜索")
    print(f"   Tavily: {'已配置' if config.TAVILY_API_KEY else '❌ 未配置'}")
    print(f"   LLM:    {'已配置' if config.SILICON_API_KEY else '❌ 未配置 (降级)'}")
    print("=" * 60)
    print_help()

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        result = run_enhanced_search(query, config)
        print_result(result)
        session.last_query = query
        session.last_result = result
        return

    while True:
        user_input = input("\n❓ 请输入: ").strip()
        if not user_input:
            continue

        if user_input.lower() in ["exit", "/exit", "quit", "q"]:
            break

        if user_input == "/help":
            print_help()
            continue

        if user_input == "/clear":
            session = Session()
            print("✅ 会话已清除")
            continue

        # 意图识别
        if config.SILICON_API_KEY and session.last_result:
            intent_data = classify_intent(user_input, config, session)
            if intent_data.get("intent") == "follow_up":
                print("🧠 识别为追问，启动 ReAct 循环...")
                result = follow_up_with_search(
                    intent_data.get("question", user_input),
                    session.last_result,
                    config
                )
                if result.get("_type") == "follow_up_result":
                    if result.get("search_performed") and result.get("search_result"):
                        print("📥 已获取新信息，更新上下文...")
                        session.last_result = result["search_result"]
                        session.last_query = result.get("query", user_input)
                    print(f"\n💬 {result.get('answer', '无回答')}")
                    continue
            else:
                # 新搜索
                result = run_enhanced_search(user_input, config)
                print_result(result)
                session.last_query = user_input
                session.last_result = result
        else:
            # 无 LLM 或没有上次结果，直接搜索
            result = run_enhanced_search(user_input, config)
            print_result(result)
            session.last_query = user_input
            session.last_result = result


if __name__ == "__main__":
    main()