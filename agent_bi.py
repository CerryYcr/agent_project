#!/usr/bin/env python
# -*- coding: utf-8 -*-
# agent_bi.py
# 主 Agent —— ReAct 循环 + 子 Agent 调度 + BI 可视化（智能图表选择 + 可追溯命名 + 中文支持 + 协作模式）

import os
import sys
import json
import re
import subprocess
import time
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# 配置
# ============================================================
class Config:
    def __init__(self):
        self.SILICON_API_KEY = os.getenv("SILICON_API_KEY")
        self.MODEL_NAME = "deepseek-ai/DeepSeek-V4-Flash"
        self.SILICON_BASE_URL = "https://api.siliconflow.cn/v1"
        self.DB_AGENT_SCRIPT = "db_agent.py"
        self.INFO_AGENT_SCRIPT = "info_agent.py"
        self.WEB_AGENT_SCRIPT = "web_agent.py"
        self.OUTPUT_DIR = "data_output"
        self.CHARTS_DIR = os.path.join(self.OUTPUT_DIR, "bi_charts")
        self.REPORTS_DIR = "reports"
        self.LOGS_DIR = "logs"
        self.MAX_STEPS = 12  # 增加步数以支持多步骤协作
        self.TIMEOUT = 60

        for d in [self.CHARTS_DIR, self.REPORTS_DIR, self.LOGS_DIR]:
            os.makedirs(d, exist_ok=True)


# ============================================================
# 企业级中文字体加载
# ============================================================
def setup_chinese_font():
    """确保 Matplotlib 显示中文"""
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'PingFang SC', 'WenQuanYi Micro Hei', 'Arial Unicode MS']
    plt.rcParams['axes.unicode_minus'] = False
    print("✅ 使用系统字体: Microsoft YaHei")


# ============================================================
# LLM 调用
# ============================================================
def call_llm(messages: List[Dict], config: Config) -> str:
    url = f"{config.SILICON_BASE_URL}/chat/completions"
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
    for attempt in range(2):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=config.TIMEOUT)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            time.sleep(2)
        except:
            time.sleep(2)
    return ""


