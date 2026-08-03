import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { policyApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';

/** 策略列表。 */
export function usePolicies(params?: Record<string, unknown>) {
  return useQuery({
    queryKey: queryKeys.policies.list(params),
    queryFn: () => policyApi.list(params),
  });
}

/** 策略详情。 */
export function usePolicyDetail(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.policies.detail(id ?? ''),
    queryFn: () => policyApi.get(id as string),
    enabled: Boolean(id),
  });
}

/** 创建策略。 */
export function useCreatePolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; yaml_source?: string; rules?: unknown[] }) => policyApi.create(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.policies.all });
    },
  });
}

/** 激活策略。 */
export function useActivatePolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => policyApi.activate(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.policies.all });
    },
  });
}
