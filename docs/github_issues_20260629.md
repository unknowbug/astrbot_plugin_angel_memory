# GitHub Issues 提交文档

> 以下文档整理自对 `astrbot_plugin_angel_memory` 插件的代码审计与修复记录。
> 适用于在仓库 https://github.com/kawayiYokami/astrbot_plugin_angel_memory 提交 Issue。

---

## Issue 1: 笔记标题 (heading_h1) 在索引中始终为空

**类型:** Bug
**优先级:** 高
**影响版本:** 当前 Tantivy 检索架构全版本 (>= 1.4.9)
**标签:** `bug`, `notes`, `index`, `title`

### 问题描述

笔记文件写入 raw 目录并索引后，在 `note_index_records` 注册表中 `heading_h1` 至 `heading_h6` 字段始终为空字符串。导致：

1. WebUI「笔记索引」页面中标题列显示为空白
2. `angel_note_read` 返回的索引信息中标题为空
3. 任何依赖标题显示的调试工具均无法展示笔记标题

### 复现步骤

1. 使用 `angel_note_create` 创建一个笔记，例如标题为「第一律公理声明」
2. 等待索引完成
3. 通过 WebUI「笔记索引」页面或 `angel_note_read` 查看笔记信息
4. 观察到标题字段为空

### 根因分析

`llm_memory/components/memory_sql_manager.py` 中的 `_upsert_note_file_entry_sync` 方法在 INSERT 和 ON CONFLICT UPDATE 语句中，将所有 `heading_h1`~`heading_h6` 字段**硬编码为空字符串 `''`**：

```python
# memory_sql_manager.py:695-696
INSERT INTO note_index_records(... heading_h1, heading_h2 ...)
VALUES (?, ?, ?, ?, '', '', '', '', '', '', ?, ?)
#                        ^^^^^^^^^^^^^^^^^^^^^^^^
#                        硬编码空字符串，非参数化
```

```python
# memory_sql_manager.py:700
ON CONFLICT(source_id) DO UPDATE SET
    heading_h1='',  # 始终写死为空
```

同时，`llm_memory/service/note_service.py` 中的 `parse_and_store_file_sync` 方法在调用 `upsert_note_file_entry` 之前，**完全没有尝试从文件内容中提取 Markdown H1 标题**，导致即便数据库端支持传入标题，也没有数据可传。

### 修复方案

已在以下位置实施修复：

**文件: `llm_memory/service/note_service.py`**
- 新增 `_extract_heading_h1(file_path)` 静态方法：读取文件内容，扫描第一个 `# 标题`（支持 `# ` 和 `#title` 两种格式），遇到非标题文本行后停止
- 修改 `parse_and_store_file_sync`：在调用 `upsert_note_file_entry` 前提取标题，并作为 `heading_h1` 参数传入

**文件: `llm_memory/components/memory_sql_manager.py`**
- 为 `upsert_note_file_entry`(async) 和 `_upsert_note_file_entry_sync`(sync) 新增 `heading_h1: str = ''` 参数
- INSERT 语句第5个占位符改为使用传入的 `heading_h1` 参数值
- ON CONFLICT UPDATE 子句改为 `heading_h1=excluded.heading_h1`

---

## Issue 2: 缺少索引重建/刷新的外部接口

**类型:** Feature Request / Bug
**优先级:** 高
**影响版本:** 全版本
**标签:** `enhancement`, `notes`, `maintenance`, `tool`

### 问题描述

当 Tantivy 搜索索引与切片库 (SQLite) 不同步时，没有任何可用的恢复手段。

当前 AI 仅能通过以下工具操作笔记：
- `angel_note_create` — 创建笔记
- `angel_note_read` — 读取笔记
- `angel_recall` — 检索笔记/记忆

**缺少：**
- 触发 Tantivy 索引全量重建的方法
- 查看索引健康状态的方法
- 列出索引注册表以排查问题的方法

### 真实场景复现

1. AI 调用 `angel_note_create` 创建笔记，文件写入成功
2. `parse_and_store_file_sync` 调用 Tantivy writer 时异常被静默捕获
3. 笔记文件存在于磁盘，但 `angel_recall` 搜索不到
4. AI 没有任何工具可以修复此状态

### 根因分析

`note_service.py` 中 `_build_and_store_chunks` 的异常处理过于宽泛：

```python
except Exception as e:
    self.logger.warning(f"切片处理失败（不影响主流程）: {e}")
    return 0
```

Tantivy 的 `index_chunks` 中的异常会被此 catch 捕获并降级为 warning，调用方无从知晓。

### 修复方案

**文件: `llm_memory/service/note_service.py`**
- 新增 `rebuild_search_index()` 方法：全量重建 Tantivy 索引
- 新增 `search_notes_all()` 方法：列出所有笔记索引记录

**文件: `llm_memory/components/memory_sql_manager.py`**
- 新增 `list_all_note_index_records()` 方法

**文件: `tools/angel_note_maintenance.py`（新建）**
- 新增 LLM 工具，支持 `rebuild` 和 `status` 操作

---

## Issue 3: AI 无法列出笔记索引状态

**类型:** Feature Request
**优先级:** 中
**标签:** `enhancement`, `notes`, `tool`

### 问题描述

AI 没有工具查看笔记索引中有哪些文件，无法：
- 确认新创建的笔记是否已进入索引
- 查看笔记的 short_id 与标题对应关系
- 诊断索引缺失问题

### 修复方案

**文件: `tools/angel_note_list.py`（新建）**
- 新增 `angel_note_list` LLM 工具
- 按 Markdown 表格格式返回：short_id、标题(heading_h1)、文件路径、行数

---

## 全部修改清单

| 文件 | 操作 | 用途 |
|------|------|------|
| `llm_memory/service/note_service.py` | 修改 | 标题提取 + 索引重建 + 列表查询 |
| `llm_memory/components/memory_sql_manager.py` | 修改 | 接受标题参数 + 列表查询方法 |
| `tools/angel_note_list.py` | 新建 | `angel_note_list` LLM 工具 |
| `tools/angel_note_maintenance.py` | 新建 | `angel_note_maintenance` LLM 工具 |
| `main.py` | 修改 | 注册两个新工具 |

---

*文档生成日期: 2026-06-29*
