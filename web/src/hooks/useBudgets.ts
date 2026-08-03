import { useQuery } from '@tanstack/react-query';
import { budgetApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';

/** 预算列表。 */
export function useBudgetList(params?: Record<string, unknown>) {
  return useQuery({
    queryKey: queryKeys.budgets.list(params),
    queryFn: () => budgetApi.list(params),
    refetchInterval: 60_000,
  });
}
