import { describe, expect, it } from 'vitest';
import {
  formatUsd,
  formatTokens,
  formatDuration,
  formatBytes,
  formatPercent,
  formatRelativeTime,
  runStateMeta,
  taskStateMeta,
  severityMeta,
} from './format';

describe('utils/format', () => {
  it('formatUsd 处理字符串十进制与边界值', () => {
    expect(formatUsd('12.34567890')).toBe('$12.3457');
    expect(formatUsd('0')).toBe('$0.0000');
    expect(formatUsd(null)).toBe('$0.0000');
    expect(formatUsd(undefined)).toBe('$0.0000');
    expect(formatUsd('abc')).toBe('$0.0000');
  });

  it('formatTokens 千分位缩写', () => {
    expect(formatTokens(0)).toBe('0');
    expect(formatTokens(999)).toBe('999');
    expect(formatTokens(1500)).toBe('1.5k');
    expect(formatTokens(1_200_000)).toBe('1.20M');
    expect(formatTokens(null)).toBe('0');
  });

  it('formatDuration 时间分段', () => {
    expect(formatDuration(500)).toBe('500ms');
    expect(formatDuration(1000)).toBe('1s');
    expect(formatDuration(90_000)).toBe('1m 30s');
    expect(formatDuration(3_700_000)).toBe('1h 1m');
    expect(formatDuration(900_000_000)).toMatch(/\d+d \d+h/);
    expect(formatDuration(null)).toBe('—');
  });

  it('formatBytes 字节缩写', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
    expect(formatBytes(null)).toBe('0 B');
  });

  it('formatPercent 比例转百分比', () => {
    expect(formatPercent(0.314)).toBe('31.4%');
    expect(formatPercent(null)).toBe('0%');
  });

  it('formatRelativeTime 非法输入回退', () => {
    expect(formatRelativeTime(null)).toBe('—');
    expect(formatRelativeTime('not-a-date')).toBe('—');
    expect(formatRelativeTime(new Date().toISOString())).toBeTruthy();
  });

  it('runStateMeta 状态文案与配色', () => {
    expect(runStateMeta('RUNNING')).toMatchObject({ label: '运行中', tone: 'info' });
    expect(runStateMeta('SUCCEEDED')).toMatchObject({ label: '成功', tone: 'ok' });
    expect(runStateMeta('FAILED')).toMatchObject({ label: '失败', tone: 'danger' });
    expect(runStateMeta('UNKNOWN')).toMatchObject({ label: 'UNKNOWN', tone: 'muted' });
  });

  it('taskStateMeta 状态文案', () => {
    expect(taskStateMeta('DONE')).toMatchObject({ label: '完成', tone: 'ok' });
    expect(taskStateMeta('BLOCKED')).toMatchObject({ label: '阻塞', tone: 'warn' });
  });

  it('severityMeta 严重级配色', () => {
    expect(severityMeta('CRITICAL')).toMatchObject({ tone: 'danger' });
    expect(severityMeta('INFO')).toMatchObject({ tone: 'info' });
  });
});
