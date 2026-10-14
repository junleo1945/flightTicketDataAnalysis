const SEPARATOR_CELL = /^\s*:?-+:?\s*$/;

// 模型偶尔写错表格分隔行的列数（如表头 6 列、分隔行 7 列），GFM 会因此整张表不渲染。
// 这里按表头列数重写分隔行，保留原有的对齐设置，多出的列丢弃，缺的列补 ---。
export function normalizeTables(md: string): string {
  const lines = md.split("\n");
  for (let i = 0; i + 1 < lines.length; i++) {
    const header = lines[i].trim();
    const sep = lines[i + 1].trim();
    if (!header.startsWith("|") || !sep.startsWith("|") || !/^\|[\s:|-]+\|?$/.test(sep) || !sep.includes("-")) continue;
    const cols = header.replace(/^\||\|$/g, "").split("|").length;
    const cells = sep.replace(/^\||\|$/g, "").split("|");
    if (cells.length === cols) continue;
    const fixed = Array.from({ length: cols }, (_, j) => (SEPARATOR_CELL.test(cells[j] ?? "") ? cells[j].trim() : "---"));
    lines[i + 1] = `|${fixed.join("|")}|`;
  }
  return lines.join("\n");
}
