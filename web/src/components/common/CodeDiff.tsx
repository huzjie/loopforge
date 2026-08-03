import { useMemo, useState } from 'react';
import clsx from 'clsx';

export interface CodeDiffProps {
  /** unified diff 文本（git diff 格式，含 +++/--- 头）。 */
  diff: string;
  className?: string;
  /** 折叠未变更块的最大行数（超过则折叠）。 */
  foldContextAbove?: number;
}

interface DiffLine {
  type: 'add' | 'del' | 'ctx' | 'hunk' | 'meta';
  text: string;
}

function parseDiff(diff: string): DiffLine[] {
  return diff.split('\n').map((line) => {
    if (line.startsWith('@@')) return { type: 'hunk' as const, text: line };
    if (line.startsWith('+++') || line.startsWith('---')) return { type: 'meta' as const, text: line };
    if (line.startsWith('+')) return { type: 'add' as const, text: line };
    if (line.startsWith('-')) return { type: 'del' as const, text: line };
    return { type: 'ctx' as const, text: line };
  });
}

const lineClass: Record<DiffLine['type'], string> = {
  add: 'bg-ok/10 text-ok',
  del: 'bg-danger/10 text-danger',
  ctx: 'text-ink-muted',
  hunk: 'bg-brand/10 font-semibold text-brand',
  meta: 'text-ink-faint',
};

/** Unified diff 渲染：行级高亮、可折叠。 */
export function CodeDiff({ diff, className, foldContextAbove = 50 }: CodeDiffProps) {
  const lines = useMemo(() => parseDiff(diff), [diff]);
  const [collapsed, setCollapsed] = useState(true);

  const ctxCount = lines.filter((line) => line.type === 'ctx').length;
  const foldable = ctxCount > foldContextAbove;
  const shownLines = foldable && collapsed ? lines.filter((line) => line.type !== 'ctx') : lines;

  return (
    <div className={clsx('overflow-auto rounded-md bg-raised font-mono text-xs leading-5', className)}>
      {foldable && (
        <button
          className="sticky left-0 w-full border-b border-border px-3 py-1 text-center text-ink-muted hover:bg-border/40"
          onClick={() => setCollapsed((prev) => !prev)}
        >
          {collapsed ? `展开 ${ctxCount} 行未变更内容` : '折叠未变更内容'}
        </button>
      )}
      <div className="p-2">
        {shownLines.map((line, index) => (
          <div
            key={index}
            className={clsx('whitespace-pre px-2', lineClass[line.type])}
          >
            {line.text || ' '}
          </div>
        ))}
      </div>
    </div>
  );
}
