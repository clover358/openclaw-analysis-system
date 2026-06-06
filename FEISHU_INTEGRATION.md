# OpenClaw 飞书接入与近期改动说明

> 写给队友：本文说明近期已完成的工程改动，以及**如何把本程序接入飞书**。  
> 支持两种模式：**A. 跑完程序自动推群（Webhook）** / **B. 群里发指令才生成（事件订阅，见第二节 B）**。

---

## 一、近期完成了什么（改动摘要）

本次在 `main → pipeline → 飞书` 链路上主要做了这些事：

### 1. 业务场景切回「大模型 API 竞品分析」

- **Collector** 从 `data/*.json` 读取 API 调用排行数据
- 数据源包括：`Market Share.json`、`Top Model.json`、`Context Length.json`、`merged_rankings.json`
- 配置项：`config/config.yaml` → `collector.data_mode: ai_api`

### 2. 补全 Generator，打通全链路

- `GeneratorAgent` 已实现 `run()` / `save()`，可与 `pipeline.py` 正常串联
- 报告标题与章节已改为 API 竞品分析语境（见 `config.yaml` → `generator.report`）

### 3. 报告落盘 + PDF 导出

- Markdown 报告：`data/processed/ai_api_competitor_report_2025.md`
- 自动转 PDF：`data/outputs/ai_api_competitor_report_2025.pdf`
- 实现位置：`src/skills/markdown_pdf_skill.py`

### 4. 飞书 Webhook 推送（跑完即推，当前主流程）

Pipeline 最后一步会自动：

1. 发送**橙色摘要卡片**（执行摘要 + 审计状态）
2. 分片发送**完整报告正文**（纯文本，无需打开 PDF）
3. 本地仍保留 PDF 备份

实现位置：

- `src/skills/feishu_bot_skill.py` — Webhook 发送、关键词注入、正文分片
- `src/pipeline.py` — Step 7 调用飞书推送

### 5. 飞书发指令才生成（事件驱动，已实现）

- 入口：`python src/feishu_agent_server.py`
- 在群里 @ 机器人并发送 **「给我发送竞品报告」** → 后台跑 Pipeline → 机器人回复摘要卡片 + PDF + 正文
- 需要：飞书**企业自建应用** + 内网穿透（ngrok），详见 **第二节 B**

---

## 二、系统怎么跑（队友最快上手）

```text
python src/main.py
```

执行顺序：

```text
Collector（读 JSON）
    → Analyst（RAG + DeepSeek 分析）
    → Generator（润色 + 图表 + 组装报告）
    → Reviewer（合规审计）
    → 保存 Markdown
    → 转 PDF
    → 飞书 Webhook 推送（摘要卡片 + 正文文本）
```

### 模式 B：飞书发指令才生成（事件驱动）

```text
python src/feishu_agent_server.py   # 常驻监听，需 ngrok 暴露 8000 端口
```

群里 **@ 机器人** 发送 `给我发送竞品报告` → 服务收到事件 → 后台跑 Pipeline → 机器人主动回复。

---

## 二-B、模式 B 完整接入步骤（发指令才生成）

> 与 Webhook 自定义机器人是**两套独立机制**，不能混用同一个机器人。

### B.1 创建飞书企业自建应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app) → **创建企业自建应用**
2. 记录 **App ID**、**App Secret**（凭证与基础信息页）
3. **添加应用能力** → 开启 **机器人**
4. **权限管理** → 申请并开通以下权限（名称以控制台为准）：
   - 获取与发送单聊、群组消息
   - 读取用户发给机器人的单聊消息
   - 接收群聊中 @ 机器人 消息
   - 获取与上传图片或文件资源（用于发送 PDF）
5. **版本管理与发布** → 创建版本并**发布**（未发布则群里看不到机器人）

### B.2 配置事件订阅

1. 应用后台 → **事件订阅**
2. **Encrypt Key** 先留空（不要开启加密，当前代码未实现解密）
3. 复制 **Verification Token**，稍后写入 `.env`
4. **请求地址** 填：`https://你的公网域名/feishu/webhook`（见 B.3 ngrok）
5. 添加事件：`im.message.receive_v1`（接收消息）
6. 保存后飞书会向该地址发 URL 验证；**必须先启动本地服务 + ngrok** 才能保存成功

