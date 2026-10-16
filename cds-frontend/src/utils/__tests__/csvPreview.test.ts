import { describe, expect, it } from 'vitest';
import { parseCsvPreview, splitCsvLine } from '../csvPreview';

describe('parseCsvPreview', () => {
  it('把表头与数据行映射成对象数组', () => {
    const r = parseCsvPreview('name,phone\n张三,138****0001\n李四,139****0002\n');
    expect(r.columns).toEqual(['name', 'phone']);
    expect(r.rows).toEqual([
      { name: '张三', phone: '138****0001' },
      { name: '李四', phone: '139****0002' },
    ]);
    expect(r.totalRows).toBe(2);
    expect(r.truncated).toBe(false);
    expect(r.reason).toBeNull();
  });

  it('带引号的字段保留内部逗号，"" 还原为引号', () => {
    expect(splitCsvLine('a,"b,c","say ""hi""",d')).toEqual(['a', 'b,c', 'say "hi"', 'd']);
    const r = parseCsvPreview('a,b\n"1,2","x""y"');
    expect(r.rows).toEqual([{ a: '1,2', b: 'x"y' }]);
  });

  it('超过 maxRows 时截断展示但如实报告总行数', () => {
    const text = ['id', '1', '2', '3', '4', '5'].join('\n');
    const r = parseCsvPreview(text, 2);
    expect(r.rows).toHaveLength(2);
    expect(r.totalRows).toBe(5);
    expect(r.truncated).toBe(true);
  });

  it('空内容给出可读原因而不是空表格假装成功', () => {
    for (const empty of [undefined, null, '', '   ']) {
      const r = parseCsvPreview(empty);
      expect(r.columns).toEqual([]);
      expect(r.rows).toEqual([]);
      expect(r.reason).toBeTruthy();
    }
  });

  it('只有表头时说明没有数据行，并仍然透出列名', () => {
    const r = parseCsvPreview('name,phone');
    expect(r.columns).toEqual(['name', 'phone']);
    expect(r.rows).toEqual([]);
    expect(r.reason).toContain('没有数据行');
  });

  it('列数多于表头时多出的值不丢弃', () => {
    const r = parseCsvPreview('a,b\n1,2,3');
    expect(r.columns).toEqual(['a', 'b', '_extra']);
    expect(r.rows[0]._extra).toBe('3');
  });
});
