import { useMutation, useQuery } from '@tanstack/react-query';
import { auditApi } from '@/api/endpoints';
import type { ChainVerifyResult } from '@/api/types';

/** 审计日志查询。 */
export function useAuditLogs(params?: Record<string, unknown>) {
  return useQuery({
    queryKey: ['audit', 'logs', params ?? {}],
    queryFn: () => auditApi.logs(params),
  });
}

/** 哈希链完整性校验。 */
export function useAuditChain() {
  return useMutation({
    mutationFn: (body?: Record<string, unknown>) => auditApi.verifyChain(body),
  });
}

export type { ChainVerifyResult };