### B.3 本地内网穿透（ngrok 示例）

```powershell
# 终端 1：启动飞书 Agent 服务
cd 项目根目录
.venv\Scripts\activate
python src/feishu_agent_server.py

# 终端 2：暴露 8000 端口（需先安装 ngrok 并登录）
ngrok http 8000
```

复制 ngrok 给出的 **HTTPS** 地址，例如 `https://abc123.ngrok-free.app`，则事件订阅 URL 为：

```text
https://abc123.ngrok-free.app/feishu/webhook
```

健康检查：浏览器访问 `https://abc123.ngrok-free.app/health` 应返回 `{"status":"ok",...}`。

### B.4 配置 `.env`

在原有 DeepSeek / 智谱 密钥基础上，**追加**：

```env
# 企业自建应用（事件驱动必填）
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
FEISHU_VERIFICATION_TOKEN=你在事件订阅页复制的Token

# 事件驱动模式下建议关闭「跑完 main 自动推群」
PIPELINE_PUSH_TO_FEISHU=false
```

> Webhook 的 `FEISHU_WEBHOOK_URL` 在模式 B 中**不需要**；可保留不动。

### B.5 把机器人拉进群

1. 飞书开放平台 → 应用 → **机器人** → 启用
2. 在目标群 → **设置** → **群机器人** → **添加机器人** → 选择你的**企业自建应用**（不是自定义 Webhook 机器人）

### B.6 触发报告生成

在群里 **@ 你的机器人**，发送：

```text
给我发送竞品报告
```

预期流程：

1. 机器人立即回复：「已收到指令，Pipeline 启动中…」
2. 终端打印 `[FeishuHandler] 收到消息` → `[Collector]` … `[Reviewer]` 全链路日志
3. 数分钟后机器人发送：橙色摘要卡片 → PDF 文件（若权限正常）→ 多条「报告正文 (1/N)」

### B.7 验证 App 凭证（可选）

```powershell
python -c "from src.utils.config_loader import load_dotenv_file; load_dotenv_file(); from src.skills.feishu_openapi_skill import get_tenant_access_token; print('token ok:', get_tenant_access_token()[:20]+'...')"
```

### B.8 常见问题

| 现象 | 处理 |
|------|------|
| 事件订阅保存失败 / challenge 超时 | 先 `python src/feishu_agent_server.py`，再开 ngrok，URL 必须 HTTPS |
| 发消息机器人无反应 | 必须 **@ 机器人**；检查 `im.message.receive_v1` 是否已订阅 |
| `未配置 FEISHU_APP_ID` | 检查 `.env` 与是否 `load_dotenv_file()` |
| 有卡片无 PDF | 检查「上传文件」权限是否开通并重新发布应用 |
| 仍想用 Webhook 跑完即推 | 用 `python src/main.py`，与模式 B 二选一演示 |

触发词可在 `config/config.yaml` → `feishu_agent.trigger_phrases` 中增删。

---

## 三、飞书接入指南（模式 A：群自定义机器人 Webhook）

### 3.1 原理说明

| 方式 | 需要什么 | 行为 |
|------|----------|------|
| **Webhook 机器人（当前使用）** | 群里的「自定义机器人」地址 | `main.py` 跑完后**主动**往群里发消息 |
| 企业自建应用 + 事件订阅（可选） | App ID/Secret + 公网 HTTPS | 用户在群里发指令，服务**被动**触发生成 |

- **模式 A**：本地跑 `main.py`，适合快速验证
- **模式 B**：群里发指令，适合答辩现场演示「对话触发」

### 3.2 在飞书群里创建自定义机器人

1. 打开目标飞书群 → **设置** → **群机器人** → **添加机器人**
2. 选择 **自定义机器人**
3. 安全设置建议：
   - 开启 **自定义关键词**，填入：`竞品报告`（与代码一致）
   - 签名校验可选（开启则需在 `.env` 填 `FEISHU_WEBHOOK_SECRET`）
4. 复制 **Webhook 地址**，形如：

   ```text
   https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

### 3.3 配置本地环境

#### ① 克隆仓库并安装依赖

```bash
git clone https://github.com/clover358/openclaw-analysis-system.git
cd openclaw-analysis-system
python -m venv .venv

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

