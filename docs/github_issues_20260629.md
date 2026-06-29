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

## Issue 4: rebuild_search_index() 不清理已删除文件的索引残留

**类型:** Bug
**优先级:** 高
**影响版本:** 全版本（当前 Tantivy 检索架构）
**标签:** `bug`, `notes`, `rebuild`, `index`, `cleanup`

### 问题描述

当用户或 AI 从磁盘删除笔记文件后，调用 `angel_note_maintenance(action='rebuild')` 重建索引，已删除的笔记条目**仍然出现在 `angel_note_list` 中**。尝试用 `angel_note_read` 读取已删除笔记时返回「文件不存在」——说明文件确实被删除了，但索引数据没有被清理。

具体现象：
1. 磁盘上实存 4 篇笔记文件
2. `angel_note_list` 仍显示 5 条（含已删除的）
3. 读取已删除的 ID 时报「文件不存在」

### 复现步骤

1. 用 `angel_note_create` 创建至少一篇笔记
2. 通过文件系统或其他方式删除该笔记文件（在 raw 目录下手动删除）
3. 调用 `angel_note_maintenance(action='rebuild')` 重建索引
4. 调用 `angel_note_list()` 查看笔记索引
5. 已删除的笔记条目依然显示在列表中

### 根因分析

`NoteService.rebuild_search_index()` 的实现过于简单，只做了从切片库到 Tantivy 的重建，完全没有与磁盘状态同步：

```python
# 修复前的代码
def rebuild_search_index(self) -> int:
    chunk_store = self.plugin_context.get_component('note_chunk_store')
    all_chunks = chunk_store.list_all_chunks()  # ← 包含已删除文件的数据
    indexed = search_engine.rebuild_all(all_chunks)  # ← 用脏数据重建
    return indexed
```

三个数据源均未清理：
1. **`note_index_records` 注册表** — 仍包含已删除文件的记录，`angel_note_list` 直接读此表
2. **`note_chunks.db` 切片库** — 仍包含已删除文件的切片数据，被 `list_all_chunks()` 返回
3. **`file_index_manager` 文件映射** — 仍保留已删除文件的路径→ID 映射

因此「重建」变成了「用垃圾数据重新索引」，删除的条目永远无法通过 rebuild 清除。

### 修复方案

已在 `llm_memory/service/note_service.py` 中重写 `rebuild_search_index()`：

```
┌─ 1. 扫描 raw 目录，获取实际存在的文件列表
├─ 2. 对比 file_index_manager 注册表，找出已删除文件
├─ 3. 清理已删除文件的所有数据：
│   ├── note_index_records（中央注册表）
│   ├── note_chunk_store（切片库 SQLite）
│   ├── Tantivy 搜索索引
│   └── file_index_manager（文件映射）
├─ 4. 新增磁盘上有但注册表中没有的文件
└─ 5. 从清理后的切片库全量重建 Tantivy 索引
```

修复后的 `rebuild_search_index()` 执行流程：

1. 使用 `os.walk()` 扫描 `raw_dir`，收集所有 `.md`/`.txt` 文件的相对路径
2. 通过 `id_service.file_manager.get_all_files()` 获取注册表中的文件
3. 找出在注册表中但不在磁盘上的文件 ID（`stale_ids`）
4. 对每个已删除文件，依次清理：
   - `memory_sql_manager.delete_note_index_by_file_id()`
   - `chunk_store.delete_by_file_id()`
   - `search_engine.delete_by_file_id()`
   - `file_manager.delete_file()`
5. 找出磁盘上有但注册表中没有的文件，调用 `parse_and_store_file_sync()` 添加
6. 最后从清理后的切片库重建 Tantivy 索引

### 提交记录

```
63e7671 fix(notes): rebuild 前同步磁盘状态，清理已删除文件的索引残留
```

### 验证方式

1. 创建笔记 A、B
2. 从磁盘删除笔记 A 的文件
3. 调用 `angel_note_maintenance(action='rebuild')`
4. 调用 `angel_note_list()` — 应只显示笔记 B
5. `angel_recall()` 搜索 A 的内容 — 应无结果

---

## Issue 5: parse_and_store_file_sync 中 asyncio.run() 嵌套导致注册表更新静默失败

**类型:** Bug
**优先级:** 🔴 高
**影响版本:** 全版本（Tantivy 检索架构）
**标签:** `bug`, `notes`, `async`, `threading`, `registry`

### 问题描述

