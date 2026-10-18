// SS-18 M2′「新增开发任务」的表单口径。范围与「执行内存随调试内存提交」必须与后端
// app/api/notebooks.py::_resolve_task_memory 同口径；错误文案只复述后端下发的字段（禁伪造）。
import { describe, it, expect } from 'vitest';
import {
  TASK_DEBUG_METHOD_OPTIONS,
  TASK_EXPORT_MODE_OPTIONS,
  TASK_MEMORY_LIMITS,
  UNKNOWN_TEXT,
  buildDevTaskPayload,
  describeNotebookCreateIssues,
  formatMemoryGib,
  gibToMib,
  mibToGib,
  taskEnumLabel,
  validateDevTaskForm,
} from '../../../services/flow';
import type { DevTaskFormValues } from '../../../services/flow';

const base: DevTaskFormValues = { title: '客单价分析', contract_id: 'c-1' };
const fields = (values: DevTaskFormValues) => validateDevTaskForm(values).map((issue) => issue.field);

describe('开发任务表单的范围校验', () => {
  it('档位与后端一致：调试 2–32 GiB、执行 2–64 GiB', () => {
    expect(TASK_MEMORY_LIMITS.debug).toMatchObject({ min_gib: 2, max_gib: 32 });
    expect(TASK_MEMORY_LIMITS.exec).toMatchObject({ min_gib: 2, max_gib: 64 });
    expect(gibToMib(8)).toBe(8192);
    expect(mibToGib(16384)).toBe(16);
  });

  it('调试内存越界只报在调试内存这一格', () => {
    expect(fields({ ...base, debug_memory_gib: 1 })).toEqual(['debug_memory_gib']);
    expect(fields({ ...base, debug_memory_gib: 33 })).toEqual(['debug_memory_gib']);
    expect(validateDevTaskForm({ ...base, debug_memory_gib: 8 })).toEqual([]);
  });

  it('执行内存越界与低于调试内存都报在执行内存', () => {
    const tooBig = validateDevTaskForm({ ...base, debug_memory_gib: 8, separate_exec_memory: true, exec_memory_gib: 65 });
    expect(tooBig).toHaveLength(1);
    expect(tooBig[0].field).toBe('exec_memory_gib');
    expect(tooBig[0].text).toContain('2–64 GiB');

    const tooSmall = validateDevTaskForm({ ...base, debug_memory_gib: 8, separate_exec_memory: true, exec_memory_gib: 4 });
    expect(tooSmall[0].text).toContain('不得低于调试内存');
  });

  it('执行内存必须随调试内存一起提交（后端同一条铁律）', () => {
    const issues = validateDevTaskForm({ ...base, separate_exec_memory: true, exec_memory_gib: 16 });
    expect(fields({ ...base, separate_exec_memory: true, exec_memory_gib: 16 })).toContain('debug_memory_gib');
    expect(issues[0].text).toContain('一起提交');
  });

  it('内存留空不算错：由服务端沿用合约已批准规格', () => {
    expect(validateDevTaskForm(base)).toEqual([]);
  });

  it('缺标题或缺合约各报一次', () => {
    expect(fields({ ...base, title: '  ' })).toEqual(['title']);
    expect(fields({ ...base, contract_id: null })).toEqual(['contract_id']);
  });
});

describe('建任务的单个 POST 载荷', () => {
  it('GiB→MB、路径留空取 /nb/<slug>.ipynb', () => {
    expect(buildDevTaskPayload({ ...base, debug_memory_gib: 8 })).toEqual({
      title: '客单价分析',
      path: '/nb/客单价分析.ipynb',
      contract_id: 'c-1',
      debug_method: undefined,
      export_mode: undefined,
      debug_memory_mb: 8192,
    });
  });

  it('执行内存只在开关打开且填了调试内存时提交', () => {
    const off = buildDevTaskPayload({ ...base, debug_memory_gib: 8, separate_exec_memory: false, exec_memory_gib: 16 });
    expect(off.exec_memory_mb).toBeUndefined();
    const noDebug = buildDevTaskPayload({ ...base, separate_exec_memory: true, exec_memory_gib: 16 });
    expect(noDebug.exec_memory_mb).toBeUndefined();
    const on = buildDevTaskPayload({ ...base, debug_memory_gib: 8, separate_exec_memory: true, exec_memory_gib: 16 });
    expect(on.exec_memory_mb).toBe(16384);
  });

  it('任务编号不进载荷：编号由服务端生成，客户端不送也不改', () => {
    const smuggled = { ...base, task_no: 'TASK-20200101-001', debug_memory_gib: 8 } as unknown as DevTaskFormValues;
    const payload = buildDevTaskPayload(smuggled);
    expect(payload).not.toHaveProperty('task_no');
    expect(Object.keys(payload).sort()).toEqual(['contract_id', 'debug_memory_mb', 'path', 'title']);
  });
});

