#!/usr/bin/env python
# -*- coding: utf-8 -*-
# db_agent.py
# 数据分析 Agent - 基于 ReAct + Function Calling
# 核心理念：让 LLM 成为决策者，系统只提供信息和执行工具

import os
import sys
import io
import json
import time
import re
from typing import Dict, List, Tuple
from datetime import datetime, date
from decimal import Decimal

# ============================================================
# 【企业级修复】强制 Windows 控制台输出 UTF-8，防止 json.dump 报 GBK 编码错误
# ============================================================
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
# ============================================================

import pymysql
import requests
from dotenv import load_dotenv


class Config:
    SILICON_API_KEY = os.getenv("SILICON_API_KEY")
    MODEL_NAME = "deepseek-ai/DeepSeek-V4-Flash"
    SILICON_BASE_URL = "https://api.siliconflow.cn/v1"
    LLM_TIMEOUT = 60
    LLM_RETRY_MAX = 3
    RETRY_BACKOFF_BASE = 2

    DB_CONFIG = {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "root",
        "password": "123456",
        "database": "sales_db",
        "charset": "utf8mb4"
    }

    MAX_SAMPLE_SIZE = 50


def make_serializable(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [make_serializable(item) for item in obj]
    if isinstance(obj, str):
        return ''.join(ch for ch in obj if ch.isprintable() or ch in '\n\r\t').strip()
    return obj


def get_db_schema(config: Config) -> Dict:
    try:
        conn = pymysql.connect(**config.DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        schema = {}
        for table in tables:
            cursor.execute(f"DESCRIBE {table}")
            fields = cursor.fetchall()
            schema[table] = [{"Field": f[0], "Type": f[1], "Null": f[2], "Key": f[3]} for f in fields]
        cursor.close()
        conn.close()
        return schema
    except Exception as e:
        return {"error": str(e)}


def query_sales_db(sql: str, config: Config) -> Dict:
    try:
        conn = pymysql.connect(**config.DB_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        start_time = time.time()
        cursor.execute(sql)
        result = cursor.fetchall()
        cursor.close()
        conn.close()
        elapsed = time.time() - start_time

        total = len(result)
        max_sample = config.MAX_SAMPLE_SIZE
        sample = make_serializable(result[:max_sample])

        stats = {}
        if result and isinstance(result[0], dict):
            numeric_fields = [k for k, v in result[0].items() if isinstance(v, (int, float, Decimal))]
            for field in numeric_fields:
                values = [row.get(field, 0) for row in result if row.get(field) is not None]
                if values:
                    stats[field] = {
                        "总和": sum(values),
                        "平均值": round(sum(values) / len(values), 2),
                        "最大值": max(values),
                        "最小值": min(values)
                    }

        # 交互模式下才打印，命令行模式下被屏蔽
        print(f"\n📊 查询结果：共 {total} 条记录（耗时 {elapsed:.2f}s）")
        if total > max_sample:
            print(f"   ⚠️ 数据量超过阈值（{max_sample}），仅展示样本 + 统计摘要")
        if stats:
            for field, v in stats.items():
                print(f"   📈 {field}: 总和 {v['总和']}, 平均 {v['平均值']}, 最大 {v['最大值']}, 最小 {v['最小值']}")
        if sample:
            sample_str = json.dumps(sample, ensure_ascii=False)
            print(f"   📋 样本（前 {len(sample)} 条）：{sample_str[:300]}{'...' if len(sample_str) > 300 else ''}")

        return {
            "_type": "sales_data",
            "total": total,
            "sample": sample,
            "stats": stats,
            "query_time": elapsed
        }
    except Exception as e:
        error_msg = f"查询失败: {str(e)}"
        print(f"❌ {error_msg}")
        return {"_type": "error", "error": error_msg}


def analyze_trend(data: Dict, field: str) -> Dict:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {"_type": "error", "error": "data 参数格式错误，请传递 query_sales_db 返回的完整对象"}

    if not isinstance(data, dict):
        return {"_type": "error", "error": "data 参数必须是字典，请传递 query_sales_db 返回的完整对象"}

    if data.get("_type") != "sales_data":
        return {"_type": "error", "error": "data 参数必须来自 query_sales_db（_type 应为 sales_data）"}

    stats = data.get("stats", {})
    field_stats = stats.get(field, {})
    total = data.get("total", 0)

    if not field_stats:
        return {"_type": "error", "error": f"没有找到字段 '{field}' 的统计信息"}

    max_val = field_stats.get("最大值")
    min_val = field_stats.get("最小值")
    avg_val = field_stats.get("平均值")
    total_val = field_stats.get("总和")

    conclusion = f"字段 '{field}' 共 {total} 条记录，总和为 {total_val}，平均值为 {avg_val}。"
    if max_val is not None and min_val is not None:
        if max_val - min_val > avg_val * 0.5:
            conclusion += f" 数据波动较大（最大值 {max_val}，最小值 {min_val}），建议关注异常点。"
        else:
            conclusion += f" 数据相对平稳（最大值 {max_val}，最小值 {min_val}）。"

    print(f"\n📈 分析结果（{field}）：")
    print(f"   {conclusion}")

    return {
        "_type": "analysis_result",
        "field": field,
        "stats": field_stats,
        "conclusion": conclusion
    }


def generate_report(title: str, content: str) -> str:
    if isinstance(content, dict) and content.get("_type") == "analysis_result":
        content = content.get("conclusion", "无结论")
    elif isinstance(content, str):
        try:
            data = json.loads(content)
            if isinstance(data, dict) and data.get("_type") == "analysis_result":
                content = data.get("conclusion", "无结论")
        except:
            pass

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = f"""
# {title}

**生成时间**: {timestamp}

## 分析结论

{content}

---
*报告由 AI Agent 自动生成*
"""
    print(f"\n📄 报告已生成：{title}")
    return report


def request_steps(additional: int) -> str:
    print(f"\n📈 申请额外步数 {additional}")
    return f"已批准，剩余步数已增加 {additional} 步。"


def call_llm(messages: List[Dict], config: Config, tool_defs: List[Dict]) -> Dict:
    url = f"{config.SILICON_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.SILICON_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": config.MODEL_NAME,
        "messages": messages,
        "tools": tool_defs,
        "tool_choice": "auto",
        "temperature": 0.3,
        "max_tokens": 2048
    }

    for attempt in range(config.LLM_RETRY_MAX):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                wait = config.RETRY_BACKOFF_BASE ** (attempt + 1) * 2
                print(f"⏳ 限流，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            else:
                return {"error": f"API 请求失败: {response.status_code}"}
        except requests.exceptions.Timeout:
            if attempt == config.LLM_RETRY_MAX - 1:
                return {"error": "请求超时"}
            wait = config.RETRY_BACKOFF_BASE ** (attempt + 1)
            print(f"⏳ 超时，等待 {wait}s 后重试...")
            time.sleep(wait)
        except requests.exceptions.ConnectionError:
            if attempt == config.LLM_RETRY_MAX - 1:
                return {"error": "网络连接失败"}
            wait = config.RETRY_BACKOFF_BASE ** (attempt + 1)
            print(f"⏳ 网络错误，等待 {wait}s 后重试...")
            time.sleep(wait)
        except Exception as e:
            if attempt == config.LLM_RETRY_MAX - 1:
                return {"error": str(e)}
            time.sleep(config.RETRY_BACKOFF_BASE)

    return {"error": "重试失败"}


def build_system_prompt(schema: Dict) -> str:
    table_desc = ""
    for table, fields in schema.items():
        field_list = ", ".join([f["Field"] for f in fields])
        table_desc += f"- **{table}**: {field_list}\n"

    return f"""
你是一个专业的智能数据分析 Agent。

## 环境信息
- 数据库：sales_db（MySQL 8.0，运行在 Docker 中）
- 表结构：
{table_desc}

## 可用工具
1. `query_sales_db(sql)`：执行 SELECT 查询，返回结构化数据（_type: sales_data）
2. `analyze_trend(data, field)`：分析趋势，返回自然语言结论（data 必须来自 query_sales_db 的返回值）
3. `generate_report(title, content)`：生成 Markdown 报告
4. `request_steps(additional)`：申请额外步数

## 重要规则
1. `analyze_trend` 的 `data` 参数必须直接传递 `query_sales_db` 的返回值
2. 当你认为数据已足够回答问题，立即调用 `generate_report`
3. 当你需要更多步数，调用 `request_steps` 申请
4. 当连续调用相同工具且结果无变化时，请改变策略

## 你的任务
分析用户问题，调用合适的工具，最终生成一份完整的 Markdown 报告。
"""


class CircleDetector:
    def __init__(self, window: int = 3):
        self.window = window
        self.history: List[str] = []

    def compute_fingerprint(self, result: str) -> str:
        try:
            data = json.loads(result)
            if isinstance(data, dict):
                if data.get("_type") == "sales_data":
                    return f"sales_data:{data.get('total', 0)}:{list(data.get('stats', {}).keys())}"
                elif data.get("_type") == "analysis_result":
                    return f"analysis_result:{data.get('field', '')}:{data.get('conclusion', '')[:30]}"
                return str(data.get("total", 0))
            return result[:100]
        except:
            return result[:100]

    def check(self, tool_name: str, args: Dict, result: str) -> Tuple[bool, str]:
        fingerprint = f"{tool_name}:{self.compute_fingerprint(result)}"
        self.history.append(fingerprint)
        if len(self.history) > self.window:
            self.history.pop(0)
        if len(self.history) == self.window and len(set(self.history)) == 1:
            return True, fingerprint
        return False, fingerprint


def execute_tool(tool_name: str, tool_args: Dict, config: Config) -> str:
    tool_map = {
        "query_sales_db": lambda args: query_sales_db(args["sql"], config),
        "analyze_trend": lambda args: analyze_trend(args["data"], args["field"]),
        "generate_report": lambda args: generate_report(args["title"], args["content"]),
        "request_steps": lambda args: request_steps(args["additional"]),
    }
    if tool_name in tool_map:
        result = tool_map[tool_name](tool_args)
        return json.dumps(make_serializable(result), ensure_ascii=False, indent=2)
    return json.dumps({"error": f"未知工具: {tool_name}"})


def run_agent(user_question: str, schema: Dict, config: Config):
    system_prompt = build_system_prompt(schema)
    messages = [{"role": "system", "content": system_prompt}]

    tool_defs = [
        {
            "type": "function",
            "function": {
                "name": "query_sales_db",
                "description": "执行 SELECT 查询，返回结构化数据（_type: sales_data）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SELECT SQL 语句"}
                    },
                    "required": ["sql"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "analyze_trend",
                "description": "分析趋势，data 必须来自 query_sales_db 的返回值",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "data": {"type": "object", "description": "query_sales_db 返回的对象（含 _type: sales_data）"},
                        "field": {"type": "string", "description": "要分析的字段名"}
                    },
                    "required": ["data", "field"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "generate_report",
                "description": "生成 Markdown 报告",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "报告标题"},
                        "content": {"type": "string", "description": "报告正文"}
                    },
                    "required": ["title", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "request_steps",
                "description": "申请额外步数",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "additional": {"type": "integer", "description": "需要的额外步数"}
                    },
                    "required": ["additional"]
                }
            }
        }
    ]

    # 初始步数
    max_steps = 5
    total_steps = 0
    no_progress_count = 0
    detector = CircleDetector()
    final_answer = None

    print(f"\n🧠 用户问题: {user_question}")
    print(f"📋 初始步数: {max_steps}\n{'='*50}\n")

    # 首次调用：用户问题
    messages.append({"role": "user", "content": user_question})

    while True:
        total_steps += 1
        remaining = max_steps - total_steps

        print(f"\n--- Step {total_steps} (剩余 {remaining} 步) ---")

        # 如果剩余步数 <= 2，在消息中加入提示（但不强制中断）
        if remaining <= 2:
            messages.append({
                "role": "system",
                "content": f"当前剩余步数: {remaining}。如需更多步数，请调用 request_steps 申请。"
            })

        response = call_llm(messages, config, tool_defs)
        if "error" in response:
            print(f"❌ {response['error']}")
            break

        assistant = response["choices"][0]["message"]

        if assistant.get("tool_calls"):
            tool_call = assistant["tool_calls"][0]
            tool_name = tool_call["function"]["name"]
            tool_args = json.loads(tool_call["function"]["arguments"])

            print(f"🔧 调用工具: {tool_name}")
            print(f"📝 参数: {json.dumps(tool_args, ensure_ascii=False)}")

            if tool_name == "request_steps":
                additional = tool_args.get("additional", 0)
                max_steps += additional
                print(f"📈 已批准，当前上限: {max_steps} 步")
                tool_result = json.dumps({"status": "已批准", "total": max_steps, "remaining": max_steps - total_steps})
                no_progress_count = 0
            else:
                tool_result = execute_tool(tool_name, tool_args, config)

                # 判断是否有进展
                if "error" in tool_result.lower() or "未找到" in tool_result:
                    no_progress_count += 1
                    print(f"⚠️ 无进展计数: {no_progress_count}")
                else:
                    no_progress_count = 0

                # 死循环检测：只检测，不强制终止，仅提示
                is_circle, fingerprint = detector.check(tool_name, tool_args, tool_result)
                if is_circle:
                    messages.append({
                        "role": "system",
                        "content": "⚠️ 检测到连续 3 次调用相同工具且结果无变化。请考虑改变策略或申请更多步数。"
                    })
                    no_progress_count = 0

            # 保底：连续 5 次无进展
            if no_progress_count >= 5:
                print("⚠️ 连续 5 次调用无进展，触发保底总结")
                break

            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [tool_call]
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": tool_result
            })

        else:
            final_answer = assistant.get("content", "")
            print(f"\n📋 最终回答:\n{final_answer}")
            break

    if final_answer is None:
        print("\n⚠️ 系统保底：生成阶段性总结...")
        summary_prompt = f"""
用户问题: {user_question}
当前已完成步骤: {total_steps}
根据已有信息，生成一份阶段性总结报告。
"""
        summary_messages = [
            {"role": "system", "content": "你是数据分析助手，生成阶段性总结。"},
            {"role": "user", "content": summary_prompt}
        ]
        summary_response = call_llm(summary_messages, config, tool_defs)
        if "error" not in summary_response:
            final_answer = summary_response["choices"][0]["message"].get("content", "未能生成总结")
        else:
            final_answer = "任务未完成，请重新提问。"
        print(f"\n📋 保底总结:\n{final_answer}")

    return final_answer


