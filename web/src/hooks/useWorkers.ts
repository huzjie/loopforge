import { useQuery } from '@tanstack/react-query';
import { workerApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';

/** Worker 列表（含健康心跳）。 */
export function useWorkers(params?: Record<string, unknown>) {
  return useQuery({
    queryKey: queryKeys.workers.list(params),
    queryFn: () => workerApi.list(params),
    refetchInterval: 15_000,
  });
}
