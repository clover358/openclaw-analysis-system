# Generator-Agent v2 使用说明

## 项目简介

`Generator-Agent v2` 是一个通用商业分析报告生成智能体，用于：

* 读取分析文本
* 自动生成结构化商业分析报告
* 调用 DeepSeek API 进行语义润色
* 自动插入图表
* 输出 Markdown 报告文件

适用于：

* 市场分析
* 产品销售分析
* 商业调研
* 数据洞察报告
* AI 自动生成报告场景

---

# 一、运行方式

## 基础命令

```bash
python generator.py -i analysis.txt
```

说明：

* `analysis.txt` 为输入分析文本
* 输出报告默认保存在：

```text
D:\better\openclaw-analysis-system-main\
openclaw-analysis-system-main\
src\agents\generator\data
```

---

# 二、命令行参数说明

| 参数             | 缩写   | 是否必须 | 说明               |
| -------------- | ---- | ---- | ---------------- |
| `--input`      | `-i` | 必须   | 输入分析文本路径         |
| `--output`     | `-o` | 可选   | 指定输出报告路径         |
| `--output-dir` | `-d` | 可选   | 指定输出目录           |
| `--api-key`    | `-k` | 可选   | DeepSeek API Key |
| `--title`      | `-t` | 可选   | 自定义报告标题          |
| `--no-polish`  | 无    | 可选   | 跳过语义润色           |
| `--no-chart`   | 无    | 可选   | 跳过图表生成           |

---

# 三、参数详细说明

---

## 1. 输入文件（必须）

```bash
-i analysis.txt
```

或：

```bash
--input analysis.txt
```

用于指定待分析的文本文件。

支持：

* `.txt`
* 纯文本分析结果
* 预处理后的 AI 分析内容

示例：

```bash
python generator.py -i sales_analysis.txt
```

---

## 2. 指定输出文件

```bash
-o report.md
```

或：

```bash
--output report.md
```

用于指定最终报告输出路径。

示例：

```bash
python generator.py -i analysis.txt -o result.md
```

---

## 3. 指定输出目录

```bash
-d ./output/
```

或：

```bash
--output-dir ./output/
```

用于指定：

* 报告保存目录
* 图表保存目录

示例：

```bash
python generator.py -i analysis.txt -d ./reports/
```

---

## 4. 指定 DeepSeek API Key

```bash
-k sk-xxxxxxxx
```

或：

```bash
--api-key sk-xxxxxxxx
```

用于覆盖默认 API Key。

示例：

```bash
python generator.py -i analysis.txt -k sk-xxxx
```

---

## 5. 自定义报告标题

```bash
-t "2025年市场分析报告"
```

或：

```bash
--title "2025年市场分析报告"
```

示例：

```bash
python generator.py -i analysis.txt -t "2025年大模型 API 竞品分析报告"
```

---

## 6. 跳过语义润色

```bash
--no-polish
```

作用：

* 不调用 DeepSeek API
* 直接使用原始文本
* 用于调试

示例：

```bash
python generator.py -i analysis.txt --no-polish
```

适合：

* API 调试
* 测试解析逻辑
* 离线运行

---

## 7. 跳过图表生成

```bash
--no-chart
```

作用：

* 不生成图表
* 不插入图像

示例：

```bash
python generator.py -i analysis.txt --no-chart
```

适合：

* 快速测试
* 纯文本报告生成

---

# 四、完整使用示例

---

## 示例 1：最基础运行

```bash
python generator.py -i analysis.txt
```

---

## 示例 2：指定输出目录

```bash
python generator.py -i analysis.txt -d ./output/
```

---

## 示例 3：自定义标题

```bash
python generator.py \
-i analysis.txt \
-t "新能源汽车行业分析报告"
```

---

## 示例 4：完整高级配置

```bash
python generator.py \
-i analysis.txt \
-o ./reports/final_report.md \
-d ./reports/ \
-k sk-xxxxxxxx \
-t "2025年大模型 API 竞品分析报告"
```

---

## 示例 5：调试模式

```bash
python generator.py \
-i analysis.txt \
--no-polish \
--no-chart
```

作用：

* 不调用 AI
* 不生成图表
* 仅测试文本处理流程

---

# 五、程序运行流程

程序执行流程如下：

```text
读取输入文本
    ↓
解析分析内容
    ↓
（可选）调用 DeepSeek API 润色
    ↓
（可选）生成图表
    ↓
构建 Markdown 报告
    ↓
保存输出文件
```

---

# 六、输出内容

程序最终会生成：

## 1. Markdown 报告

例如：

```text
market_report.md
```

内容包括：

* 标题
* 摘要
* 市场分析
* 销售分析
* 用户画像
* 竞争分析
* 发展建议
* 图表

---

## 2. 图表文件（如果启用）

例如：

```text
chart_1.png
chart_2.png
```

---

# 七、默认输出目录

默认目录：

```text
D:\better\openclaw-analysis-system-main\
openclaw-analysis-system-main\
src\agents\generator\data
```

如果目录不存在，请提前创建。

---

# 八、常见问题

---

## 1. 提示找不到输入文件

错误：

```text
FileNotFoundError
```

解决：

* 检查输入文件路径
* 使用绝对路径

例如：

```bash
python generator.py -i D:\data\analysis.txt
```

---

## 2. DeepSeek API 调用失败

可能原因：

* API Key 无效
* 网络异常
* 请求次数限制

解决：

```bash
--no-polish
```

先跳过 AI 润色测试流程。

---

## 3. 图表生成失败

可能原因：

* matplotlib 未安装
* 数据格式异常

解决：

```bash
pip install matplotlib pandas
```

或：

```bash
--no-chart
```

---

# 九、推荐运行环境

## Python 版本

推荐：

```text
Python 3.10+
```

---

## 推荐依赖

```bash
pip install pandas matplotlib openai markdown
```

---

# 十、建议目录结构

```text
project/
│
├── generator.py
├── analysis.txt
├── output/
│   ├── report.md
│   ├── chart_1.png
│   └── chart_2.png
```

---

# 十一、终端输出示例

运行成功后：

```text
完成！报告路径：./output/report.md
```

---

# 十二、开发者说明

程序核心入口：

```python
if __name__ == "__main__":
```

核心类：

```python
GeneratorAgent
```

主要方法：

```python
run_from_file()
```

支持扩展：

* Word 导出
* PDF 导出
* 自动 PPT
* 多模型接入
* 图表智能分析
* 多语言报告生成

---