使用 `angel_note_create` 创建笔记后，笔记文件成功写入磁盘，切片数据成功写入 `note_chunks.db`，但**中央注册表 `note_index_records` 未更新**。导致：
- `angel_note_list` 不显示新笔记
- `angel_recall` 搜索不到新笔记内容
- `angel_note_read` 无法通过 short_id 读取
- `rebuild` 后来源文件数不变

### 复现步骤

1. 连续创建多篇笔记（或在高负载下创建笔记）
2. `angel_note_create` 返回成功
3. 调用 `angel_note_list()` 检查索引
4. 新笔记未出现在列表中
5. 检查 `note_chunks.db` → 切片已存在（179条）
6. 检查 `note_index_records` 表 → 注册表未更新（仍为4篇）

### 根因分析

#### 线程池饥饿

`note_service.py` 中 `parse_and_store_file_sync` 是一个**同步方法**，被 `angel_note_create` 通过 `asyncio.to_thread()` 提交到线程池执行。其内部通过 `asyncio.run()` 调用异步的 `upsert_note_file_entry`：

```
angel_note_create
  → asyncio.to_thread(parse_and_store_file_sync)      ← 线程池线程T1
    → asyncio.run(upsert_note_file_entry(...))          ← T1内创建新事件循环
      → await asyncio.to_thread(_upsert_sync, ...)      ← 同线程池抢线程T2
    → _build_and_store_chunks(...)                      ← T1继续执行
```

当线程池中所有线程都被占满时（例如并发创建多篇笔记、或后台其他 `to_thread` 任务占用），内层的 `asyncio.to_thread()` **无法获取新线程**，导致 `_upsert_note_file_entry_sync` 永远不会执行。但 `asyncio.run()` 不会超时报错——它只是阻塞等待。

**结果：**
- ✅ 文件写入磁盘成功
- ✅ `_build_and_store_chunks` 写入切片库成功（走同步代码，不受影响）
- ❌ `_upsert_note_file_entry_sync` **从未执行**，注册表无记录
- ❌ Tantivy 搜索索引未更新

#### 共 3 处受影响的调用点

| 位置 | 行号 | 方法 | 风险 |
|------|------|------|------|
| `note_service.py` | 原322 | `parse_and_store_file_sync` 注册表插入 | 新笔记无法检索 |
| `note_service.py` | 原147 | `rebuild_search_index` stale 清理 | 已删除文件残留 |
| `note_service.py` | 原430 | `remove_file_data_by_file_id` 注册表删除 | 孤立数据 |

### 修复方案

**文件：`llm_memory/service/note_service.py`**

将全部 3 处 `asyncio.run()` 替换为直接调用对应的同步版本方法：

| 原代码 | 替换为 |
|--------|--------|
| `asyncio.run(memory_sql_manager.upsert_note_file_entry(...))` | `memory_sql_manager._upsert_note_file_entry_sync(...)` |
| `asyncio.run(memory_sql_manager.delete_note_index_by_file_id(...))` | `memory_sql_manager._delete_note_index_by_file_id_sync(...)` |
| 同上 | 同上 |

同时修复 `remove_file_data_by_file_id` 中**异常吞没**问题：
- 原代码：`chunk_store.delete_by_file_id` 和 `search_engine.delete_by_file_id` 的异常被 `try/except` 捕获并打 warning 后继续，返回 `True`
- 修复后：异常传播到外层 `except`，返回 `False`，调用方知道清理失败

**文件：`llm_memory/components/memory_sql_manager.py`**

`_connect()` 添加 WAL 模式（与 `NoteChunkStore` 一致）：
```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
```

减少高并发下 SQLite 锁竞争导致的连接超时。

### 提交记录

```
（将在本次修复后提交）
```

### 验证方式

1. 创建 5 篇笔记
2. 调用 `angel_note_list()` — 应显示 5 篇
3. 从磁盘删除 2 篇
4. 调用 `angel_note_maintenance(action='rebuild')`
5. 调用 `angel_note_list()` — 应只显示 3 篇

---

## 全部修改清单

| 文件 | 操作 | 用途 |
|------|------|------|
| `llm_memory/service/note_service.py` | 修改 | 标题提取 + 索引重建(磁盘同步) + 列表查询 + 消除asyncio.run嵌套 |
| `llm_memory/components/memory_sql_manager.py` | 修改 | 接受标题参数 + 列表查询方法 + WAL模式 |
| `tools/angel_note_list.py` | 新建 | `angel_note_list` LLM 工具 |
| `tools/angel_note_maintenance.py` | 新建 | `angel_note_maintenance` LLM 工具 |
| `main.py` | 修改 | 注册两个新工具 |

---

*文档生成日期: 2026-06-29*
