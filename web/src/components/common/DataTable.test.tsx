import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DataTable, type ColumnDef } from './DataTable';

vi.mock('@/i18n', () => ({
  t: (key: string) => (key === 'common.next' ? '下一页' : key === 'common.noMore' ? '没有更多了' : key === 'common.empty' ? '暂无数据' : key),
}));

interface Row {
  id: string;
  name: string;
}

const columns: ColumnDef<Row>[] = [
  { key: 'id', header: 'ID' },
  { key: 'name', header: '名称' },
];

const rows: Row[] = [
  { id: '1', name: 'Alpha' },
  { id: '2', name: 'Beta' },
];

describe('DataTable', () => {
  it('渲染表头与数据行', () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} />);
    expect(screen.getByText('ID')).toBeInTheDocument();
    expect(screen.getByText('名称')).toBeInTheDocument();
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
  });

  it('空数据渲染空态', () => {
    render(<DataTable columns={columns} rows={[]} rowKey={(r) => r.id} emptyTitle="暂无运行" />);
    expect(screen.getByText('暂无运行')).toBeInTheDocument();
  });

  it('错误态展示错误信息', () => {
    const error = new Error('请求失败');
    render(<DataTable columns={columns} rows={undefined} error={error} rowKey={(r) => r.id} />);
    expect(screen.getByText('请求失败')).toBeInTheDocument();
  });

  it('行点击回调触发', () => {
    const onRowClick = vi.fn();
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} onRowClick={onRowClick} />);
    fireEvent.click(screen.getByText('Alpha'));
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
  });

  it('hasMore 时显示加载更多按钮并触发回调', () => {
    const onLoadMore = vi.fn();
    render(
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        hasMore
        nextCursor="cursor-2"
        onLoadMore={onLoadMore}
      />,
    );
    const button = screen.getByRole('button', { name: '下一页' });
    fireEvent.click(button);
    expect(onLoadMore).toHaveBeenCalled();
  });

  it('自定义单元格渲染优先于默认取值', () => {
    const customColumns: ColumnDef<Row>[] = [
      { key: 'id', header: 'ID' },
      { key: 'name', header: '名称', render: (r) => <span data-testid="custom">{r.name.toUpperCase()}</span> },
    ];
    render(<DataTable columns={customColumns} rows={rows} rowKey={(r) => r.id} />);
    expect(screen.getByTestId('custom')).toHaveTextContent('ALPHA');
  });
});