def main():
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(env_path)

    config = Config()
    config.SILICON_API_KEY = os.getenv('SILICON_API_KEY')

    print("=" * 50)
    print("🤖 数据分析 Agent")
    print("=" * 50)

    print("🔍 正在探索数据库表结构...")
    schema = get_db_schema(config)
    if "error" in schema:
        print(f"❌ 表结构探索失败: {schema['error']}")
        return

    print("✅ 表结构加载完成")
    print(f"📊 发现表: {list(schema.keys())}")

    while True:
        question = input("\n❓ 请输入你的问题 (输入 exit 退出): ")
        if question.lower() in ["exit", "quit", "q"]:
            break
        if not question.strip():
            continue
        run_agent(question, schema, config)


# ============================================================
# 命令行入口（专为父 Agent 调用设计）
# ============================================================
if __name__ == "__main__":
    import sys
    import json
    import re
    import io
    from contextlib import redirect_stdout

    load_dotenv()

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        config = Config()
        config.SILICON_API_KEY = os.getenv('SILICON_API_KEY')
        schema = get_db_schema(config)
        if "error" in schema:
            # 错误信息直接输出 JSON（使用 buffer 确保 UTF-8）
            sys.stdout.buffer.write(json.dumps({"_type": "error", "error": schema["error"]}, ensure_ascii=False).encode('utf-8'))
            sys.exit(1)

        # 构建 SQL 生成 Prompt
        prompt = f"""
你是 SQL 专家。请根据用户问题生成 SQL 查询语句。
用户问题：{question}
表结构：{json.dumps(schema, ensure_ascii=False)}
只输出 SQL 查询语句，不要其他内容。查询必须是 SELECT 语句。
"""
        sql_response = call_llm([{"role": "user", "content": prompt}], config, [])

        # 处理返回值
        if isinstance(sql_response, dict):
            if "error" in sql_response:
                sys.stdout.buffer.write(json.dumps({"_type": "error", "error": sql_response["error"]}, ensure_ascii=False).encode('utf-8'))
                sys.exit(1)
            if "choices" in sql_response and sql_response["choices"]:
                sql_text = sql_response["choices"][0]["message"]["content"]
            else:
                sql_text = sql_response.get("content", str(sql_response))
        else:
            sql_text = sql_response

        if isinstance(sql_text, str) and sql_text.startswith("❌"):
            sys.stdout.buffer.write(json.dumps({"_type": "error", "error": sql_text}, ensure_ascii=False).encode('utf-8'))
            sys.exit(1)

        # 提取 SQL（清理所有空白字符）
        sql_match = re.search(r"(SELECT.*?)(?:;|$)", sql_text, re.IGNORECASE | re.DOTALL)
        if not sql_match:
            sys.stdout.buffer.write(json.dumps({"_type": "error", "error": "无法提取 SQL 语句"}, ensure_ascii=False).encode('utf-8'))
            sys.exit(1)
        sql = sql_match.group(1).strip()
        sql = re.sub(r'\s+', ' ', sql)

        # 执行查询（屏蔽所有 print 输出）
        with io.StringIO() as buf, redirect_stdout(buf):
            result = query_sales_db(sql, config)

        if result.get("_type") == "error":
            sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
            sys.exit(1)

        # 构造表格数据
        sample = result.get("sample", [])
        stats = result.get("stats", {})
        if sample:
            headers = list(sample[0].keys())
            rows = sample
        else:
            headers = []
            rows = []

        summary = f"查询到 {result.get('total', 0)} 条记录"
        if stats:
            for field, v in stats.items():
                summary += f"，{field} 总和 {v.get('总和', 0)}"

        output = {
            "_type": "web_data",
            "tables": [{
                "headers": headers,
                "rows": rows,
                "row_count": len(rows),
                "column_count": len(headers)
            }],
            "summary": summary,
            "total": result.get("total", 0)
        }
        # 【企业级修复】使用 buffer 直接写入 UTF-8 字节流
        sys.stdout.buffer.write(json.dumps(output, ensure_ascii=False, indent=2).encode('utf-8'))
    else:
        main()