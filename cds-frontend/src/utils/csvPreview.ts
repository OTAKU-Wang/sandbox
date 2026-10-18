/**
 * 共用的 CSV 预览模型：把后端返回的 CSV 文本转成可渲染的表格数据。
 *
 * 两个调用方共用（它们都只能拿到 CSV 文本，形状不一致）：
 * - `POST /api/v1/data-resources/{id}/desensitize` → `{ data, row_count, masking_applied }`，
 *   `data` 是**已做 PII 掩码的样例**，不是原始数据。
 * - `GET /api/v1/deliveries/dataset/{id}/preview` → `{ data, rows_returned, row_count_total }`，
 *   `data` 是已过出口审查的成果前 N 行。
 *
 * 这里只负责解析与截断，不做任何“补全/推断”，解析不出来的就原样透出。
 *
 * 解析口径（2026-10-18 本轮修的缺陷）：
 * - 旧实现先 `split('\n')` 再跑引号状态机，于是**引号字段内部的换行**被当成行分隔符：
 *   一行裂成两行，且“空行过滤”会把字段内部的空行删掉。现在整段扫一遍（`splitCsvRecords`），
 *   引号状态机优先于换行，符合 RFC4180 §2.6（字段可以包含 CRLF）。
 * - 行尾统一归一成 `\n`，**引号内也一样**（预览用途下不做“原文字节级还原”的承诺）。
 * - 表头重名时旧实现 `row[col] = …` 后一列静默覆盖前一列；现在后出现的列键加 `#2/#3…` 后缀。
 * - 引号未闭合不报错，按 RFC 的宽松读法一路吞到末尾原样透出（诚实降级，同上面的“不推断”口径）。
 */

export interface CsvPreview {
  columns: string[];
  rows: Record<string, string>[];
  totalRows: number;
  truncated: boolean;
  reason: string | null;
}

const EMPTY: CsvPreview = {
  columns: [],
  rows: [],
  totalRows: 0,
  truncated: false,
  reason: '没有可预览的内容',
};

/** 多于表头的单元格收进这个键，避免“看起来少了字段”。 */
const EXTRA_KEY = '_extra';

/**
 * RFC4180 记录切分：一趟扫完整段文本，所以引号里的换行/逗号是字段内容而不是分隔符。
 * 返回每条记录的字段数组；尾随换行不会多造一条空记录（真正的空行留给调用方按需过滤）。
 */
export function splitCsvRecords(text: string): string[][] {
  const src = text.replace(/\r\n?/g, '\n');
  if (!src) return [];

  const records: string[][] = [];
  let record: string[] = [];
  let field = '';
  let quoted = false;

  for (let i = 0; i < src.length; i += 1) {
    const ch = src[i];
    if (quoted) {
      if (ch !== '"') {
        field += ch;
      } else if (src[i + 1] === '"') {
        field += '"';
        i += 1;
      } else {
        quoted = false;
      }
      continue;
    }
    if (ch === '"') {
      quoted = true;
    } else if (ch === ',') {
      record.push(field);
      field = '';
    } else if (ch === '\n') {
      record.push(field);
      records.push(record);
      record = [];
      field = '';
    } else {
      field += ch;
    }
  }

  // 文本以换行结尾时，上面已经把最后一条记录收尾了，别再补一条空记录。
  if (record.length > 0 || field !== '') {
    record.push(field);
    records.push(record);
  }
  return records;
}

/** 按逗号切分一行 CSV，支持双引号包裹与 `""` 转义（跨行字段请用 `splitCsvRecords`）。 */
export function splitCsvLine(line: string): string[] {
  return splitCsvRecords(line)[0] ?? [''];
}

/**
 * 表头去重：重名的后续列加 `#2/#3…`，行对象里就不会互相覆盖。
 * 两遍扫：先原样保留不冲突的字面列名（本来就叫 `v#2` 的列不会被改名），
 * 再把重名的那份递增到没占用的后缀。
 */
function dedupeHeaders(headers: string[]): string[] {
  const taken = new Set<string>();
  const literal = headers.map((raw) => {
    if (taken.has(raw)) return null;
    taken.add(raw);
    return raw;
  });
  return literal.map((name, idx) => {
    if (name !== null) return name;
    let n = 2;
    while (taken.has(`${headers[idx]}#${n}`)) n += 1;
    const key = `${headers[idx]}#${n}`;
    taken.add(key);
    return key;
  });
}

export function parseCsvPreview(text: string | null | undefined, maxRows = 50): CsvPreview {
  if (!(text ?? '').trim()) return EMPTY;

  // 只丢“真正的空行”（只有一个空字段的记录）；引号字段内部的空行在这一步已经是字段内容了。
  const records = splitCsvRecords(text as string).filter((rec) => !(rec.length === 1 && rec[0].trim() === ''));
  if (records.length === 0) return { ...EMPTY, reason: '没有可预览的内容' };

  const columns = dedupeHeaders(records[0]);
  if (records.length === 1) {
    return { ...EMPTY, reason: '只有表头，没有数据行', columns };
  }

  const body = records.slice(1);
  const rows = body.slice(0, maxRows).map((cells) => {
    const row: Record<string, string> = {};
    columns.forEach((col, idx) => {
      row[col] = cells[idx] ?? '';
    });
    // 列数多于表头时多出的单元格不丢弃，原样透出，避免"看起来少了字段"
    if (cells.length > columns.length) {
      row[EXTRA_KEY] = cells.slice(columns.length).join(',');
    }
    return row;
  });

  const extraColumns = rows.some((r) => EXTRA_KEY in r) ? [EXTRA_KEY] : [];
  return {
    columns: [...columns, ...extraColumns],
    rows,
    totalRows: body.length,
    truncated: body.length > maxRows,
    reason: null,
  };
}
