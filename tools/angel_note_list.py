"""
angel_note_list - 列出笔记索引

列出所有已索引的笔记文件，含标题、short_id、更新时间。
"""
from dataclasses import dataclass, field
from typing import List

from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


@dataclass
class NoteListTool(FunctionTool):
    name: str = "angel_note_list"
    description: str = (
        "列出所有已索引的笔记，显示 short_id、标题、行数。"
        "用于查看笔记库中有哪些文件，以及确认新笔记是否已进入索引。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    def __post_init__(self):
        self.logger = logger

    async def run(self, event: AstrMessageEvent) -> str:
        if not hasattr(event, "plugin_context") or event.plugin_context is None:
            return "错误：无法获取插件上下文。"
        plugin_context = event.plugin_context

        note_service = plugin_context.get_component("note_service")
        if note_service is None:
            return "错误：笔记服务不可用。"

        try:
            records: List[dict] = await note_service.search_notes_all()
        except Exception as e:
            self.logger.error(f"{self.name}: 查询笔记索引失败: {e}", exc_info=True)
            return f"错误：查询笔记索引失败：{e}"

        if not records:
            return "笔记库为空，暂无已索引的笔记。"

        lines = [f"笔记索引共 {len(records)} 篇："]
        lines.append("")
        lines.append("| short_id | 标题 | 文件路径 | 行数 |")
        lines.append("|----------|------|----------|------|")
        for r in records:
            sid = r.get("note_short_id", -1)
            heading = (r.get("heading_h1") or "").strip()
            path = (r.get("source_file_path") or "").strip()
            total = r.get("total_lines", 0)
            title = heading if heading else _title_from_path(path)
            short_id_str = str(sid) if sid >= 0 else "?"
            lines.append(f"| {short_id_str} | {title} | {path} | {total} |")

        lines.append("")
        lines.append("提示：使用 angel_note_read(note_short_id) 查看正文。")
        return "\n".join(lines)


def _title_from_path(path: str) -> str:
    """从文件名提取可读标题（去掉时间戳后缀）。"""
    import re
    name = path.split("/")[-1].split("\\")[-1]
    name = name.replace(".md", "").replace(".txt", "")
    name = re.sub(r"_\d{8}_\d{6}$", "", name)
    name = name.strip("_ -")
    return name if name else path
