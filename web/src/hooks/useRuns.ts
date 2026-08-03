import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { runApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';
import type { RunControlAction, RunCreate, RunRead, RunUpdate } from '@/api/types';

/** Run 列表查询（游标分页，支持加载更多）。 */
export function useRuns(params?: Record<string, unknown>) {
  return useInfiniteQuery({
    queryKey: queryKeys.runs.list(params),
    queryFn: ({ pageParam }) => runApi.list({ ...params, cursor: pageParam ?? undefined }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

/** 创建 Run。 */
export function useCreateRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RunCreate) => runApi.create(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.all });
    },
  });
}

/** Run 控制指令（start/pause/resume/cancel/retry）。 */
export function useRunControl(runId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: RunControlAction) => runApi.control(runId, action),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.detail(runId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.all });
    },
  });
}

/** 更新 Run。 */
export function useUpdateRun(runId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RunUpdate) => runApi.update(runId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.detail(runId) });
    },
  });
}

/** 删除 Run。 */
export function useDeleteRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => runApi.remove(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.all });
    },
  });
}

/** 从列表数据里按 id 找 Run（乐观更新时用）。 */
export function pickRun(list: RunRead[] | undefined, id: string): RunRead | undefined {
  return list?.find((run) => run.id === id);
}
