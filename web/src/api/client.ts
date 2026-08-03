import { useAuthStore } from '@/stores/authStore';
import { mockGet, mockMutate, isMockEnabled } from './mock';
import type { ApiEnvelope } from './types';

/**
 * API 客户端：fetch 封装。
 *
 * 约定（docs/TASK_BREAKDOWN.md §11.8）：
 * - 基址：`/api/v1`（可用 VITE_API_BASE_URL 覆盖）
 * - 认证：`Authorization: Bearer lf_<prefix>_<secret>`（从 authStore 读取）
 * - 写操作自动附加 `Idempotency-Key`
 * - 响应统一信封 `{ code, data, message, trace_id }`，这里解包并抛 `ApiError`
 * - 金额一律字符串十进制，字段 snake_case，不做驼峰转换
 */

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '/api/v1').replace(/\/+$/, '');

/** 最近一次响应的 trace_id（供错误态展示）。 */
let lastTraceId = '';

export function getLastTraceId(): string {
  return lastTraceId;
}

export class ApiError extends Error {
  readonly code: string;
  readonly traceId: string;
  readonly status: number;
  readonly retryable: boolean;
  readonly details: Record<string, unknown>;

  constructor(
    code: string,
    message: string,
    options: { traceId?: string; status?: number; retryable?: boolean; details?: Record<string, unknown> } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.traceId = options.traceId ?? '';
    this.status = options.status ?? 0;
    this.retryable = options.retryable ?? false;
    this.details = options.details ?? {};
  }
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  /** 写操作幂等键；缺省时自动生成。 */
  idempotencyKey?: string;
  /** 查询参数（会被序列化进 URL）。 */
  params?: Record<string, unknown>;
  /** 不自动附带认证头（如 /healthz）。 */
  public?: boolean;
  body?: unknown;
}

function buildUrl(path: string, params?: Record<string, unknown>): string {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  const url = new URL(API_BASE_URL + normalized, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null) continue;
      if (Array.isArray(value)) {
        for (const item of value) url.searchParams.append(key, String(item));
      } else if (typeof value === 'object') {
        url.searchParams.append(key, JSON.stringify(value));
      } else {
        url.searchParams.append(key, String(value));
      }
    }
  }
  return url.pathname + url.search;
}

function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** 真正的 HTTP 请求（mock 关闭时的唯一通路）。 */
async function doRequest<T>(path: string, options: RequestOptions): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase();
  const headers = new Headers(options.headers as HeadersInit | undefined);
  headers.set('Accept', 'application/json');

  if (options.body !== undefined) headers.set('Content-Type', 'application/json');

  const apiKey = useAuthStore.getState().apiKey;
  if (!options.public && apiKey) headers.set('Authorization', `Bearer ${apiKey}`);

  const isWrite = method !== 'GET';
  if (isWrite) headers.set('Idempotency-Key', options.idempotencyKey ?? newIdempotencyKey());

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.params), {
      method,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
      credentials: 'same-origin',
    });
  } catch (err) {
    throw new ApiError('LF-CORE-0002', `网络请求失败：${err instanceof Error ? err.message : String(err)}`, {
      retryable: true,
    });
  }

  const traceId = response.headers.get('X-Trace-Id') ?? '';
  if (traceId) lastTraceId = traceId;

  let payload: ApiEnvelope<T> | null = null;
  try {
    payload = (await response.json()) as ApiEnvelope<T>;
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const code = payload?.code ?? `LF-API-${response.status >= 500 ? '1502' : '1501'}`;
    throw new ApiError(code, payload?.message ?? `HTTP ${response.status}`, {
      traceId: payload?.trace_id ?? traceId,
      status: response.status,
      retryable: response.status >= 500,
      details: payload?.data && typeof payload.data === 'object' ? (payload.data as Record<string, unknown>) : {},
    });
  }

  if (!payload || payload.code !== 'OK') {
    throw new ApiError(payload?.code ?? 'LF-CORE-0002', payload?.message ?? '未知错误', {
      traceId: payload?.trace_id ?? traceId,
      status: response.status,
      details: payload?.data && typeof payload.data === 'object' ? (payload.data as Record<string, unknown>) : {},
    });
  }

  return payload.data;
}

/**
 * 统一请求入口。
 * 开发期启用 mock（VITE_USE_MOCK=true）时，GET 命中 mock 表则返回 mock 数据，
 * 生产构建（import.meta.env.DEV === false）永远不会进入 mock 分支。
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase();
  if (isMockEnabled()) {
    if (method === 'GET') {
      const mocked = mockGet<T>(path, options.params);
      if (mocked !== undefined) return mocked;
    } else {
      const mocked = mockMutate<T>(path, options.body);
      if (mocked !== undefined) return mocked;
    }
  }
  return doRequest<T>(path, options);
}

export const api = {
  get: <T>(path: string, params?: Record<string, unknown>, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'GET', params }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'POST', body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'PUT', body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'PATCH', body }),
  del: <T>(path: string, options?: RequestOptions) => request<T>(path, { ...options, method: 'DELETE' }),
};

export default api;
