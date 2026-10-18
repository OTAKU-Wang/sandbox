// 图 2-4 的固定文案与"这一格到底有没有来源"的映射层：这些字符串是要和原型逐字对齐的，
// 所以直接锁常量；再加上内存未计量、状态三态、破折号三条口径的纯函数锁。
import { describe, expect, it } from 'vitest';
import {
  DEV_TASK_NODE_HINT,
  MEMORY_UNMEASURED_FALLBACK,
  MEMORY_USAGE_UNMEASURED,
  NO_ACTIVE_DEBUG_SESSION_NOTE,
  NO_SOURCE_TEXT,
  PRODUCT_ORG_HINT,
  RESULT_SET_SCOPE_HINT,
  SANDBOX_MEMORY_RUNNING_NOTE,
  formatPrototypeMemory,
  formatRunDuration,
  memoryUnmeasuredReason,
  productTypeLabel,
  runRecordStatusMeta,
  taskNodeText,
} from '../devTasksModel';
import { HOME_MEMORY_UNMEASURED } from './devFixtures';

describe('图 2-4 固定文案逐字锁（照机器转录 group-B.md §图 2）', () => {
  it('资源配置 / 沙箱释放两句照图原文', () => {
    expect(SANDBOX_MEMORY_RUNNING_NOTE).toBe('沙箱环境运行中，退出调试后自动释放内存');
    expect(MEMORY_USAGE_UNMEASURED).toBe('未计量');
    expect(NO_ACTIVE_DEBUG_SESSION_NOTE).toBe('当前无活跃调试会话，没有待释放的内存');
  });

  it('三条口径说明都挂在 tooltip 上，且都写明来源而不是空话', () => {
    expect(RESULT_SET_SCOPE_HINT).toContain('没有任务维度外键');
    expect(PRODUCT_ORG_HINT).toContain('/users/options');
    expect(DEV_TASK_NODE_HINT).toContain('user.organization');
    expect(MEMORY_UNMEASURED_FALLBACK).toContain('没有');
  });

  it('表格里「整项没有来源」的占位是破折号，不是 0 也不是「未配置」', () => {
    expect(NO_SOURCE_TEXT).toBe('—');
  });
});

describe('资源配置的数值与使用量口径', () => {
  it('配额按图上的单位写作「N G」，非整数保留一位；缺字段记未记录', () => {
    expect(formatPrototypeMemory(8192)).toBe('8 G');
    expect(formatPrototypeMemory(2560)).toBe('2.5 G');
    expect(formatPrototypeMemory(null)).toBe('未记录');
    expect(formatPrototypeMemory(undefined)).toBe('未记录');
  });

  it('使用量的理由优先引后端原话（home.not_collected 的 memory.measured）', () => {
    expect(memoryUnmeasuredReason(HOME_MEMORY_UNMEASURED.not_collected)).toContain('Cube 节点 RPC');
    // 后端没给这一项时退回固定文案，不自己另编一套理由
    // 后端自己给了理由，就照抄后端那句，一个字都不改
    expect(memoryUnmeasuredReason(HOME_MEMORY_UNMEASURED.not_collected)).toContain('get_storage_metrics');
    expect(memoryUnmeasuredReason([])).toBe(MEMORY_UNMEASURED_FALLBACK);
    expect(memoryUnmeasuredReason(undefined)).toBe(MEMORY_UNMEASURED_FALLBACK);
  });

  it('耗时只有后端起止都在才算，缺一头就是破折号', () => {
    expect(formatRunDuration('2026-10-16T02:00:00Z', '2026-10-16T02:03:06Z')).toBe('3 分 6 秒');
    expect(formatRunDuration('2026-10-16T02:00:00Z', null)).toBe(NO_SOURCE_TEXT);
    expect(formatRunDuration(null, null)).toBe(NO_SOURCE_TEXT);
  });
});

describe('状态色只认图上三态', () => {
  it('执行成功绿 / 执行失败红 / 运行中蓝；三态之外灰底原样透出', () => {
    expect(runRecordStatusMeta('succeeded')).toEqual({ color: 'green', label: '执行成功' });
    expect(runRecordStatusMeta('failed')).toEqual({ color: 'red', label: '执行失败' });
    expect(runRecordStatusMeta('running')).toEqual({ color: 'blue', label: '运行中' });
    // 这些都不许被静默归成三态（尤其不许归成执行成功）
    expect(runRecordStatusMeta('pending').color).toBe('default');
    expect(runRecordStatusMeta('pending').label).toBe('排队中');
    expect(runRecordStatusMeta('cancelled').color).toBe('default');
    expect(runRecordStatusMeta('skipped_overlap').color).toBe('default');
    expect(runRecordStatusMeta('whatever_the_backend_adds_next').label).toBe('whatever_the_backend_adds_next');
    expect(runRecordStatusMeta(null).label).toBe('未记录');
  });
});

describe('关联数据的两列读法', () => {
  it('产品类型给人读名，表里没有的取值原样透出', () => {
    expect(productTypeLabel('structured')).toBe('结构化数据');
    expect(productTypeLabel('semi-structured')).toBe('半结构化数据');
    expect(productTypeLabel('timeseries')).toBe('timeseries');
    expect(productTypeLabel(null)).toBe(NO_SOURCE_TEXT);
  });

  it('所属节点取登录主体的组织；档案里没有组织就置破折号', () => {
    expect(taskNodeText('沈阳数据中心R')).toBe('沈阳数据中心R');
    expect(taskNodeText('  ')).toBe(NO_SOURCE_TEXT);
    expect(taskNodeText(null)).toBe(NO_SOURCE_TEXT);
  });
});
