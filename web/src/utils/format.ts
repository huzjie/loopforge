import { formatDistanceToNowStrict, format } from 'date-fns';
import { zhCN, enUS } from 'date-fns/locale';

export type LocaleCode = 'zh-CN' | 'en-US';

/**
 * 格式化工具：金额、token、时长、字节、相对时间、状态文案。
 * 金额输入一律是字符串十进制（后端约定），输出两位小数。
 */

export function formatUsd(value: string | number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || value === '') return '$0.0000';
  const num = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(num)) return '$0.0000';
  return `$${num.toFixed(digits)}`;
}

export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '—';
  if (ms < 1_000) return `${Math.round(ms)}ms`;
  const seconds = Math.floor(ms / 1_000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function formatPercent(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return '0%';
  return `${(ratio * 100).toFixed(1)}%`;
}

export function formatDateTime(iso: string | null | undefined, locale: LocaleCode = 'zh-CN'): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return format(date, 'yyyy-MM-dd HH:mm:ss', { locale: locale === 'zh-CN' ? zhCN : enUS });
}

export function formatRelativeTime(iso: string | null | undefined, locale: LocaleCode = 'zh-CN'): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return formatDistanceToNowStrict(date, { addSuffix: true, locale: locale === 'zh-CN' ? zhCN : enUS });
}

export interface StateMeta {
  label: string;
  tone: 'ok' | 'warn' | 'danger' | 'info' | 'muted';
}

/** Run 状态 → 徽标语义（14 态配色，见 ARCHITECTURE §6.1）。 */
export function runStateMeta(state: string): StateMeta {
  const map: Record<string, StateMeta> = {
    CREATED: { label: '已创建', tone: 'muted' },
    PLANNING: { label: '规划中', tone: 'info' },
    READY: { label: '就绪', tone: 'info' },
    RUNNING: { label: '运行中', tone: 'info' },
    PAUSING: { label: '暂停中', tone: 'warn' },
    PAUSED: { label: '已暂停', tone: 'warn' },
    RECONCILING: { label: '对账中', tone: 'info' },
    RECOVERING: { label: '恢复中', tone: 'warn' },
    AWAITING_APPROVAL: { label: '待审批', tone: 'warn' },
    HALTED: { label: '已停机', tone: 'danger' },
    CANCELLING: { label: '取消中', tone: 'warn' },
    CANCELLED: { label: '已取消', tone: 'muted' },
    SUCCEEDED: { label: '成功', tone: 'ok' },
    FAILED: { label: '失败', tone: 'danger' },
  };
  return map[state] ?? { label: state, tone: 'muted' };
}

/** Task 状态 → 徽标语义（10 态配色，见 ARCHITECTURE §6.2）。 */
export function taskStateMeta(state: string): StateMeta {
  const map: Record<string, StateMeta> = {
    CREATED: { label: '已创建', tone: 'muted' },
    READY: { label: '就绪', tone: 'info' },
    LEASED: { label: '已租约', tone: 'info' },
    ACTIVE: { label: '执行中', tone: 'info' },
    BLOCKED: { label: '阻塞', tone: 'warn' },
    IN_REVIEW: { label: '评审中', tone: 'warn' },
    DEFERRED: { label: '推迟', tone: 'muted' },
    DONE: { label: '完成', tone: 'ok' },
    FAILED: { label: '失败', tone: 'danger' },
    CANCELLED: { label: '已取消', tone: 'muted' },
  };
  return map[state] ?? { label: state, tone: 'muted' };
}

/** 严重级 → 徽标语义。 */
export function severityMeta(severity: string): StateMeta {
  const map: Record<string, StateMeta> = {
    DEBUG: { label: 'DEBUG', tone: 'muted' },
    INFO: { label: 'INFO', tone: 'info' },
    NOTICE: { label: 'NOTICE', tone: 'info' },
    WARNING: { label: 'WARN', tone: 'warn' },
    ERROR: { label: 'ERROR', tone: 'danger' },
    CRITICAL: { label: 'CRIT', tone: 'danger' },
  };
  return map[severity] ?? { label: severity, tone: 'muted' };
}
