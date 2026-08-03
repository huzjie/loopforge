import { Badge, type BadgeTone } from '@/components/common/Badge';
import { runStateMeta, taskStateMeta } from '@/utils/format';

export interface RunStatusBadgeProps {
  state: string;
}

/** Run 状态徽标（14 态配色）。 */
export function RunStatusBadge({ state }: RunStatusBadgeProps) {
  const meta = runStateMeta(state);
  return <Badge tone={meta.tone as BadgeTone}>{meta.label}</Badge>;
}

export interface TaskStatusBadgeProps {
  state: string;
}

/** Task 状态徽标（10 态配色）。 */
export function TaskStatusBadge({ state }: TaskStatusBadgeProps) {
  const meta = taskStateMeta(state);
  return <Badge tone={meta.tone as BadgeTone}>{meta.label}</Badge>;
}
