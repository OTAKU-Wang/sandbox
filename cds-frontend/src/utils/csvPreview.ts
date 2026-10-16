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

/** 按逗号切分一行 CSV，支持双引号包裹与 `""` 转义。 */
export function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        cur += ch;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ',') {
      out.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

export function parseCsvPreview(text: string | null | undefined, maxRows = 50): CsvPreview {
  const trimmed = (text ?? '').replace(/\r\n/g, '\n').trim();
  if (!trimmed) return EMPTY;

  const lines = trimmed.split('\n').filter((l) => l.trim() !== '');
  if (lines.length === 0) return { ...EMPTY, reason: '没有可预览的内容' };
  if (lines.length === 1) {
    return { ...EMPTY, reason: '只有表头，没有数据行', columns: splitCsvLine(lines[0]) };
  }

  const columns = splitCsvLine(lines[0]);
  const body = lines.slice(1);
  const rows = body.slice(0, maxRows).map((line) => {
    const cells = splitCsvLine(line);
    const row: Record<string, string> = {};
    columns.forEach((col, idx) => {
      row[col] = cells[idx] ?? '';
    });
    // 列数多于表头时多出的单元格不丢弃，原样透出，避免"看起来少了字段"
    if (cells.length > columns.length) {
      row['_extra'] = cells.slice(columns.length).join(',');
    }
    return row;
  });

  const extraColumns = rows.some((r) => '_extra' in r) ? ['_extra'] : [];
  return {
    columns: [...columns, ...extraColumns],
    rows,
    totalRows: body.length,
    truncated: body.length > maxRows,
    reason: null,
  };
}
