import { describe, expect, it } from 'vitest';
import { parseCsvPreview, splitCsvLine, splitCsvRecords } from '../csvPreview';

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

/**
 * RFC4180 切分（本轮修的真缺陷）：旧实现先 `split('\n')` 再进引号状态机，
 * 于是引号字段里的换行被当成行分隔（一行裂成两行），而“空行过滤”会把字段**内部**的
 * 空行删掉；同时表头重名时 `row[col] = …` 后一列静默覆盖前一列。
 * 下面每条都对着这两个坑写。
 */
describe('parseCsvPreview 带引号字段（RFC4180）', () => {
  it('引号里的换行属于字段内容，不拆成两行', () => {
    const r = parseCsvPreview('name,note\n"a\nb",1\n"c",2\n');
    expect(r.columns).toEqual(['name', 'note']);
    expect(r.totalRows).toBe(2);
    expect(r.rows).toEqual([
      { name: 'a\nb', note: '1' },
      { name: 'c', note: '2' },
    ]);
  });

  it('引号里的空行不被“空行过滤”吃掉', () => {
    const r = parseCsvPreview('a,b\n"line1\n\nline2",x\n');
    expect(r.totalRows).toBe(1);
    expect(r.rows).toEqual([{ a: 'line1\n\nline2', b: 'x' }]);
  });

  it('引号里的逗号不切列（整行级别）', () => {
    const r = parseCsvPreview('a,b,c\n"x,y",z,"p""q"');
    expect(r.rows).toEqual([{ a: 'x,y', b: 'z', c: 'p"q' }]);
    expect(r.totalRows).toBe(1);
  });

  it('表头重名时给出可区分的键，后一列不覆盖前一列', () => {
    const r = parseCsvPreview('id,name,name\n1,a,b');
    expect(r.columns).toEqual(['id', 'name', 'name#2']);
    expect(r.rows).toEqual([{ id: '1', name: 'a', 'name#2': 'b' }]);

    const triple = parseCsvPreview('x,x,x\n1,2,3');
    expect(triple.columns).toEqual(['x', 'x#2', 'x#3']);
    expect(triple.rows[0]).toEqual({ x: '1', 'x#2': '2', 'x#3': '3' });

    // 原本就叫 `v#2` 的列保留自己的字面名（不改名），重名的那份往后挤到没占用的后缀。
    // 列的**顺序**仍按文件里的列序，只是重名那列的标签拿的是空出来的后缀。
    const clash = parseCsvPreview('v,v,v#2\n1,2,3');
    expect(clash.columns).toEqual(['v', 'v#3', 'v#2']);
    expect(clash.rows[0]).toEqual({ v: '1', 'v#3': '2', 'v#2': '3' });
  });

  it('CRLF 行尾与尾随行尾不产生空行', () => {
    const r = parseCsvPreview('a,b\r\n1,2\r\n3,4\r\n');
    expect(r.rows).toEqual([
      { a: '1', b: '2' },
      { a: '3', b: '4' },
    ]);
    expect(r.totalRows).toBe(2);
    expect(r.truncated).toBe(false);
  });

  it('CRLF 在引号内归一为 \n（行尾归一不按引号内外区分）', () => {
    const r = parseCsvPreview('a,b\r\n"p\r\nq",z');
    expect(r.rows).toEqual([{ a: 'p\nq', b: 'z' }]);
  });

  it('行间的真空行仍然过滤（不属于字段内部时）', () => {
    const r = parseCsvPreview('a,b\n\n1,2\n\n');
    expect(r.totalRows).toBe(1);
    expect(r.rows).toEqual([{ a: '1', b: '2' }]);
  });

  it('引号未闭合时按字面透出到行尾，不报异常（诚实降级口径）', () => {
    const r = parseCsvPreview('a,b\n"unclosed,1\n2,3');
    expect(r.totalRows).toBe(1);
    expect(r.rows[0].a).toBe('unclosed,1\n2,3');
  });

  it('splitCsvRecords 把整段文本切成记录，引号内的换行/逗号不算分隔', () => {
    expect(splitCsvRecords('a,b\n"x\n,y",z')).toEqual([
      ['a', 'b'],
      ['x\n,y', 'z'],
    ]);
    // 尾随换行不留下空记录
    expect(splitCsvRecords('a\n')).toEqual([['a']]);
  });
});
