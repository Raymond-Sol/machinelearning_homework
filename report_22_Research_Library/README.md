# Report22 - 个人科研资料智能管理库

科研人员每天都在被海量论文、代码仓库淹没：查文献靠手动搜索、下载的 PDF 散落在下载目录里、看过就忘。传统文献管理工具（Zotero 等）解决了"存"，但解决不了"自动跟踪最新资料"和"让 AI Agent 直接使用你的资料库"这两个新需求。

本报告源自一个真实的工程项目：构建一个**个人论文/图书/代码资料管理库**——给定研究主题，每天定时抓取最新论文与代码资料自动入库；PDF 自动解析成 Markdown 支持全文检索；对外提供 REST API 和 MCP 两种访问方式，让 kimi code 等 AI Agent 能像查自己的记忆一样检索你的资料库。（选择本报告的同学只需完成这一个报告即可，无需再选做其他3个报告。）

本报告同样要求使用 kimi-code 等 AI Agent 完成开发，训练两个能力：复杂系统的架构拆解与 prompt 驱动开发、真实工程问题（限速、风控、去重、失败降级）的处理。

## 任务总体描述

构建一个资料管理服务，核心功能：

1. **主题订阅与定时收集**：配置研究主题（检索式 + 数据源），服务每天定时抓取最新论文/代码资料，自动去重入库，有 OA 链接时下载 PDF
2. **资料解析**：PDF → Markdown 解析（公式、图表保真），与 PDF 一起按规范目录存放
3. **检索**：元数据 + 全文检索，支持关键词（BM25）与语义（Embedding 向量）两种模式
4. **双协议接入**：FastAPI 提供 REST API；FastMCP 提供 MCP 工具，供 AI Agent 调用
5. **手动拷贝入库（加分）**：监听指定目录，用户把 PDF 拷进去即自动解析入库
6. **AI Agent 集成（加分）**：kimi code 通过 MCP 检索资料库回答"我库里有没有关于 X 的资料"

## 参考架构

分层架构（接入层共享同一个 service 层，业务逻辑零重复）：

```
┌────────────────────────────────────────────────────┐
│ 接入层：FastAPI REST (/api/v1/*)   FastMCP (stdio)  │
└──────────────┬─────────────────────┬───────────────┘
               ▼                     ▼
┌────────────────────────────────────────────────────┐
│ Service 层：TopicService（订阅/调度）                │
│            SearchService（检索/去重/入库编排）        │
│            IngestService（解析入库）                 │
│            QueryService（关键词/语义检索）            │
└──────┬──────────────┬───────────────┬──────────────┘
       ▼              ▼               ▼
  收集器          解析器            存储
  arXiv / S2 /    MinerU API 或     SQLite（元数据）
  OpenAlex /      pymupdf4llm       BM25 全文索引
  HF Daily /      （可插拔）         文件系统（PDF+MD）
  GitHub / Gitee
       ▲
       │ APScheduler 每日定时触发
```

建议技术选型：Python ≥3.10，FastAPI（Web 框架，自带 OpenAPI 文档）、FastMCP（MCP server）、APScheduler（进程内定时调度，个人规模不需要 celery/redis）、watchdog（目录监听）、SQLite + SQLModel（单用户元数据库，与 FastAPI 共享 pydantic schema）、纯 Python BM25 零依赖起步。

## 数据模型与目录布局参考

核心表（三张即可起步）：

* `items`：论文/图书/代码仓库统一为 item，用 `kind` 区分（paper/book/repo）；字段包括 title、authors、year、venue、doi、arxiv_id、abstract、source、topics、pdf_path、md_path、status（new → pdf_saved → parsed / failed / meta_only）等
* `topics`：主题订阅，含检索式 `query_expr`、数据源列表 `sources`、调度配置、`last_run_at`
* `fetch_log`：每次抓取的统计（found/ingested/failed）与错误明细，供审计

物理目录布局（**一篇论文一个目录**，而不是裸 PDF 平铺，给 md/images 留位置）：

```
library_root/
├── config.yaml            # 主题订阅、路径等配置
├── incoming/              # 手动拷贝 PDF 的观察目录（与正式库分离）
├── papers/{YYYY}/{first-author}{year}_{short-title}/
│     ├── paper.pdf
│     ├── paper.md
│     └── images/
├── repos/                 # 代码仓库只存 README 快照+元信息，不 git clone
├── library.db             # SQLite
└── logs/
```

## 关键工程问题（真实踩坑经验，设计时必须考虑）

