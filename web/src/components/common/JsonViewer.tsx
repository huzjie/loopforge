import { useState } from 'react';
import clsx from 'clsx';

export interface JsonViewerProps {
  value: unknown;
  /** 默认展开深度；-1 表示全展开。 */
  defaultDepth?: number;
  maxHeight?: string;
  className?: string;
}

type JsonType = 'object' | 'array' | 'primitive';

function typeOf(value: unknown): JsonType {
  if (value !== null && typeof value === 'object') {
    return Array.isArray(value) ? 'array' : 'object';
  }
  return 'primitive';
}

function renderPrimitive(value: unknown): string {
  if (value === null) return 'null';
  if (typeof value === 'string') return JSON.stringify(value);
  return String(value);
}

/** JSON 折叠查看器（事件 payload / 工具参数）。 */
export function JsonViewer({ value, defaultDepth = 1, maxHeight = '320px', className }: JsonViewerProps) {
  return (
    <div className={clsx('overflow-auto rounded-md bg-raised p-3 font-mono text-xs', className)} style={{ maxHeight }}>
      <JsonNode value={value} depth={0} defaultDepth={defaultDepth} />
    </div>
  );
}

function JsonNode({ value, depth, defaultDepth }: { value: unknown; depth: number; defaultDepth: number }) {
  const t = typeOf(value);

  if (t === 'primitive') {
    const color =
      value === null
        ? 'text-ink-faint'
        : typeof value === 'string'
          ? 'text-ok'
          : typeof value === 'number'
            ? 'text-info'
            : 'text-warn';
    return <span className={color}>{renderPrimitive(value)}</span>;
  }

  const [open, setOpen] = useState(depth < defaultDepth);
  const entries = Object.entries(value as Record<string, unknown>);
  const isArray = Array.isArray(value);
  const preview = entries.length === 0 ? '∅' : `${isArray ? '[' : '{'} ${entries.length} ${isArray ? '项' : '键'} ${isArray ? ']' : '}'}`;

  return (
    <div className="ml-1">
      <button
        className="rounded px-0.5 text-ink-muted hover:bg-border/50"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
      >
        {open ? '▾' : '▸'} {isArray ? 'Array' : 'Object'} <span className="text-ink-faint">{preview}</span>
      </button>
      {open && (
        <div className="ml-3 border-l border-border/60 pl-2">
          {entries.map(([key, child]) => (
            <div key={key} className="py-0.5">
              <span className="text-ink-muted">{isArray ? '' : `"${key}": `}</span>
              <JsonNode value={child} depth={depth + 1} defaultDepth={defaultDepth} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
