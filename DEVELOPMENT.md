# 开发说明（DEVELOPMENT）

> 这不是产品文档，是给三位同学协作开发用的预说明。读完再动代码可以避免互相踩坑。

---

## 1. 目录结构与分工

```
openclaw-analysis-system/
├── config/config.yaml           # 全局 + 各 Agent 配置段
├── data/
│   ├── raw/                     # 原始测试数据（Excel/PDF/HTML 已就位）
│   └── processed/               # 报告输出（默认被 .gitignore）
├── .env.example                 # 复制为 .env 后填密钥
├── requirements.txt
└── src/
    ├── main.py                  # 总入口（不要轻易改）
    ├── pipeline.py              # 全链路编排（不要轻易改）
    ├── utils/config_loader.py   # 配置加载工具（直接用即可）
    └── agents/
        ├── base.py              # BaseAgent 抽象基类
        ├── collector/           # 同学 A 的领地（多源数据采集）
        ├── analyst/             # 同学 B 的领地（RAG + 商业分析）
        ├── generator/           # 同学 C 的领地（报告生成与回填）
        └── reviewer/            # 同学 D 的领地（合规审计）
```

每位 Agent 负责人只在 **自己的 Agent 子目录** 下增删文件，不要跨目录改代码。

---

## 2. Agent 开发契约

### 2.1 必须遵守的两件事

1. 你的 Agent 类必须继承 [BaseAgent](src/agents/base.py)，并设置类变量 `name`（用于日志前缀和读取 `config.yaml` 中的同名段）。
2. 必须实现 `run(...)` 方法。`run()` 的签名要与 [pipeline.py](src/pipeline.py) 里的调用方式对齐（见下表）。

### 2.2 各 Agent 接口约定

[pipeline.py](src/pipeline.py) 已经按下面的方式串联，请按此实现 `run()`：

| Agent     | 入参（kwargs）                                 | 返回                                                                   |
| --------- | ---------------------------------------------- | ---------------------------------------------------------------------- |
| Collector | `excel_path`, `pdf_path`, `html_path`          | `tuple[list[dict], str]`：结构化文本列表 + 人类可读摘要                |
| Analyst   | `raw_texts`, `user_requirement`                | `str`：含 `## 章节` 的分析正文 Markdown                                |
| Generator | `analysis_text`                                | `str`：完整 Markdown 报告；并实现 `save(content, path) -> Path`        |
| Reviewer  | `report_content`, `raw_data_summary`           | `dict`：含 `is_passed: bool`、`review_opinions: str`、`revised_content: str` |

如果你认为接口需要改，**先在群里同步**，对应改 [pipeline.py](src/pipeline.py) 的那一行即可，不要默默改。

### 2.3 Agent 内部组织自由

`agent.py` 只是入口。如果实现复杂，欢迎在自己的 Agent 目录下任意拆分，例如：

```
src/agents/analyst/
├── __init__.py
├── agent.py           # 入口（必须保留 AnalystAgent 类）
├── rag.py             # 向量库构建/检索
├── prompts.py         # System / User Prompt 模版
└── skills/
    └── ...
```

只要 `from src.agents.analyst import AnalystAgent` 仍能拿到类即可。

---

## 3. 配置与密钥

- 全局共享配置在 [config/config.yaml](config/config.yaml)。每个 Agent 都有一个独立段（`collector:` / `analyst:` / `generator:` / `reviewer:`），需要新增字段就直接往自己的段里加，**不要动别人的段**。
- 在代码里读配置：直接用 `self.agent_config`（基类已加载好，对应自己 `name` 段）；要读全局 `openai` / `zhipu` 段则用 `self.config["openai"]`。
- 密钥**绝对不要**硬编码到 `config.yaml`。统一走 `${ENV_VAR}` 占位符 + 项目根目录 `.env`：
  1. 复制 `.env.example` 为 `.env`，填入真实 key
  2. `.env` 已被 `.gitignore` 忽略，不会进 Git
  3. `config.yaml` 里写 `api_key: "${OPENAI_API_KEY}"`，加载时自动替换
如果有同学想换别的模型（如 GPT-4），直接改 config.yaml 里的 base_url 和 model 就行，代码不用动。

---

## 4. 环境与依赖

- 每人本地自建虚拟环境，**不要把 `.venv/` 提交到 Git**：
  ```bash
  python -m venv .venv
  .venv\Scripts\activate          # Windows
  pip install -r requirements.txt
  ```
- 你的 Agent 需要新依赖时，加到 [requirements.txt](requirements.txt) 并在 PR 描述中注明。
- 锁版本时统一用 `>=`，避免冲突。

---

## 5. 独立调试建议

不要等其他 Agent 写完再联调。每位同学在自己的 Agent 目录下写一个 `_debug.py`（或 `tests/`）单独跑：

```python
# 例：src/agents/collector/_debug.py
from src.agents.collector import CollectorAgent

agent = CollectorAgent()
raw_texts, summary = agent.run(
    excel_path="data/raw/sales_data.xlsx",
    pdf_path="data/raw/industry_report.pdf",
    html_path="data/raw/competitor_site.html",
)
print(summary[:500])
```

`_debug.py` 仅供本地调试，可加到 `.gitignore`，也可以提交但不要被 pipeline 引用。

全链路联调（任意一个 Agent 没实现都会在那一步抛 `NotImplementedError`）：

```bash
python src/main.py
```

---

## 6. 协作纪律（重要）

- **不要跨 Agent 直接 import 内部实现**。比如 Reviewer 不要 `from src.agents.analyst.rag import xxx`，那是别人的内部细节。需要共享的工具放到 [src/utils/](src/utils/)。
- **不要改 [pipeline.py](src/pipeline.py) / [main.py](src/main.py) / [base.py](src/agents/base.py)**，除非接口约定变更并已在群里同步。
- **不要提交**：`.env`、`.venv/`、`__pycache__/`、`data/processed/*.md`（已在 `.gitignore` 里）、`data/processed/faiss_index/`。
- 各自开 feature 分支推 PR，命名建议：`feat/collector-xxx` / `feat/analyst-xxx`。
- 写代码时遇到 Agent 间接口模糊，**先群里对齐再写**，不要自己脑补。

---

## 7. 一个最简实现长什么样

参考 [src/agents/collector/agent.py](src/agents/collector/agent.py) 当前骨架，把 `raise NotImplementedError` 替换为真实逻辑即可。基类已经处理好 `self.config` / `self.agent_config` / `self.log()`，你的 `run()` 直接用就行。

```python
class CollectorAgent(BaseAgent):
    name = "collector"

    def run(self, *, excel_path, pdf_path, html_path):
        self.log("开始采集...")
        # 你的实现
        return raw_texts, raw_data_summary
```

就这样。有疑问群里问，不要自己默默改框架代码。
