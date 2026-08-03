"""审计导出器 —— 把审计链记录导出为 JSON / CSV / JSONL。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``audit/exporter.py``。
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from loopforge.governance.audit.chain import ChainRecord

__all__ = ["AuditExporter", "export_records"]


def _row(record: ChainRecord) -> dict[str, Any]:
    """转成平铺字典。"""
    return {
        "seq": record.seq,
        "prev_hash": record.prev_hash,
        "hash": record.hash,
        "payload": json.dumps(record.payload, ensure_ascii=False, sort_keys=True),
        "created_at": record.created_at,
    }


class AuditExporter:
    """审计导出器。

    :param format: 导出格式：``json`` / ``jsonl`` / ``csv``。
    """

    def __init__(self, format: str = "json") -> None:
        """初始化导出器。"""
        if format not in ("json", "jsonl", "csv"):
            raise ValueError(f"format 必须是 json/jsonl/csv，收到 {format!r}")
        self.format: str = format

    def export(self, records: Sequence[ChainRecord]) -> str:
        """把记录导出为字符串。

        :param records: 链记录列表。
        :returns: 导出文本。
        """
        if self.format == "json":
            return json.dumps(
                [_row(record) for record in records],
                ensure_ascii=False,
                indent=2,
            )
        if self.format == "jsonl":
            return "\n".join(
                json.dumps(_row(record), ensure_ascii=False, sort_keys=True)
                for record in records
            )
        # csv
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=["seq", "prev_hash", "hash", "payload", "created_at"],
        )
        writer.writeheader()
        for record in records:
            writer.writerow(_row(record))
        return buffer.getvalue()

    def export_to_file(self, records: Sequence[ChainRecord], path: Path | str) -> Path:
        """导出到文件。

        :param records: 链记录列表。
        :param path: 目标文件路径（父目录自动创建）。
        :returns: 写入的文件路径。
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.export(records), encoding="utf-8")
        return target


def export_records(
    records: Sequence[ChainRecord],
    format: str = "json",
) -> str:
    """便捷函数：导出链记录。

    :param records: 链记录列表。
    :param format: ``json`` / ``jsonl`` / ``csv``。
    :returns: 导出文本。
    """
    return AuditExporter(format=format).export(records)
