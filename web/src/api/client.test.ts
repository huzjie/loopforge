import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { request, ApiError, API_BASE_URL, getLastTraceId } from './client';
import { useAuthStore } from '@/stores/authStore';

// 统一打桩：client 内部使用 window.location.origin 构造 URL。
const ORIGIN = 'http://localhost:5173';

function mockFetchOnce(body: unknown, init: { status?: number; headers?: Record<string, string> } = {}) {
  const response = {
    ok: (init.status ?? 200) >= 200 && (init.status ?? 200) < 300,
    status: init.status ?? 200,
    headers: new Headers(init.headers ?? {}),
    json: async () => body,
  } as Response;
  return vi.fn().mockResolvedValue(response);
}

describe('api/client', () => {
  beforeEach(() => {
    useAuthStore.getState().setApiKey(null);
    window.history.replaceState({}, '', '/');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('解包成功信封返回 data', async () => {
    vi.stubGlobal('fetch', mockFetchOnce({ code: 'OK', data: { id: 'run-1' }, message: '', trace_id: 't1' }));
    const data = await request<{ id: string }>('/runs/1');
    expect(data.id).toBe('run-1');
    expect(getLastTraceId()).toBe('t1');
  });

  it('失败信封抛 ApiError 且携带错误码与 trace_id', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetchOnce(
        { code: 'LF-RUN-0301', data: null, message: 'Run 不存在', trace_id: 't2' },
        { status: 404, headers: { 'X-Trace-Id': 't2' } },
      ),
    );
    const err = await request('/runs/missing').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    if (err instanceof ApiError) {
      expect(err.code).toBe('LF-RUN-0301');
      expect(err.status).toBe(404);
      expect(err.traceId).toBe('t2');
    }
  });

  it('自动附加 Bearer API Key 头（从 authStore 读取）', async () => {
    useAuthStore.getState().setApiKey('lf_test_secret');
    const fetchMock = mockFetchOnce({ code: 'OK', data: [], message: '', trace_id: 't3' });
    vi.stubGlobal('fetch', fetchMock);
    await request('/runs');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`${API_BASE_URL}/runs`);
    const headers = new Headers(init.headers as HeadersInit);
    expect(headers.get('Authorization')).toBe('Bearer lf_test_secret');
  });

  it('写操作自动附加 Idempotency-Key 头', async () => {
    const fetchMock = mockFetchOnce({ code: 'OK', data: { id: 'r1' }, message: '', trace_id: 't4' });
    vi.stubGlobal('fetch', fetchMock);
    await request('/runs', { method: 'POST', body: { goal: 'x' } });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers as HeadersInit);
    expect(headers.get('Idempotency-Key')).toBeTruthy();
  });

  it('查询参数序列化（数组重复 key，null 跳过）', async () => {
    const fetchMock = mockFetchOnce({ code: 'OK', data: { items: [], next_cursor: null, has_more: false, total: 0 }, message: '', trace_id: 't5' });
    vi.stubGlobal('fetch', fetchMock);
    await request('/events', { params: { types: ['RUN_STARTED', 'TASK_DONE'], limit: 10, state: null } });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain('types=RUN_STARTED');
    expect(url).toContain('types=TASK_DONE');
    expect(url).toContain('limit=10');
    expect(url).not.toContain('state');
  });

  it('网络异常归一化为可重试的 ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const err = await request('/runs').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    if (err instanceof ApiError) {
      expect(err.retryable).toBe(true);
      expect(err.code).toBe('LF-CORE-0002');
    }
  });

  it('非 2xx 但无法解析信封时给出 HTTP 状态错误', async () => {
    vi.stubGlobal('fetch', mockFetchOnce('oops', { status: 500 }));
    const err = await request('/runs').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    if (err instanceof ApiError) {
      expect(err.status).toBe(500);
      expect(err.retryable).toBe(true);
    }
  });
});