describe('建任务失败 → 可读文案', () => {
  it('RESOURCE_OVERRIDE_EXCEEDED 说清合约已批准规格并归位到字段', () => {
    const error = {
      response: {
        status: 400,
        data: {
          code: 'RESOURCE_OVERRIDE_EXCEEDED',
          message: 'debug_memory_mb=32768 超出合约已批准的规格上限 16384',
          detail: { contract_memory_mb: 16384, effective_ceiling_mb: 16384 },
          request_id: 'req-1',
        },
      },
    };
    const issues = describeNotebookCreateIssues(error);
    expect(issues).toHaveLength(1);
    expect(issues[0].field).toBe('debug_memory_mb');
    expect(issues[0].text).toContain('超出合约已批准规格（合约内存 16 GiB）');
    expect(issues[0].text).toContain('上限 16 GiB');
  });

  it('合约未下发规格时不编一个数字出来', () => {
    const issues = describeNotebookCreateIssues({
      response: { status: 400, data: { code: 'RESOURCE_OVERRIDE_EXCEEDED', message: 'exec_memory_mb=65536 超出规格', detail: {} } },
    });
    expect(issues[0].field).toBe('exec_memory_mb');
    expect(issues[0].text).toContain('合约内存未下发');
  });

  it('VALIDATION_ERROR 逐条取 detail.errors 的字段级 msg', () => {
    const issues = describeNotebookCreateIssues({
      response: {
        status: 422,
        data: {
          code: 'VALIDATION_ERROR',
          message: '请求参数校验失败',
          detail: {
            errors: [
              { loc: ['body', 'debug_memory_mb'], msg: '调试内存不得低于 2 GiB', type: 'value_error' },
              { loc: ['body', 'exec_memory_mb'], msg: '执行内存必须与调试内存一起提交', type: 'value_error' },
            ],
          },
        },
      },
    });
    expect(issues.map((issue) => issue.field)).toEqual(['debug_memory_mb', 'exec_memory_mb']);
    expect(issues[0].text).toBe('调试内存：调试内存不得低于 2 GiB');
    expect(issues[1].text).toContain('一起提交');
  });

  it('合约 404 归位到合约字段，不假装是内存问题', () => {
    const issues = describeNotebookCreateIssues({ response: { status: 404, data: { detail: 'contract not found' } } });
    expect(issues[0].field).toBe('contract_id');
    expect(issues[0].text).toContain('404');
  });

  it('其余错误回落 describeFlowError，不裸抛 code', () => {
    const issues = describeNotebookCreateIssues({ response: { status: 500, data: { code: 'INTERNAL', message: '后端炸了', detail: null } } });
    expect(issues).toEqual([{ field: null, text: '后端炸了（INTERNAL）' }]);
  });
});

describe('未下发字段的诚实展示', () => {
  it('内存缺失 = 未记录，不当成 0', () => {
    expect(formatMemoryGib(null)).toBe(UNKNOWN_TEXT);
    expect(formatMemoryGib(undefined)).toBe(UNKNOWN_TEXT);
    expect(formatMemoryGib(8192)).toBe('8 GiB');
    expect(formatMemoryGib(2560)).toBe('2.5 GiB');
  });

  it('任务枚举：未登记取值原样透出，不编中文标签', () => {
    expect(taskEnumLabel(TASK_DEBUG_METHOD_OPTIONS, 'web_ide')).toBe('Web IDE 在线调试');
    expect(taskEnumLabel(TASK_DEBUG_METHOD_OPTIONS, null)).toBe(UNKNOWN_TEXT);
    expect(taskEnumLabel(TASK_EXPORT_MODE_OPTIONS, 'brand_new_mode')).toBe('brand_new_mode');
    expect(TASK_DEBUG_METHOD_OPTIONS.map((option) => option.value)).toEqual(['web_ide', 'local_plugin', 'jupyter']);
    expect(TASK_EXPORT_MODE_OPTIONS.map((option) => option.value)).toEqual(['bundle', 'openapi']);
  });
});