# ============================================================
# 子 Agent 调用器（UTF-8 强制）
# ============================================================
def call_sub_agent(script: str, query: str, config: Config) -> Dict:
    try:
        result = subprocess.run(
            [sys.executable, script, query],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=120
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            try:
                match = re.search(r"\{.*\}", output, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    if isinstance(data, dict):
                        if data.get("_type") == "error":
                            return data
                        if any(key in data for key in ["tables", "sample", "rows", "headers"]):
                            return data
                return {"_type": "text", "content": output}
            except Exception as e:
                return {"_type": "text", "content": output, "parse_error": str(e)}
        else:
            return {"_type": "error", "error": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"_type": "error", "error": "子 Agent 执行超时"}
    except Exception as e:
        return {"_type": "error", "error": str(e)}


# ============================================================
# 辅助：提取关键词（用于图表文件名）
# ============================================================
def extract_keywords(question: str, max_len: int = 4) -> str:
    cleaned = re.sub(r'[^\w\u4e00-\u9fa5]', ' ', question)
    words = [w for w in cleaned.split() if w]
    key = '_'.join(words[:max_len])
    if len(key) > 30:
        key = key[:30]
    if not key:
        key = hashlib.md5(question.encode()).hexdigest()[:8]
    return key


# ============================================================
# 进度打印
# ============================================================
def print_progress(step: int, msg: str, status: str = "info"):
    icons = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌", "process": "⏳"}
    icon = icons.get(status, "ℹ️")
    print(f"{icon} {msg}")


# ============================================================
# System Prompt（协作模式）
# ============================================================
def build_system_prompt() -> str:
    return """
你是一个智能任务调度 Agent，负责分析用户问题并调度子 Agent 完成任务。

## 可用工具
1. `call_db(query)`: **仅限**查询内部销售数据库（产品表、销售记录表）。适用于"销售额"、"产品销量"、"地区业绩"、"TOP产品"等问题。
2. `call_info(query)`: 调用信息检索子 Agent（Tavily 搜索引擎），获取实时新闻、百科、最新资讯。**用于发现数据源和获取背景信息。**
3. `call_web(query)`: 调用网页抓取子 Agent，提取指定网页的结构化表格数据。**用于从具体页面提取结构化数据。**

## 🔥 多步骤协作模式（核心）
对于需要"结构化数据 + 图表"的问题，你应该采用以下模式：

### 模式1：搜索 → 抓取 → 图表（推荐）
1. **第一步**：调用 `call_info` 搜索问题，获取相关网页的 URL 列表。
2. **观察结果**：查看 `call_info` 返回的 `results` 中是否有包含表格数据的网页链接（如教育考试院、统计局、东方财富等）。
3. **第二步**：如果找到合适的 URL，调用 `call_web` 抓取该 URL 的表格数据。
4. **第三步**：用 `final` 输出结果，图表会自动生成。

### 模式2：直接抓取（仅当你知道确切的 URL）
- 如果用户提供了 URL，或你确定某个网站（如 `https://www.cngold.org/quote/`）有数据，可以直接调用 `call_web`。

### 模式3：仅搜索（不需要图表时）
- 如果问题只需要文字答案（如"什么是 AI"），直接调用 `call_info` 后 `final`。

## 具体示例
### 示例1：教育类问题
用户问："贵州、四川、云南、重庆近十年的一本录取分数"

**正确流程**：
1. Thought: 需要找到这些省份的录取分数数据，先搜索看看有没有官方或教育网站公布了历年数据。
2. Action: `call_info` 搜索"贵州 四川 云南 重庆 近十年 一本录取分数线"
3. Observation: 返回结果中包含链接 `https://gaokao.cn/province/guizhou/` 等
4. Thought: 这些链接可能包含表格，抓取它们提取数据。
5. Action: `call_web` 抓取 `https://gaokao.cn/province/guizhou/`
6. Observation: 返回表格数据
7. Action: `final` 输出答案，图表自动生成

**错误流程**（禁止）：
- ❌ 直接调用 `call_web` 抓取金投网（完全不相关）。
- ❌ 只调用 `call_info` 而不尝试后续抓取（如果用户需要图表）。

## 决策铁律
1. 如果问题需要**数据+图表**，**优先尝试"搜索→抓取"模式**。
2. 如果 `call_info` 返回的结果中没有合适的 URL，再考虑仅用文字回答。
3. 如果问题涉及金融行情（价格、走势），先尝试抓取金投网，失败再搜索。

## 输出格式
- 调用工具：{"action": "call_db/info/web", "query": "..."}
- 任务完成：{"action": "final", "answer": "..."}
"""


# ============================================================
# 智能数据提取（支持表格和列表）
# ============================================================
def extract_df_from_tables(tables: List[Dict], lists: List[Dict] = None) -> Tuple[Optional[pd.DataFrame], str]:
    """
    从 web_agent 返回的 tables 或 lists 中提取数据框
    优先从表格提取，若表格数据无效（如全是 "--"），则尝试从列表解析
    """
    # ---- 1. 从表格提取 ----
    for idx, table in enumerate(tables):
        headers = table.get("headers", [])
        rows = table.get("rows", [])
        if not headers or not rows:
            continue

        print_progress(0, f"🔍 检查表格 {idx+1}: 列名={headers}, 行数={len(rows)}", "info")

        try:
            df = pd.DataFrame(rows)
        except Exception as e:
            print_progress(0, f"⚠️ 表格 {idx+1} 构建失败: {e}", "warning")
            continue

        # 识别名称列和数值列
        name_col = None
        value_col = None
        
        name_keywords = ['品种', '名称', '商品', '产品', '项目', '日期', '省份']
        for col in df.columns:
            col_lower = col.lower()
            if any(kw in col_lower for kw in name_keywords):
                name_col = col
                break
        if name_col is None:
            name_col = df.columns[0]

        value_keywords = ['最新价', '价格', '收盘', '开盘', '最高', '最低', '涨跌', '销售额', '销量', '分数', '录取']
        for col in df.columns:
            if col == name_col:
                continue
            col_lower = col.lower()
            if any(kw in col_lower for kw in value_keywords):
                try:
                    test_series = pd.to_numeric(df[col], errors='coerce')
                    if test_series.notna().sum() > 0:
                        value_col = col
                        break
                except:
                    pass
        
        if value_col is None:
            for col in df.columns:
                if col == name_col:
                    continue
                try:
                    test_series = pd.to_numeric(df[col], errors='coerce')
                    if test_series.notna().sum() > 0:
                        value_col = col
                        break
                except:
                    pass

        if value_col is None:
            print_progress(0, f"⚠️ 表格 {idx+1} 没有有效数值列", "warning")
            continue

        df_extracted = df[[name_col, value_col]].copy()
        df_extracted = df_extracted.rename(columns={name_col: '名称', value_col: '数值'})
        df_extracted['数值'] = pd.to_numeric(df_extracted['数值'], errors='coerce')
        df_extracted = df_extracted.dropna(subset=['名称', '数值'])
        df_extracted = df_extracted[df_extracted['名称'].astype(str).str.len() > 0]
        df_extracted = df_extracted[df_extracted['数值'] > 0]

        if df_extracted.empty:
            print_progress(0, f"⚠️ 表格 {idx+1} 过滤后为空（可能数据是 '--'）", "warning")
            continue

        print_progress(0, f"✅ 使用表格 {idx+1}: 名称列='{name_col}', 数值列='{value_col}', 有效行数={len(df_extracted)}", "success")
        return df_extracted, f"表格 {idx+1}"

    # ---- 2. 表格无效，尝试从列表提取 ----
    if lists:
        for lst in lists:
            items = lst.get("items", [])
            if not items:
                continue
            
            # 检查是否包含价格/分数类数据（包含数字且含关键词）
            has_data = any(
                any(kw in item for kw in ['黄金', '白银', '铂金', '伦敦金', '伦敦银', 'T+D', '录取', '分数', '一本', '本科'])
                for item in items
            )
            if not has_data:
                continue
            
            print_progress(0, f"🔍 从列表 {lst.get('list_index', '?')} 提取数据", "info")
            
            data_rows = []
            for item in items:
                # 匹配格式: 名称 + 数字（可能粘连）
                # 例如: "黄金T+D252.9810.22" -> 名称: 黄金T+D, 价格: 252.98
                # 或 "贵州大学 580" -> 名称: 贵州大学, 分数: 580
                match = re.match(r'([^\d]+)([\d.]+)', item)
                if match:
                    name = match.group(1).strip()
                    price_str = match.group(2)
                    price_match = re.search(r'([\d.]+)', price_str)
                    if price_match:
                        try:
                            price = float(price_match.group(1))
                            if price > 0 and price < 100000:  # 合理范围
                                data_rows.append({'名称': name, '数值': price})
                        except:
                            pass
                
                # 如果上面的匹配失败，尝试更宽松的匹配（有空格分隔）
                if len(data_rows) == 0 or len(data_rows) < len([i for i in items if any(kw in i for kw in ['黄金', '白银', '录取'])]) // 2:
                    match = re.match(r'([^\d]+)\s*([\d.]+)', item)
                    if match:
                        name = match.group(1).strip()
                        try:
                            price = float(match.group(2))
                            if price > 0 and price < 100000:
                                data_rows.append({'名称': name, '数值': price})
                        except:
                            pass
            
            if data_rows:
                df = pd.DataFrame(data_rows)
                print_progress(0, f"✅ 从列表提取数据，有效行数={len(df)}", "success")
                return df, f"列表 {lst.get('list_index', '?')}"

    return None, ""


# ============================================================
# 智能图表生成
# ============================================================
def generate_bi_charts(data: Dict, prefix: str, config: Config, user_question: str = "") -> List[str]:
    # ===== 强制中文显示 =====
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'PingFang SC', 'WenQuanYi Micro Hei', 'Arial Unicode MS']
    plt.rcParams['axes.unicode_minus'] = False
    # =========================

    chart_paths = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if user_question:
        key = extract_keywords(user_question, max_len=4)
    else:
        key = prefix

    if data.get("_type") == "error":
        print_progress(0, f"⚠️ 跳过图表生成：数据错误", "warning")
        return []

    # ---- 1. 从 tables 提取 ----
    df = None
    table_desc = ""
    tables = data.get("tables", [])
    lists = data.get("lists", [])
    if tables or lists:
        df, table_desc = extract_df_from_tables(tables, lists)

    # ---- 2. 如果 tables 无效，尝试 sample（db_agent 返回的数据） ----
    if df is None or df.empty:
        sample = data.get("sample", [])
        if sample:
            try:
                df = pd.DataFrame(sample)
                # 智能识别名称列和数值列
                name_col = None
                value_col = None
                
                name_keywords = ['名称', '产品', '商品', '项目', '日期', '省份', '品种']
                for col in df.columns:
                    col_lower = col.lower()
                    if any(kw in col_lower for kw in name_keywords):
                        name_col = col
                        break
                if name_col is None:
                    name_col = df.columns[0]
                
                value_keywords = ['销售额', '销量', '数量', '价格', '总额', '收入', 'revenue', 'sales', '分数']
                for col in df.columns:
                    if col == name_col:
                        continue
                    col_lower = col.lower()
                    if any(kw in col_lower for kw in value_keywords):
                        value_col = col
                        break
                if value_col is None:
                    for col in df.columns:
                        if col == name_col:
                            continue
                        try:
                            test_series = pd.to_numeric(df[col], errors='coerce')
                            if test_series.notna().sum() > 0:
                                value_col = col
                                break
                        except:
                            pass
                
                if value_col is not None:
                    df = df[[name_col, value_col]].dropna()
                    df = df.rename(columns={name_col: '名称', value_col: '数值'})
                    df['数值'] = pd.to_numeric(df['数值'], errors='coerce')
                    df = df.dropna(subset=['名称', '数值'])
                    df = df[df['名称'].astype(str).str.len() > 0]
                    df = df[df['数值'] > 0]
                    print_progress(0, f"✅ 从 sample 提取数据，有效行数={len(df)}", "success")
                else:
                    df = None
            except Exception as e:
                print_progress(0, f"⚠️ sample 解析失败: {e}", "warning")
                df = None

    # ---- 3. 如果仍然为空，尝试文本解析 ----
    if df is None or df.empty:
        text_content = data.get("content", "") or data.get("answer", "") or data.get("text", "") or str(data)
        if text_content and len(text_content) > 10:
            print_progress(0, "🔍 尝试从文本解析...", "warning")
            lines = text_content.split('\n')
            data_rows = []
            for line in lines:
                match = re.search(r'([\u4e00-\u9fa5a-zA-Z0-9\s]+)[:：]?\s*([\d.]+)', line)
                if match:
                    label = match.group(1).strip()
                    value_str = match.group(2).strip()
                    if value_str and value_str != '.':
                        try:
                            value = float(value_str)
                            data_rows.append({"名称": label, "数值": value})
                        except:
                            pass
            if data_rows:
                df = pd.DataFrame(data_rows)
                print_progress(0, f"✅ 从文本解析出 {len(df)} 行数据", "success")

    # ---- 数据校验 ----
    if df is None or df.empty:
        print_progress(0, "⚠️ 无有效数据，跳过图表生成", "warning")
        return []

    # 确保有名称列和数值列
    if '名称' not in df.columns or '数值' not in df.columns:
        print_progress(0, f"⚠️ 数据列名不符，当前列: {list(df.columns)}", "warning")
        return []

    # 过滤无效行
    df = df[df['名称'].astype(str).str.len() > 0]
    df = df[df['数值'].notna()]
    df = df[df['数值'] > 0]

    if df.empty:
        print_progress(0, "⚠️ 过滤后无有效数据", "warning")
        return []

    print_progress(0, f"📊 准备生成图表，数据行数={len(df)}", "success")

    # ---- 判断数据量，选择图表类型 ----
    plt.style.use('dark_background')
    colors = ['#00d4ff', '#ff6b6b', '#ffd93d', '#6bcb77', '#4d96ff']

    x_col = '名称'
    y_col = '数值'

    # ---- 柱状图（核心图表） ----
    try:
        fig, ax1 = plt.subplots(figsize=(10, 6))
        fig.patch.set_facecolor('#0d1117')
        ax1.set_facecolor('#0d1117')

        x_pos = np.arange(len(df))
        bars = ax1.bar(x_pos, df[y_col], color=colors[0], alpha=0.8)

        ax1.set_xlabel(x_col, color='white')
        ax1.set_ylabel(y_col, color='white')
        ax1.tick_params(colors='white')
        ax1.set_xticks(x_pos)
        ax1.set_xticklabels(df[x_col], rotation=45, ha='right')

        # 添加数值标签
        for i, v in enumerate(df[y_col]):
            ax1.text(i, v + 0.05 * max(df[y_col]), f'{v:.2f}', color='white', ha='center', fontsize=9)

        # 标题
        if len(df) == 1:
            title = f"{prefix} 指标卡 (唯一值)"
        else:
            title = f"{prefix} 柱状图"
        ax1.set_title(title, color='white', fontsize=16, pad=20)

        ax1.grid(alpha=0.2)
        for spine in ax1.spines.values():
            spine.set_color('#333')

        plt.tight_layout()
        chart_file = os.path.join(config.CHARTS_DIR, f"{key}_{timestamp}_bar.png")
        plt.savefig(chart_file, dpi=150, bbox_inches='tight', facecolor='#0d1117')
        plt.close()
        chart_paths.append(chart_file)
        print_progress(0, f"📊 生成柱状图: {os.path.basename(chart_file)}", "success")
    except Exception as e:
        print_progress(0, f"⚠️ 生成柱状图失败: {e}", "warning")

    # ---- 环形图（3条以上数据时生成） ----
    if len(df) >= 3:
        try:
            fig2, ax2 = plt.subplots(figsize=(6, 6))
            fig2.patch.set_facecolor('#0d1117')
            ax2.set_facecolor('#0d1117')

            labels = df[x_col].astype(str).tolist()
            values = df[y_col].tolist()
            values = [abs(v) for v in values]

            if sum(values) > 0 and len(set(values)) > 1:
                ax2.pie(
                    values,
                    labels=labels,
                    autopct='%1.0f%%',
                    colors=colors[:len(labels)],
                    wedgeprops={'width': 0.5, 'edgecolor': '#0d1117'},
                    textprops={'color': 'white', 'fontsize': 10}
                )
                ax2.set_title(f"{prefix} 构成", color='white', fontsize=14, pad=20)
                plt.tight_layout()
                chart_file2 = os.path.join(config.CHARTS_DIR, f"{key}_{timestamp}_pie.png")
                plt.savefig(chart_file2, dpi=150, bbox_inches='tight', facecolor='#0d1117')
                plt.close()
                chart_paths.append(chart_file2)
                print_progress(0, f"📊 生成环形图: {os.path.basename(chart_file2)}", "success")
        except Exception as e:
            print_progress(0, f"⚠️ 生成环形图失败: {e}", "warning")

    return chart_paths


# ============================================================
# 报告生成
# ============================================================
def generate_report(title: str, content: str, charts: List[str], format: str = "md") -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if format == "html":
        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>{title}</title>
<style>
body {{ background: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 40px; }}
h1 {{ color: #00d4ff; }}
h2 {{ color: #ffd93d; margin-top: 30px; }}
img {{ max-width: 100%; margin: 20px 0; border-radius: 8px; border: 1px solid #30363d; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p><strong>生成时间：</strong>{timestamp}</p>
<div>{content}</div>
"""
        if charts:
            html += "<h2>📊 可视化图表</h2>"
            for chart in charts:
                rel_path = os.path.relpath(chart, start=os.path.dirname(os.path.abspath(__file__)))
                html += f'<img src="{rel_path}" style="max-width:100%;margin:10px 0;" />\n'
        html += "</body></html>"
        return html
    else:
        md = f"# {title}\n\n**生成时间：** {timestamp}\n\n"
        md += content + "\n\n"
        if charts:
            md += "## 📊 可视化图表\n\n"
            for chart in charts:
                rel_path = os.path.relpath(chart, start=os.path.dirname(os.path.abspath(__file__)))
                md += f"![图表]({rel_path})\n\n"
        return md


# ============================================================
# ReAct 循环主流程
# ============================================================
def run_agent(user_input: str, config: Config, report_format: str = "md") -> str:
    print_progress(0, f"用户问题: {user_input}", "info")
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": f"用户问题：{user_input}"}
    ]
    step = 0
    final_answer = None
    all_data = {}
    charts = []

    while step < config.MAX_STEPS:
        step += 1
        print_progress(step, f"第 {step} 步思考...", "process")
        response = call_llm(messages, config)
        if not response:
            print_progress(step, "LLM 无响应，尝试降级...", "warning")
            break
        try:
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                decision = json.loads(json_match.group())
            else:
                decision = {"action": "final", "answer": response}
        except:
            decision = {"action": "final", "answer": response}

        action = decision.get("action")
        query = decision.get("query", user_input)

        if action == "final":
            final_answer = decision.get("answer", "无法生成答案")
            print_progress(step, "任务完成，正在生成报告...", "success")
            break
        elif action == "call_db":
            print_progress(step, f"调用数据分析 Agent: {query}", "process")
            result = call_sub_agent(config.DB_AGENT_SCRIPT, query, config)
            all_data["db"] = result
            if result.get("_type") == "error":
                print_progress(step, f"❌ 数据查询失败: {result.get('error', '未知错误')}", "error")
                observation = json.dumps(result, ensure_ascii=False, indent=2)[:500]
            else:
                charts.extend(generate_bi_charts(result, "db", config, user_question=user_input))
                observation = json.dumps(result, ensure_ascii=False, indent=2)[:500]
                print_progress(step, "✅ 数据查询完成", "success")
        elif action == "call_info":
            print_progress(step, f"调用信息检索 Agent: {query}", "process")
            result = call_sub_agent(config.INFO_AGENT_SCRIPT, query, config)
            all_data["info"] = result
            observation = json.dumps(result, ensure_ascii=False, indent=2)[:500]
            print_progress(step, "✅ 信息检索完成", "success")
        elif action == "call_web":
            print_progress(step, f"调用网页抓取 Agent: {query}", "process")
            result = call_sub_agent(config.WEB_AGENT_SCRIPT, query, config)
            all_data["web"] = result
            if result.get("_type") != "error":
                charts.extend(generate_bi_charts(result, "web", config, user_question=user_input))
            observation = json.dumps(result, ensure_ascii=False, indent=2)[:500]
            print_progress(step, "✅ 网页抓取完成", "success")
        else:
            observation = f"未知操作: {action}"
            print_progress(step, observation, "warning")

        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    if final_answer is None:
        final_answer = "达到最大步数，未能完成分析"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_content = final_answer
    if all_data:
        report_content += "\n\n## 📦 详细数据\n\n"
        for k, v in all_data.items():
            report_content += f"### {k.upper()}\n"
            report_content += json.dumps(v, ensure_ascii=False, indent=2) + "\n\n"

    report_title = f"AI 分析报告 - {timestamp}"
    report_md = generate_report(report_title, report_content, charts, format=report_format)
    ext = "html" if report_format == "html" else "md"
    report_file = os.path.join(config.REPORTS_DIR, f"report_{timestamp}.{ext}")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_md)
    print_progress(0, f"📁 报告已保存: {report_file}", "success")
    if charts:
        print_progress(0, f"📊 已生成 {len(charts)} 张图表，保存在 {config.CHARTS_DIR}", "success")
    else:
        print_progress(0, "⚠️ 未生成图表", "warning")
    return final_answer


# ============================================================
# 错误处理
# ============================================================
def handle_fatal_error(e: Exception, config: Config):
    error_msg = f"任务执行失败: {str(e)}"
    print_progress(0, error_msg, "error")
    print("\n" + "=" * 60)
    print("❌ 任务无法完成，如果你认为这是一个 bug，请前往 GitHub 提交 Issue：")
    print("   https://github.com/CerryYcr/agent_project/issues")
    print("\n请附上：")
    print("1. 你输入的问题")
    print("2. 完整的错误信息（如上所示）")
    print("3. 你的环境信息（Python 版本、操作系统）")
    print("=" * 60)

    error_report = {
        "timestamp": datetime.now().isoformat(),
        "error": str(e),
        "python_version": sys.version,
        "platform": sys.platform,
    }
    error_file = os.path.join(config.LOGS_DIR, f"error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(error_file, "w", encoding="utf-8") as f:
        json.dump(error_report, f, indent=2)
    print(f"\n📄 错误详情已保存: {error_file}")


# ============================================================
# 入口
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI Agent 主程序（ReAct + BI 可视化）")
    parser.add_argument("--format", choices=["md", "html"], default="md",
                        help="报告格式：md（Markdown）或 html（HTML）")
    parser.add_argument("--query", type=str, help="直接传入问题（非交互模式）")
    args = parser.parse_args()

    config = Config()

    # 强制中文字体（一次性）
    setup_chinese_font()

    print("=" * 60)
    print("🤖 智能 Agent 系统 (ReAct + BI 可视化)")
    print(f"📦 模型: {config.MODEL_NAME}")
    print(f"📂 子 Agent: db | info | web")
    print(f"📄 报告格式: {args.format}")
    print(f"🔁 最大步数: {config.MAX_STEPS}")
    print("=" * 60)

    if args.query:
        try:
            run_agent(args.query, config, report_format=args.format)
        except Exception as e:
            handle_fatal_error(e, config)
        return

    while True:
        user_input = input("\n❓ 请输入你的问题 (输入 exit 退出): ").strip()
        if user_input.lower() in ["exit", "quit", "q"]:
            break
        if not user_input:
            continue

        try:
            run_agent(user_input, config, report_format=args.format)
        except Exception as e:
            handle_fatal_error(e, config)


if __name__ == "__main__":
    main()