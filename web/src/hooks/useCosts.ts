import { useQuery } from '@tanstack/react-query';
import { costApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';

/** 成本汇总（今日/本周/累计）。 */
export function useCostSummary() {
  return useQuery({
    queryKey: queryKeys.costs.summary,
    queryFn: () => costApi.summary(),
    refetchInterval: 60_000,
  });
}

/** 成本时序（按小时/天）。 */
export function useCostTimeseries(params?: Record<string, unknown>) {
  return useQuery({
    queryKey: queryKeys.costs.timeseries(params),
    queryFn: () => costApi.timeseries(params),
  });
}

/** 成本五维下钻。 */
export function useCostBreakdown(params?: Record<string, unknown>) {
  return useQuery({
    queryKey: queryKeys.costs.breakdown(params),
    queryFn: () => costApi.breakdown(params),
  });
}
