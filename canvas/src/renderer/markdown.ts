/**
 * Minimal, safe Markdown for chat text. Every byte of input is HTML-escaped
 * before any markup is added, and the only tags produced are p, br, pre,
 * code, strong, em, ul, ol, li, h3, h4, span.link and, for tables, div.table,
 * table, thead, tbody, tr, th and td (alignment as a class, never a style).
 * Links are shown as text (label and URL), never as anchors: there is no
 * browser.
 */

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Placeholder delimiters cannot appear in the input (stripped first).
const PH_OPEN = "\u0000";
const PH_CLOSE = "\u0001";

function renderInline(text: string): string {
  const codes: string[] = [];
  // 1. Pull out code spans so nothing inside them is formatted.
  let s = text.replace(/`([^`\n]+)`/g, (_m, code: string) => {
    codes.push(`<code>${escapeHtml(code)}</code>`);
    return `${PH_OPEN}${codes.length - 1}${PH_CLOSE}`;
  });
  // 2. Escape everything else.
  s = escapeHtml(s);
  // 3. Links as text: [label](url) -> label (url)
  s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_m, label: string, url: string) =>
    label === url ? `<span class="link">${url}</span>` : `${label} <span class="link">(${url})</span>`,
  );
  // 4. Emphasis.
  s = s.replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/__([^_\n]+?)__/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^_\w])_([^_\s][^_\n]*?)_(?!\w)/g, "$1<em>$2</em>");
  // 5. Restore code spans.
  s = s.replace(new RegExp(`${PH_OPEN}(\\d+)${PH_CLOSE}`, "g"), (_m, i: string) => codes[Number(i)] ?? "");
  return s;
}

function renderLines(lines: string[]): string {
  return lines.map(renderInline).join("<br>");
}

// A table: a header row, then a separator row of dashes (`|---|:---:|`), as GitHub writes them.
const SEPARATOR_CELL = /^\s*(:?)-+(:?)\s*$/;

/** Splits a table row on `|`, keeping `\|` and pipes inside code spans in the cell. */
function splitRow(line: string): string[] {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  const cells: string[] = [];
  let cell = "";
  let inCode = false;
  for (let i = 0; i < row.length; i++) {
    const c = row[i]!;
    if (c === "\\" && row[i + 1] === "|") {
      cell += "|";
      i++;
    } else if (c === "`") {
      inCode = !inCode;
      cell += c;
    } else if (c === "|" && !inCode) {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += c;
    }
  }
  cells.push(cell.trim());
  return cells;
}

/** The column alignments when lines[i] and lines[i + 1] start a table, else null. */
function tableStart(lines: string[], i: number): Array<"" | "left" | "center" | "right"> | null {
  const head = lines[i];
  const sep = lines[i + 1];
  if (head === undefined || sep === undefined || !head.includes("|") || !sep.includes("-")) return null;
  const marks = splitRow(sep);
  if (!marks.every((m) => SEPARATOR_CELL.test(m))) return null;
  if (splitRow(head).length !== marks.length || (marks.length < 2 && !sep.includes("|"))) return null;
  return marks.map((m) => {
    const [, l, r] = SEPARATOR_CELL.exec(m)!;
    return l && r ? "center" : r ? "right" : l ? "left" : "";
  });
}

function renderTable(lines: string[], align: Array<"" | "left" | "center" | "right">): string {
  const cell = (tag: "th" | "td", text: string, col: number) => {
    const a = align[col];
    return `<${tag}${a ? ` class="al-${a}"` : ""}>${renderInline(text)}</${tag}>`;
  };
  // Every row gets the header's number of cells: missing ones are empty, extra ones are dropped.
  const row = (line: string, tag: "th" | "td") =>
    `<tr>${align.map((_, col) => cell(tag, splitRow(line)[col] ?? "", col)).join("")}</tr>`;
  const body = lines.slice(2).map((l) => row(l, "td")).join("");
  return `<div class="table"><table><thead>${row(lines[0]!, "th")}</thead>${body ? `<tbody>${body}</tbody>` : ""}</table></div>`;
}

function renderTextBlock(block: string): string {
  const lines = block.split("\n");
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    const align = tableStart(lines, i);
    if (align) {
      const rows = [lines[i]!, lines[i + 1]!];
      i += 2;
      while (i < lines.length && lines[i]!.includes("|")) rows.push(lines[i++]!);
      out.push(renderTable(rows, align));
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const tag = heading[1]!.length <= 2 ? "h3" : "h4";
      out.push(`<${tag}>${renderInline(heading[2]!)}</${tag}>`);
      i++;
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i]!)) {
        items.push(`<li>${renderInline(lines[i]!.replace(/^\s*[-*+]\s+/, ""))}</li>`);
        i++;
      }
      out.push(`<ul>${items.join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i]!)) {
        items.push(`<li>${renderInline(lines[i]!.replace(/^\s*\d+[.)]\s+/, ""))}</li>`);
        i++;
      }
      out.push(`<ol>${items.join("")}</ol>`);
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      !/^(#{1,6})\s+/.test(lines[i]!) &&
      !/^\s*[-*+]\s+/.test(lines[i]!) &&
      !/^\s*\d+[.)]\s+/.test(lines[i]!) &&
      !(para.length && tableStart(lines, i))
    ) {
      para.push(lines[i]!);
      i++;
    }
    out.push(`<p>${renderLines(para)}</p>`);
  }
  return out.join("");
}

export function renderMarkdown(input: string): string {
  const text = String(input ?? "")
    .replace(/[\u0000\u0001]/g, "")
    .replace(/\r\n?/g, "\n");
  const out: string[] = [];
  const lines = text.split("\n");
  let i = 0;
  let buf: string[] = [];

  const flush = () => {
    const joined = buf.join("\n");
    buf = [];
    for (const block of joined.split(/\n\s*\n/)) {
      if (block.trim()) out.push(renderTextBlock(block.replace(/^\n+|\n+$/g, "")));
    }
  };

  while (i < lines.length) {
    const fence = /^\s*(```|~~~)\s*([\w+-]*)\s*$/.exec(lines[i]!);
    if (!fence) {
      buf.push(lines[i]!);
      i++;
      continue;
    }
    flush();
    const marker = fence[1]!;
    const lang = fence[2] ?? "";
    const code: string[] = [];
    i++;
    while (i < lines.length && !new RegExp(`^\\s*${marker}\\s*$`).test(lines[i]!)) {
      code.push(lines[i]!);
      i++;
    }
    i++; // closing fence (or end of a still-streaming block)
    const cls = lang ? ` data-lang="${escapeHtml(lang)}"` : "";
    out.push(`<pre${cls}><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  }
  flush();
  return out.join("");
}
