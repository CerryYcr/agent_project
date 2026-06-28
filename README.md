```markdown
# 多智能体协作 BI 系统

基于 ReAct 循环的多智能体协作系统，支持数据分析（MySQL）、信息检索（Tavily）、网页抓取（动态/静态），自动生成 BI 可视化图表和完整报告。

---

## 功能特点

- **多智能体协作**：父 Agent（ReAct 调度器）统一调度三个子 Agent（数据分析、信息检索、网页抓取）
- **数据分析**：通过 `db_agent` 连接 MySQL，执行 SQL 查询并返回结构化数据
- **信息检索**：通过 `info_agent` 调用 Tavily 搜索引擎，获取实时新闻、百科、资讯，并生成智能摘要
- **网页抓取**：通过 `web_agent` 抓取静态/动态网页表格数据，支持反爬策略（UA 轮换、代理、Selenium 降级）
- **BI 可视化**：自动从数据中提取数值列，生成柱状图、环形图（支持 1 条数据也生成指标卡）
- **智能数据提取**：支持从表格、列表、纯文本中自动识别名称列和数值列（兼容金投网等金融数据源）
- **多步协作模式**：支持 `搜索 → 抓取 → 图表` 流水线（如教育类问题：先搜索找到 URL，再抓取表格生成图表）
- **可追溯命名**：图表文件名包含用户问题关键词，便于识别
- **多格式报告**：支持 Markdown 和 HTML 格式报告，自动嵌入图表
- **企业级代码质量**：UTF-8 编码强制、错误处理、重试机制、结构化日志

---

## 环境要求

- Python 3.11+（项目使用 3.11.9）
- Git 2.54.0.1
- VS Code
- Docker（用于 MySQL 容器）
- Windows / macOS / Linux

---

## 安装指南

### 1. 克隆项目

```bash
git clone https://github.com/CerryYcr/agent_project.git
cd agent_project
```

### 2. 创建并激活虚拟环境

```bash
python -m venv .venv

# Windows:
.venv\Scripts\activate

# macOS/Linux:
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

如果安装缓慢，可使用国内镜像源加速：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 4. 配置 API Key

在项目根目录创建 `.env` 文件，填入以下内容：

```env
SILICON_API_KEY=your_siliconflow_api_key
TAVILY_API_KEY=your_tavily_api_key
WEB_AGENT_USE_SELENIUM=true   # 可选，用于动态页面抓取
```

> ⚠️ **重要**：`.env` 文件已加入 `.gitignore`，请勿提交到仓库（开源里的.env的API为废弃API，只是为了更方便展示）。

### 5. 启动 MySQL 数据库（Docker）

```bash
docker run --name mysql-agent -e MYSQL_ROOT_PASSWORD=123456 -e MYSQL_DATABASE=sales_db -p 3306:3306 -d mysql:8.0
```

然后执行 `init.sql` 初始化表结构和测试数据：

```bash
docker exec -i mysql-agent mysql -uroot -p123456 sales_db < init.sql
```

### 6. 运行 Agent

交互模式（推荐）：

```bash
python agent_bi.py
```

直接传入问题（非交互模式）：

```bash
python agent_bi.py --query "最近30天销售额最高的产品是什么？"
```

---

## 项目结构

```
用户问题
    │
    ▼
┌────────────────────────────────────────────────────────────┐
│  父 Agent（agent_bi.py）— ReAct 循环                       │
│  Thought → Action → Observation → Thought → ...          │
│  MAX_STEPS = 12                                          │
└────────────────────────────────────────────────────────────┘
    │
    ├── call_db ──→ db_agent.py（数据分析子 Agent）
    │                  ├── 连接 MySQL（Docker）
    │                  ├── LLM 生成 SQL
    │                  ├── 执行查询 → 返回 JSON（含 tables + sample + stats）
    │                  └── 输出强制 UTF-8（解决 GBK 编码问题）
    │
    ├── call_info ──→ info_agent.py（信息检索子 Agent）
    │                  ├── Tavily Search API
    │                  ├── LLM 智能摘要（含来源标注）
    │                  ├── 搜索缓存（search_cache.json）
    │                  ├── ReAct 追问（自动补充搜索）
    │                  └── 异步搜索 + 并行查询
    │
    └── call_web ──→ web_agent.py（网页抓取子 Agent）
                       ├── 硬解析优先（BeautifulSoup 提取表格）
                       ├── LLM 辅助降级（硬解析无数据时）
                       ├── 反爬策略（UA 轮换 + Referer + 代理）
                       ├── Selenium 降级（动态页面）
                       ├── 智能缓存（cache.json，24 小时 TTL）
                       └── 输出 JSON（含 tables + lists）
    │
    ▼
┌────────────────────────────────────────────────────────────┐
│  generate_bi_charts()                                   │
│  ├── extract_df_from_tables() → 智能识别名称列 + 数值列   │
│  ├── 支持 1 行数据生成指标卡                              │
│  ├── 柱状图（核心图表）                                   │
│  └── 环形图（≥3 条数据时生成）                           │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌────────────────────────────────────────────────────────────┐
│  报告生成（Markdown / HTML）                              │
│  ├── 文字分析 + 图表嵌入                                  │
│  └── 保存至 reports/ 目录                                │
└────────────────────────────────────────────────────────────┘
```

---

## 技术栈

| 类别 | 工具/库 |
| :--- | :--- |
| 核心框架 | LangChain, ReAct 模式 |
| 大模型 API | 硅基流动（SiliconFlow）DeepSeek-V4-Flash |
| 数据库 | MySQL 8.0（Docker） |
| 信息检索 | Tavily Search API |
| 网页抓取 | Requests, BeautifulSoup4, Selenium（可选） |
| 数据处理 | Pandas, NumPy |
| 可视化 | Matplotlib |
| 开发语言 | Python 3.11+ |

---

## 注意事项

- 首次运行会自动检测中文字体（微软雅黑 / 苹方），图表中文显示正常。
- 如果网页抓取遇到反爬，可在 `.env` 中开启 `WEB_AGENT_USE_SELENIUM=true`（需安装 Chrome 浏览器和 `webdriver-manager`）。
- `data_output/`、`logs/`、`reports/` 目录会在首次运行时自动创建。
- 子 Agent 的 `stdout` 已强制 UTF-8 编码，避免 Windows 下 `gbk` 编码错误。
- 建议将 `MAX_STEPS`（最大推理步数）设为 12，以支持多步骤协作（搜索 → 抓取 → 图表）。

---

## 作者

**CerryYcr**  
GitHub: [@CerryYcr](https://github.com/CerryYcr)
```
