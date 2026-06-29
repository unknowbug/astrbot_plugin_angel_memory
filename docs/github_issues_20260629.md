# Angel Memory Plugin — Issues 汇总

> 整理自代码审计与修复记录。
> 仓库: https://github.com/kawayiYokami/astrbot_plugin_angel_memory

---

## Issue 1: 笔记标题 (heading_h1) 在索引中始终为空

**类型:** Bug | **优先级:** 高 | **标签:** `bug` `notes` `index` `title`

### 问题描述

笔记写入索引后，`note_index_records` 表中 `heading_h1`~`heading_h6` 字段始终为空字符串。导致 WebUI 和调试工具中的标题列空白。

### 根因

`memory_sql_manager.py` 中 `_upsert_note_file_entry_sync` 的 INSERT 语句将 `heading_h1` 硬编码为 `''`：

```python
# 第 695 行
VALUES (?, ?, ?, ?, '', '', '', '', '', '', ?, ?)
```

同时 `note_service.py` 的 `parse_and_store_file_sync` 从未从文件内容中提取 Markdown H1 标题。

### 修复

- `note_service.py`: 新增 `_extract_heading_h1(file_path)` 从文件内容提取第一个 `# 标题`
- `memory_sql_manager.py`: `upsert_note_file_entry` 接受 `heading_h1` 参数，INSERT 使用参数值
- `commit`: `e13e45b`

---

## Issue 2: 笔记创建后注册表更新静默失败

**类型:** Bug | **优先级:** 高 | **标签:** `bug` `notes` `async` `threading`

### 问题描述

`angel_note_create` 返回成功，文件写入磁盘，切片写入 `note_chunks.db`，但 `note_index_records` 注册表未更新。新笔记无法被 `angel_recall` 检索到。

### 复现

1. 连续创建多篇笔记
2. `angel_note_create` 返回成功
3. `angel_note_list` 不显示新笔记
4. `note_chunks.db` 有切片数据，但 `note_index_records` 无对应记录

### 根因

`parse_and_store_file_sync` 通过 `asyncio.to_thread()` 在线程池中执行，内部又用 `asyncio.run()` 调用 `upsert_note_file_entry`（该方法内部再 `await asyncio.to_thread(_upsert_sync)`）。当线程池满时，内层 `to_thread` 阻塞等待，注册表写入静默超时：

```
angel_note_create
  → asyncio.to_thread(parse_and_store_file_sync)     ← 线程T1
    → asyncio.run(upsert_note_file_entry(...))         ← T1内建事件循环
      → await asyncio.to_thread(_upsert_sync)          ← 抢线程T2（可能无空闲）
    → _build_and_store_chunks(...)                     ← 继续执行，不受影响
```

共 3 处受影响的调用点：

| 位置 | 方法 | 风险 |
|------|------|------|
| `note_service.py:322` | `parse_and_store_file_sync` 注册表插入 | 新笔记无法检索 |
| `note_service.py:147` | `rebuild_search_index` stale 清理 | 已删除文件残留 |
| `note_service.py:430` | `remove_file_data_by_file_id` 注册表删除 | 孤立数据 |

### 修复

- 替换全部 3 处 `asyncio.run()` 为直接调用同步方法（`_upsert_note_file_entry_sync` / `_delete_note_index_by_file_id_sync`）
- `memory_sql_manager._connect()` 添加 WAL 模式减少锁竞争
- `remove_file_data_by_file_id` 不再吞没 chunk/Tantivy 删除异常
- `commit`: `24dec5c`

---

## Issue 3: rebuild 后已删除文件的索引残留

**类型:** Bug | **优先级:** 高 | **标签:** `bug` `notes` `rebuild` `cleanup`

### 问题描述

从磁盘删除笔记文件后调用 `angel_note_maintenance(action='rebuild')`，已删除笔记仍然出现在 `angel_note_list` 中。`angel_note_read` 报「文件不存在」。

### 根因

原 `rebuild_search_index()` 只做 `chunk_store.list_all_chunks() → search_engine.rebuild_all(chunks)`，完全不与磁盘状态同步。三个数据源均未清理：
1. `note_index_records` 注册表 — 残留已删除记录
2. `note_chunks.db` 切片库 — 残留切片数据
3. `file_index_manager` 文件映射 — 残留路径→ID 映射

### 修复

`rebuild_search_index()` 重写为：

```
1. os.walk() 扫描 raw 目录，获取实际文件列表
2. 对比 file_index_manager 注册表，找出已删除文件
3. 清理四层数据：注册表 → 切片库 → Tantivy → 文件索引
4. 新增磁盘上有但注册表中没有的文件
5. 从清理后的切片库全量重建 Tantivy 索引
```

- `commit`: `63e7671`

---

## Issue 4: 缺少索引维护与状态查看工具

**类型:** Feature Request | **优先级:** 高 | **标签:** `enhancement` `notes` `tool`

### 问题描述

AI 仅有 4 个笔记工具（create/read/recall/remember），缺少：
- 触发索引全量重建的方法
- 查看索引健康状态的方法
- 列出索引注册表排查问题的方法

### 修复

新增 2 个 LLM 工具：

**`angel_note_list`** — 列出所有笔记索引，显示 short_id、标题、路径、行数

**`angel_note_maintenance`** — 维护工具，支持：
- `action='rebuild'` — 全量同步磁盘 + 重建索引
- `action='status'` — 查看注册表笔记数、切片数

- `commit`: `e13e45b`

---

## Issue 5: remove_file_data_by_file_id 异常吞没导致孤立数据

**类型:** Bug | **优先级:** 中 | **标签:** `bug` `notes` `cleanup`

### 问题描述

`remove_file_data_by_file_id` 中，如果注册表删除成功但切片库/Tantivy 删除失败，异常被 `try/except` 吞没并打 warning，方法仍返回 `True`。调用方（file_monitor / rebuild）认为清理完成，实际留下了孤立切片和 Tantivy 残影。

### 根因

```python
try:
    chunk_store.delete_by_file_id(str(file_id))
except Exception as e:
    self.logger.warning(f"删除切片存储失败（不影响主流程）: {e}")
```

### 修复

移除异常吞没，让异常传播到外层 `except`，返回 `False` 通知调用方。

- `commit`: `24dec5c`（与 Issue 2 同一次提交）

---

## 全部修改清单

| 文件 | 操作 | 用途 |
|------|------|------|
| `llm_memory/service/note_service.py` | 修改 | 标题提取 + 索引重建(磁盘同步) + 列表查询 + 消除 asyncio.run 嵌套 + 原子性修复 |
| `llm_memory/components/memory_sql_manager.py` | 修改 | 接受标题参数 + 列表查询方法 + WAL 模式 |
| `tools/angel_note_list.py` | 新建 | `angel_note_list` LLM 工具 |
| `tools/angel_note_maintenance.py` | 新建 | `angel_note_maintenance` LLM 工具 |
| `main.py` | 修改 | 注册两个新工具 |
| `docs/github_issues_20260629.md` | 修改 | 此文档 |

---

## 提交历史

| Commit | 说明 |
|--------|------|
| `e13e45b` | fix(notes): 提取笔记 H1 标题写入索引，新增索引维护与列表工具 |
| `63e7671` | fix(notes): rebuild 前同步磁盘状态，清理已删除文件的索引残留 |
| `24dec5c` | fix(notes): 消除 asyncio.run() 嵌套导致的注册表更新静默失败 |

---

*生成日期: 2026-06-29*