* **去重**：三级去重键——`doi` → `arxiv_id` → `title 归一化(小写/去标点/压空白)+首作者`；冲突时保留信息更完整的一条并合并 topic 标签
* **API 限速与礼貌爬取**：arXiv 官方 API 要求请求间隔 ≥3 秒；Semantic Scholar 免费额度 100 请求/5 分钟，429 时必须读 `Retry-After` 退避。多主题轮询下要考虑单次运行的耗时与断点续传（按上次成功时间增量抓取）
* **数据源风控/降级**：以 Gitee 为例——实测其 `search/repositories` 接口对程序化客户端返回空数组（风控），此时应降级为"watchlist 模式"：用户显式指定仓库列表，每日只查各仓库的 `pushed_at` 判断更新。**遇到接口不可用时的降级策略设计**是本报告的考察点之一
* **文件稳定检测**：目录监听时文件可能拷贝到一半，需等尺寸稳定（如 2 秒不变）后再处理；处理完成即移出观察目录——"移动即事务边界"，避免半解析文件混入正库
* **PDF 校验**：入库前检查 `%PDF` magic bytes，防止把损坏/伪装文件当 PDF
* **解析服务不可用**：解析管线要可重跑（扫描 `status=pdf_saved` 的条目重新投递），服务宕机恢复后不丢数据
* **付费论文边界**：无 OA 链接的论文只存元数据，明确标注 `meta_only`，不要尝试绕过付费墙

## 要求

1. 使用 kimi-code 等 AI 编程 Agent（Claude Code、Cursor 等也可以）完成开发，报告中记录任务拆解、关键 prompt 与 Agent 响应、你发现并修正的 Agent 生成代码的问题
2. **必做——收集与元数据服务**：项目骨架 + SQLite 三张表 + arXiv 收集器（官方 Atom API，`sortBy=submittedDate` 抓最新，遵守 ≥3s 限速）+ BM25 关键词检索 + REST API（`GET /api/v1/items?query=`、`POST /api/v1/topics`、`GET /api/v1/topics/{id}/runs`）
3. **必做——解析管线**：PDF → Markdown（MinerU 类 OCR 服务或 pymupdf4llm 本地方案，至少实现一个、接口设计成可插拔），解析结果与 PDF 按上面的目录布局存放，`GET /api/v1/items/{id}` 能返回结构化 Markdown
4. **必做——检索对比实验（机器学习核心）**：构建一个含 10~20 个查询的测试集（每个查询标注相关条目），对比三种方案的检索质量：BM25 关键词检索、Embedding 向量语义检索（可用任意 embedding API）、向量检索 + Rerank 重排。用 Precision@k / MRR 等指标量化评估，分析各自适合的场景（中英文、缩写、语义改述等）
5. **加分——目录监听自动入库**：watchdog 监听 `incoming/`，含稳定检测、magic bytes 校验、DOI 提取（PDF 元数据 + 全文正则匹配 doi.org 链接）反查元数据补全题录、自动归档入库
6. **加分——MCP server 与 Agent 集成**：FastMCP 注册工具 `search_library(query, mode, top_k)`、`get_item(id)`、`list_topics()`、`run_topic_now(id)`，配置到 kimi code 中实际验证 Agent 能检索你的资料库并回答问题（附对话截图）
7. **加分——多源扩展**：HF Daily Papers（榜单 + arXiv API 补全元数据）、GitHub 仓库 watchlist（只存 README 快照）、Gitee watchlist（体验一下风控问题并实现降级策略）
8. **验证标准**（报告中需逐条自证）：
   - 建一个主题 → 触发一次抓取 → curl 能查到新入库论文
   - 任一入库论文能取到结构化 Markdown（公式/图片路径正确）
   - 拷贝一个带 DOI 的 PDF 进 `incoming/`，10 分钟内库内可查到且已完成解析（若做加分项5）
   - kimi code 能通过 MCP 检索库（若做加分项6）
9. 按照`report_template.md`撰写自己的报告。

## 参考资料

* [arXiv API 官方文档](https://info.arxiv.org/help/api/)（Atom XML，限速约定 ≥3s）
* [FastAPI 官方文档](https://fastapi.tiangolo.com/) / [FastMCP 文档](https://gofastmcp.com/)
* [Semantic Scholar API](https://api.semanticscholar.org/api-docs/) / [OpenAlex API](https://docs.openalex.org/)
* [pymupdf4llm](https://pypi.org/project/pymupdf4llm/)（PDF→Markdown 本地方案）
* [APScheduler](https://apscheduler.readthedocs.io/) / [watchdog](https://python-watchdog.readthedocs.io/)
* BM25: Robertson & Zaragoza, *The Probabilistic Relevance Framework: BM25 and Beyond*
* [MCP 协议](https://modelcontextprotocol.io/)（Model Context Protocol，AI Agent 工具接入标准）
