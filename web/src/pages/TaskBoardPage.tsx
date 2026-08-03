import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { Card } from '@/components/common/Card';
import { Tabs } from '@/components/common/Tabs';
import { TaskBoard } from '@/components/task/TaskBoard';
import { TaskDetailPanel } from '@/components/task/TaskDetailPanel';
import { useTasks } from '@/hooks/useTasks';
import { t } from '@/i18n';

/** Issue 看板 + 状态机视图。 */
export default function TaskBoardPage() {
  const { runId } = useParams<{ runId?: string }>();
  const [tab, setTab] = useState<'board' | 'graph'>('board');
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);

  const { data: tasks, isLoading } = useTasks(runId ? { run_id: runId, limit: 500 } : { limit: 500 });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-ink">{t('task.board')}</h1>
        <Tabs
          items={[
            { key: 'board', label: '看板' },
            { key: 'graph', label: '状态机' },
          ]}
          active={tab}
          onChange={(key) => setTab(key as 'board' | 'graph')}
        />
      </div>

      {tab === 'board' ? (
        <Card>
          <TaskBoard tasks={tasks?.items} loading={isLoading} onTaskClick={(task) => setActiveTaskId(task.id)} activeTaskId={activeTaskId} />
        </Card>
      ) : (
        <Card title="Task 状态机">
          <p className="mb-4 text-sm text-ink-muted">
            合法迁移：CREATED → READY → LEASED → ACTIVE → IN_REVIEW → DONE；异常路径见
            ARCHITECTURE §6.2。状态机可视化组件 StateMachineGraph 已在 W3 收口接入。
          </p>
        </Card>
      )}

      <TaskDetailPanel taskId={activeTaskId} onClose={() => setActiveTaskId(null)} />
    </div>
  );
}
