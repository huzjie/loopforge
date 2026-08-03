import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { approvalApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';
import { useAuthStore } from '@/stores/authStore';

/** 审批列表。 */
export function useApprovals(params?: Record<string, unknown>) {
  return useQuery({
    queryKey: queryKeys.approvals.list(params),
    queryFn: () => approvalApi.list(params),
    refetchInterval: 30_000,
  });
}

/** 审批汇总（待办计数，供顶栏红点轮询）。 */
export function useApprovalSummary() {
  return useQuery({
    queryKey: queryKeys.approvals.summary,
    queryFn: () => approvalApi.summary(),
    refetchInterval: 30_000,
  });
}

/** 审批裁决（批准/拒绝）。 */
export function useApprovalDecision(approvalId: string) {
  const queryClient = useQueryClient();
  const principal = useAuthStore((state) => state.principal);
  return useMutation({
    mutationFn: ({ approve, reason }: { approve: boolean; reason?: string }) => {
      const decidedBy = principal?.username ?? principal?.subject_id ?? 'web-ui';
      return approve
        ? approvalApi.approve(approvalId, { decided_by: decidedBy, reason })
        : approvalApi.reject(approvalId, { decided_by: decidedBy, reason });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.approvals.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.approvals.summary });
    },
  });
}
