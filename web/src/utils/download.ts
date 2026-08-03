/**
 * 前端导出工具：JSON / CSV / JSONL 下载。
 * 用于事件导出、成本明细导出等场景。
 */

function triggerDownload(content: string, filename: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

export function downloadJson(data: unknown, filename: string): void {
  triggerDownload(JSON.stringify(data, null, 2), filename, 'application/json;charset=utf-8');
}

export function downloadCsv(rows: Record<string, unknown>[], filename: string): void {
  if (rows.length === 0) {
    triggerDownload('', filename, 'text/csv;charset=utf-8');
    return;
  }
  const headers = Object.keys(rows[0]);
  const escape = (value: unknown): string => {
    const text = value === null || value === undefined ? '' : String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const lines = [headers.join(','), ...rows.map((row) => headers.map((h) => escape(row[h])).join(','))];
  // 加 BOM 以便 Excel 正确识别 UTF-8
  triggerDownload(`\uFEFF${lines.join('\n')}`, filename, 'text/csv;charset=utf-8');
}

export function downloadJsonl(rows: unknown[], filename: string): void {
  const content = rows.map((row) => JSON.stringify(row)).join('\n');
  triggerDownload(content, filename, 'application/x-ndjson;charset=utf-8');
}
