/**
 * 时间工具：ISO 8601 解析、倒计时、时间窗计算。
 * 后端时间格式统一为 ISO 8601 UTC（带毫秒与 Z）。
 */

export function parseIso(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function toIso(date: Date = new Date()): string {
  return date.toISOString();
}

export function nowIso(): string {
  return new Date().toISOString();
}

/** 距目标时间的剩余毫秒（过期返回 0）。 */
export function msUntil(iso: string | null | undefined): number {
  const date = parseIso(iso);
  if (!date) return 0;
  return Math.max(0, date.getTime() - Date.now());
}

/** 距目标时间的剩余秒数（过期返回 0）。 */
export function secondsUntil(iso: string | null | undefined): number {
  return Math.floor(msUntil(iso) / 1000);
}

export interface TimeWindow {
  start: string;
  end: string;
}

/** 最近 N 小时的时间窗（闭区间）。 */
export function lastHours(hours: number): TimeWindow {
  const end = new Date();
  const start = new Date(end.getTime() - hours * 3600_000);
  return { start: start.toISOString(), end: end.toISOString() };
}

/** 今天零点到现在的窗（本地时区）。 */
export function todayWindow(): TimeWindow {
  const end = new Date();
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  return { start: start.toISOString(), end: end.toISOString() };
}

/** 最近 N 天零点到现在的窗。 */
export function lastDays(days: number): TimeWindow {
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - (days - 1));
  start.setHours(0, 0, 0, 0);
  return { start: start.toISOString(), end: end.toISOString() };
}