#### ② 创建 `.env`（不要提交 Git）

在项目根目录新建 `.env`，参考：

```env
# DeepSeek（分析 / 审计）
OPENAI_API_KEY=你的DeepSeek密钥
OPENAI_API_BASE=https://api.deepseek.com/v1

# 智谱（Embedding 向量化）
ZHIPU_API_KEY=你的智谱密钥
ZHIPU_API_BASE=https://open.bigmodel.cn/api/paas/v4

# 飞书群自定义机器人 Webhook（跑完即推）
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/你的Token
FEISHU_WEBHOOK_KEYWORD=竞品报告
FEISHU_WEBHOOK_SECRET=

# true = main.py 跑完后自动推飞书；false = 仅本地生成报告
PIPELINE_PUSH_TO_FEISHU=true
```

> `.env` 已在 `.gitignore` 中，**切勿 push 到 GitHub**。

#### ③ 确认数据文件存在

`data/` 目录下需有 API 排行 JSON（仓库应已包含）：

- `Market Share.json`
- `Top Model.json`
- `Context Length.json`
- `merged_rankings.json`

### 3.4 运行并验证飞书推送

```bash
python src/main.py
```

终端成功标志：

```text
[Feishu] 已启用跑完推送（Webhook: ...）
  飞书是否推送 : 是
[Feishu] 已向群聊推送 N 条消息
```

飞书群里应依次收到：

1. 橙色卡片「OpenClaw 自动化分析报告已生成」
2. 提示「完整正文共 N 条」
3. 多条「报告正文 (1/N)…」纯文本消息

### 3.5 仅测试飞书 Webhook（不跑完整 Pipeline）

```bash
python -m src.skills.feishu_bot_skill
```

或在 Python 中：

```python
from src.utils.config_loader import load_dotenv_file
from src.skills.feishu_bot_skill import send_test_message

load_dotenv_file()
send_test_message()
```

---

## 四、关键文件索引（队友改代码时看哪里）

| 文件 | 职责 |
|------|------|
| `src/main.py` | 一键入口，控制是否 `push_to_feishu` |
| `src/pipeline.py` | 全链路编排；Step 6 PDF；Step 7 飞书推送 |
| `src/skills/feishu_bot_skill.py` | 飞书 Webhook 发送（卡片 / 文本 / 关键词） |
| `src/skills/markdown_pdf_skill.py` | Markdown → PDF |
| `src/agents/collector/agent.py` | API 竞品 JSON 数据采集 |
| `src/agents/analyst/agent.py` | RAG + 商业分析 Prompt |
| `src/agents/generator/agent.py` | 报告生成与润色 |
| `config/config.yaml` | 全局配置（数据源、报告标题、检索词） |
| `.env` | 密钥与飞书 Webhook（本地私有） |

---



---

## 六、常见问题

### Q1：终端显示「飞书是否推送：否」？

检查：

1. `.env` 是否有 `FEISHU_WEBHOOK_URL`
2. `PIPELINE_PUSH_TO_FEISHU` 是否为 `true`
3. 关键词是否为 `竞品报告`（飞书后台与 `.env` 一致）
4. 机器人是否在该群内

飞书返回 `code=19024 Key Words Not Found` → 消息未包含关键词，检查 `FEISHU_WEBHOOK_KEYWORD`。

### Q2：只想本地生成，不打扰飞书群？

```env
PIPELINE_PUSH_TO_FEISHU=false
```

### Q3：想用「群里发指令才生成」？

见本文 **第二节 B**，核心命令：

```powershell
python src/feishu_agent_server.py
ngrok http 8000
```

群里 @ 机器人发送：**给我发送竞品报告**。

---

## 七、Git 与密钥安全

- 已推送分支：`main`（含飞书 Skill 与 Pipeline 集成）
- **不要提交**：`.env`、`.venv/`、`data/processed/*.md`、`data/outputs/*.pdf`
- 密钥泄露后请在 DeepSeek / 智谱 / 飞书后台**轮换**

---

## 八、相关文档

- 开发契约与 Agent 接口：`DEVELOPMENT.md`
- Generator 单独调试：`src/agents/generator/Instruction.md`

---

*文档维护：集成飞书 Webhook + API 竞品分析 Pipeline 的同学。有问题在群里 @ 或提 Issue。*
