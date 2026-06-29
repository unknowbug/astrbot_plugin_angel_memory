"""
angel_note_maintenance - 笔记索引维护工具

提供索引重建、状态查看等维护功能。
"""
from dataclasses import dataclass, field

from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


@dataclass
class NoteMaintenanceTool(FunctionTool):
    name: str = "angel_note_maintenance"
    description: str = (
        "笔记索引维护工具。支持操作:\n"
        "- rebuild: 从磁盘文件全量重建搜索索引（修复索引不同步问题）\n"
        "- status: 查看索引状态（切片数、文件数）\n"
        "如发现笔记写入后无法检索到，可用 rebuild 修复。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作: rebuild = 重建搜索索引, status = 查看状态",
                    "enum": ["rebuild", "status"],
                },
            },
            "required": ["action"],
        }
    )

    def __post_init__(self):
        self.logger = logger

    async def run(self, event: AstrMessageEvent, action: str) -> str:
        if not hasattr(event, "plugin_context") or event.plugin_context is None:
            return "错误：无法获取插件上下文。"
        plugin_context = event.plugin_context

        note_service = plugin_context.get_component("note_service")
        if note_service is None:
            return "错误：笔记服务不可用。"

        if action == "status":
            return await self._show_status(plugin_context, note_service)
        elif action == "rebuild":
            return await self._rebuild(plugin_context, note_service)
        else:
            return f"错误：未知操作 '{action}'，支持 rebuild 或 status。"

    async def _show_status(self, plugin_context, note_service) -> str:
        """显示索引状态"""
        try:
            records = await note_service.search_notes_all()
            file_count = len(records)

            chunk_store = plugin_context.get_component("note_chunk_store")
            chunk_count = 0
            if chunk_store:
                stats = chunk_store.get_stats()
                chunk_count = stats.get("total_chunks", 0)

            lines = ["笔记索引状态："]
            lines.append(f"- 注册表笔记数: {file_count}")
            lines.append(f"- 切片库切片数: {chunk_count}")
            lines.append("")
            if file_count == 0:
                lines.append("笔记库为空。")
            else:
                lines.append("如需修复索引同步问题，请使用 angel_note_maintenance(action='rebuild')。")
            return "\n".join(lines)
        except Exception as e:
            self.logger.error(f"状态查询失败: {e}", exc_info=True)
            return f"错误：状态查询失败：{e}"

    async def _rebuild(self, plugin_context, note_service) -> str:
        """全量重建搜索索引"""
        try:
            import time
            start = time.time()
            indexed = note_service.rebuild_search_index()
            elapsed = time.time() - start

            chunk_store = plugin_context.get_component("note_chunk_store")
            stats = chunk_store.get_stats() if chunk_store else {}

            return (
                f"搜索索引全量重建完成！\n"
                f"- 索引切片数: {indexed}\n"
                f"- 来源文件数: {stats.get('total_files', 0)}\n"
                f"- 耗时: {elapsed:.2f} 秒\n"
                f"\n现在所有笔记应已立即可检索。如果仍有问题，请使用 angel_note_list 确认索引状态。"
            )
        except Exception as e:
            self.logger.error(f"索引重建失败: {e}", exc_info=True)
            return f"错误：索引重建失败：{e}"
