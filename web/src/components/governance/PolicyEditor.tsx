import { useState } from 'react';
import { Button } from '@/components/common/Button';
import { useCreatePolicy, useActivatePolicy } from '@/hooks/usePolicies';
import { useAuthStore } from '@/stores/authStore';
import { t } from '@/i18n';

export interface PolicyEditorProps {
  /** 初始 YAML 文本。 */
  initialValue?: string;
  onSaved?: () => void;
}

const DEFAULT_YAML = `# LoopForge 策略示例（Rego-like YAML）
name: default
rules:
  - rule_id: R-001
    category: shell
    match: { tool: run_shell, contains: "rm -rf" }
    effect: DENY
    reason: 禁止危险删除
  - rule_id: R-002
    category: vcs
    match: { tool: git_ops, op: push, branch: "main" }
    effect: REQUIRE_APPROVAL
    reason: 主分支推送需要人工审批
`;

/** 策略 YAML 编辑器（保存走后端，语法校验在 W3 接入 dry-run）。 */
export function PolicyEditor({ initialValue = DEFAULT_YAML, onSaved }: PolicyEditorProps) {
  const [value, setValue] = useState(initialValue);
  const [name, setName] = useState('default');
  const createPolicy = useCreatePolicy();
  const activatePolicy = useActivatePolicy();
  const can = useAuthStore((state) => state.can);

  if (!can('admin')) {
    return <p className="text-sm text-ink-muted">需要 admin 权限</p>;
  }

  const save = () => {
    createPolicy.mutate(
      { name, yaml_source: value },
      {
        onSuccess: (result) => {
          activatePolicy.mutate(result.id);
          onSaved?.();
        },
      },
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <label className="text-xs text-ink-muted">策略名</label>
        <input
          className="h-8 rounded-md border border-border bg-surface px-2 text-sm text-ink focus:border-brand focus:outline-none"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <Button size="sm" variant="primary" loading={createPolicy.isPending} onClick={save}>
          {t('common.save')}
        </Button>
      </div>
      <textarea
        className="h-72 w-full rounded-md border border-border bg-raised p-3 font-mono text-xs text-ink focus:border-brand focus:outline-none"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        spellCheck={false}
      />
      {createPolicy.isError && (
        <p className="text-xs text-danger">
          {createPolicy.error instanceof Error ? createPolicy.error.message : String(createPolicy.error)}
        </p>
      )}
    </div>
  );
}
