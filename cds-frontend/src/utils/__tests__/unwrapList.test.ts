import { describe, it, expect } from 'vitest';
import { unwrapList, ListShapeError } from '../unwrapList';

describe('unwrapList — 规范形态优先', () => {
  it('裸数组（/certificates/list、/federation/trusts 的规范形态）原样返回', () => {
    const payload = [{ id: 1 }, { id: 2 }];
    expect(unwrapList(payload, '/certificates/list', 'array')).toBe(payload);
    expect(unwrapList([], '/certificates/list', 'array')).toEqual([]);
  });

  it('{items}（/kms/keys、/kms/audit 的规范形态）', () => {
    expect(unwrapList({ items: [1, 2] }, '/kms/keys', 'items')).toEqual([1, 2]);
    expect(unwrapList({ items: [] }, '/kms/audit', 'items')).toEqual([]);
  });

  it('canonical 只决定尝试顺序，不缩小接受范围：两种 canonical 对同载荷结果一致', () => {
    for (const payload of [[1], { items: [1] }, { data: [1] }, { results: [1] }]) {
      expect(unwrapList(payload, '/x', 'array')).toEqual([1]);
      expect(unwrapList(payload, '/x', 'items')).toEqual([1]);
    }
  });
});

describe('unwrapList — 兼容回退形态', () => {
  it('{total, items}', () => {
    expect(unwrapList({ total: 120, items: ['a'] }, '/x')).toEqual(['a']);
  });

  it('{data}', () => {
    expect(unwrapList({ data: ['a'] }, '/x')).toEqual(['a']);
  });

  it('{results}', () => {
    expect(unwrapList({ results: ['a'], count: 1 }, '/x')).toEqual(['a']);
  });

  it('包装键同时存在时按 canonical 优先取，不抛错', () => {
    expect(unwrapList({ items: ['i'], data: ['d'] }, '/x', 'array')).toEqual(['i']);
    expect(unwrapList({ items: ['i'], data: ['d'] }, '/x', 'data')).toEqual(['d']);
  });
});

describe('unwrapList — 认不出就抛，绝不静默返回空数组', () => {
  const garbage: Array<[string, unknown]> = [
    ['null', null],
    ['undefined', undefined],
    ['空对象', {}],
    ['未知包装键', { entries: [1, 2] }],
    ['items 不是数组（字符串）', { items: 'nope' }],
    ['items 为 null', { items: null }],
    ['items 为对象', { items: { 0: 'a' } }],
    ['只有 total', { total: 0 }],
    ['字符串', '[]'],
    ['数字', 0],
    ['布尔', true],
  ];

  it.each(garbage)('%s → ListShapeError', (_label, payload) => {
    expect(() => unwrapList(payload, '/kms/audit', 'items')).toThrow(ListShapeError);
  });

  it('错误信息带端点与收到的形态，payload 挂在错误上便于上层记录', () => {
    const payload = { entries: [] };
    let caught: unknown;
    try {
      unwrapList(payload, '/kms/audit', 'items');
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(ListShapeError);
    const err = caught as ListShapeError;
    expect(err.name).toBe('ListShapeError');
    expect(err.endpoint).toBe('/kms/audit');
    expect(err.payload).toBe(payload);
    expect(err.message).toContain('/kms/audit');
    expect(err.message).toContain('object{entries}');
  });
});
