import { useQuery } from '@tanstack/react-query';
import { runApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';

/** Run 详情。 */
export function useRunDetail(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.runs.detail(id ?? ''),
    queryFn: () => runApi.get(id as string),
    enabled: Boolean(id),
  });
}

/** Run 阶段时间轴。 */
export function useRunTimeline(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.runs.timeline(id ?? ''),
    queryFn: () => runApi.timeline(id as string),
    enabled: Boolean(id),
  });
}

/** Run 成本明细。 */
export function useRunCost(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.runs.cost(id ?? ''),
    queryFn: () => runApi.cost(id as string),
    enabled: Boolean(id),
  });
}

/** Run 任务 DAG。 */
export function useRunTaskGraph(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.runs.taskGraph(id ?? ''),
    queryFn: () => runApi.taskGraph(id as string),
    enabled: Boolean(id),
  });
}
