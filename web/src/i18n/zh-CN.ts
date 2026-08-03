import type { I18nDict } from './index';

/** 中文文案（全量键）。新增 key 时必须同步 en-US.ts。 */
export const zhCN: I18nDict = {
  // 导航
  'nav.overview': '总览',
  'nav.runs': 'Run 列表',
  'nav.tasks': '任务看板',
  'nav.replay': '轨迹回放',
  'nav.costs': '成本分析',
  'nav.policies': '策略管理',
  'nav.approvals': '审批中心',
  'nav.providers': 'Provider',
  'nav.projects': '项目管理',
  'nav.health': '系统健康',
  'nav.settings': '设置',

  // 通用
  'common.loading': '加载中…',
  'common.empty': '暂无数据',
  'common.error': '加载失败',
  'common.retry': '重试',
  'common.cancel': '取消',
  'common.confirm': '确认',
  'common.save': '保存',
  'common.delete': '删除',
  'common.create': '新建',
  'common.edit': '编辑',
  'common.search': '搜索',
  'common.close': '关闭',
  'common.refresh': '刷新',
  'common.download': '导出',
  'common.traceId': 'trace_id',
  'common.actions': '操作',
  'common.status': '状态',
  'common.createdAt': '创建时间',
  'common.updatedAt': '更新时间',
  'common.next': '下一页',
  'common.prev': '上一页',
  'common.noMore': '没有更多了',
  'common.viewDetail': '查看详情',

  // 顶部栏
  'topbar.health': '健康',
  'topbar.theme': '切换主题',
  'topbar.lang': '语言',
  'topbar.pendingApprovals': '待审批',
  'topbar.activeRuns': '活跃 Run',

  // 总览
  'overview.title': '总览大盘',
  'overview.activeRuns': '活跃 Run',
  'overview.todayCost': '今日成本',
  'overview.totalTasks': '任务总数',
  'overview.successRate': '成功率',
  'overview.queueDepth': '队列深度',
  'overview.pendingApprovals': '待审批',
  'overview.recentRuns': '最近 Run',
  'overview.viewAll': '查看全部',

  // Run
  'run.list': 'Run 列表',
  'run.create': '创建 Run',
  'run.goal': '目标',
  'run.state': '状态',
  'run.loops': '轮次',
  'run.cost': '成本',
  'run.duration': '耗时',
  'run.detail': 'Run 详情',
  'run.timeline': '时间轴',
  'run.events': '事件流',
  'run.tasks': '任务',
  'run.controls.start': '启动',
  'run.controls.pause': '暂停',
  'run.controls.resume': '恢复',
  'run.controls.cancel': '取消',
  'run.controls.retry': '重试',
  'run.createGoal': '输入目标…',

  // Task
  'task.board': '任务看板',
  'task.title': '标题',
  'task.priority': '优先级',
  'task.worker': 'Worker',
  'task.attempts': '尝试',
  'task.lease': '租约',

  // 事件
  'event.stream': '实时事件流',
  'event.paused': '已暂停跟随',
  'event.resume': '恢复跟随',
  'event.filter': '筛选',

  // 成本
  'cost.title': '成本分析',
  'cost.today': '今日',
  'cost.week': '本周',
  'cost.total': '累计',
  'cost.rate': '速率（$/h）',

  // 设置
  'settings.title': '设置',
  'settings.apiKey': 'API Key',
  'settings.theme': '主题',
  'settings.language': '语言',
  'settings.apiKeyPlaceholder': '粘贴 lf_ 开头的 API Key',
  'settings.saveKey': '保存 Key',

  // 404
  'notFound.title': '页面不存在',
  'notFound.back': '返回首页',

  // 错误边界
  'errorBoundary.title': '出错了',
  'errorBoundary.reload': '重新加载',
};
