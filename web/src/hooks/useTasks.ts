import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { taskApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';
import type { TaskCreate, TaskStateChange } from '@/api/types';

/** Task 列表查询。 */
export function useTasks(params?: Record<string, unknown>) {
  return useQuery({
    queryKey: queryKeys.tasks.list(params),
    queryFn: () => taskApi.list(params),
    placeholderData: (prev) => prev,
  });
}

/** Task 详情。 */
export function useTaskDetail(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.tasks.detail(id ?? ''),
    queryFn: () => taskApi.get(id as string),
    enabled: Boolean(id),
  });
}

/** Task 的 Step 列表。 */
export function useTaskSteps(id: string | undefined, params?: Record<string, unknown>) {
  return useQuery({
    queryKey: queryKeys.tasks.steps(id ?? ''),
    queryFn: () => taskApi.steps(id as string, params),
    enabled: Boolean(id),
  });
}

/** 手工创建 Task。 */
export function useCreateTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TaskCreate) => taskApi.create(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks.all });
    },
  });
}

/** 显式状态迁移（受状态机校验）。 */
export function useTransitionTask(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TaskStateChange) => taskApi.transition(taskId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks.detail(taskId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks.all });
    },
  });
}
