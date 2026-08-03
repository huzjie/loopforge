import type { MemoryItemRead } from '@/api/types';
import { formatRelativeTime } from '@/utils/format';

export interface LineageTreeProps {
  /** 压缩谱系（按 compressed_into 组织为树）。 */
  items: MemoryItemRead[] | undefined;
  loading?: boolean;
}

interface TreeNode extends MemoryItemRead {
  children: TreeNode[];
}

/** 压缩谱系树（摘要 ← 原始条目，可下钻）。 */
export function LineageTree({ items, loading }: LineageTreeProps) {
  if (loading && !items) {
    return <div className="h-32 animate-pulse rounded-md bg-raised" />;
  }
  if (!items || items.length === 0) {
    return <div className="py-6 text-center text-sm text-ink-muted">暂无谱系数据</div>;
  }

  const byId = new Map<string, TreeNode>();
  for (const item of items) {
    byId.set(item.id, { ...item, children: [] });
  }

  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    const parentId = node.compressed_into;
    if (parentId && byId.has(parentId)) {
      byId.get(parentId)?.children.push(node);
    } else {
      roots.push(node);
    }
  }

  const renderNode = (node: TreeNode, depth: number) => (
    <div key={node.id} style={{ marginLeft: depth * 16 }}>
      <div className="mb-1 rounded-md border border-border p-2">
        <p className="text-xs font-medium text-ink">{node.content}</p>
        <p className="mt-0.5 text-[10px] text-ink-faint">
          {node.layer} · {node.tokens} tokens · {formatRelativeTime(node.created_at)}
        </p>
      </div>
      {node.children.length > 0 && (
        <div className="border-l border-border pl-2">{node.children.map((child) => renderNode(child, depth + 1))}</div>
      )}
    </div>
  );

  return <div>{roots.map((node) => renderNode(node, 0))}</div>;
}
