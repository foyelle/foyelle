import { describe, expect, it } from "vitest";
import { escapeHtml, renderMarkdown } from "../src/renderer/markdown";

const ALLOWED_TAGS = new Set([
  "p", "br", "pre", "code", "strong", "em", "ul", "ol", "li", "h3", "h4", "span",
  "div", "table", "thead", "tbody", "tr", "th", "td",
]);

function tags(html: string): string[] {
  return [...html.matchAll(/<\/?([a-zA-Z0-9]+)/g)].map((m) => m[1]!.toLowerCase());
}

describe("escapeHtml", () => {
  it("escapes the five HTML metacharacters", () => {
    expect(escapeHtml(`<a href="x">'&'</a>`)).toBe("&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;");
  });
});

describe("renderMarkdown", () => {
  it("renders paragraphs and line breaks", () => {
    expect(renderMarkdown("one\ntwo\n\nthree")).toBe("<p>one<br>two</p><p>three</p>");
  });

  it("renders bold, italic and inline code", () => {
    expect(renderMarkdown("**b** and *i* and _j_ and `c`")).toBe("<p><strong>b</strong> and <em>i</em> and <em>j</em> and <code>c</code></p>");
  });

  it("does not format inside inline code", () => {
    expect(renderMarkdown("`**not bold** <b>`")).toBe("<p><code>**not bold** &lt;b&gt;</code></p>");
  });

  it("renders fenced code blocks verbatim and escaped", () => {
    const html = renderMarkdown("before\n```js\nif (a < b) { **x** }\n```\nafter");
    expect(html).toBe('<p>before</p><pre data-lang="js"><code>if (a &lt; b) { **x** }</code></pre><p>after</p>');
  });

  it("renders an unclosed fence (still streaming) as code", () => {
    expect(renderMarkdown("```\nline")).toBe("<pre><code>line</code></pre>");
  });

  it("shows links as text, never as anchors", () => {
    const html = renderMarkdown("see [the spec](https://example.org/a?b=1&c=2)");
    expect(html).toBe('<p>see the spec <span class="link">(https://example.org/a?b=1&amp;c=2)</span></p>');
    expect(html).not.toContain("<a");
    expect(renderMarkdown("[x](javascript:alert(1))")).not.toContain("href");
  });

  it("renders lists and headings", () => {
    expect(renderMarkdown("# Title\n- a\n- b\n1. one\n2. two")).toBe("<h3>Title</h3><ul><li>a</li><li>b</li></ul><ol><li>one</li><li>two</li></ol>");
  });

  it("renders tables with a header row", () => {
    expect(renderMarkdown("| Day | Price |\n|---|---|\n| Mon | 313 |\n| Tue | 459 |")).toBe(
      '<div class="table"><table><thead><tr><th>Day</th><th>Price</th></tr></thead>' +
        "<tbody><tr><td>Mon</td><td>313</td></tr><tr><td>Tue</td><td>459</td></tr></tbody></table></div>",
    );
  });

  it("aligns columns with classes, never styles", () => {
    const html = renderMarkdown("a | b | c\n:-- | :-: | --:\n1 | 2 | 3");
    expect(html).toContain('<th class="al-left">a</th><th class="al-center">b</th><th class="al-right">c</th>');
    expect(html).toContain('<td class="al-right">3</td>');
    expect(html).not.toContain("style");
  });

  it("formats cells inline and keeps escaped pipes and pipes in code", () => {
    const html = renderMarkdown("| What | How |\n|---|---|\n| **Nonstop** | `a|b` and x \\| y |");
    expect(html).toContain("<td><strong>Nonstop</strong></td><td><code>a|b</code> and x | y</td>");
  });

  it("gives every row the header's columns", () => {
    const html = renderMarkdown("| a | b |\n|---|---|\n| 1 |\n| 1 | 2 | 3 |");
    expect(html).toContain("<tr><td>1</td><td></td></tr><tr><td>1</td><td>2</td></tr>");
  });

  it("ends a paragraph where a table starts and the table at a line without a pipe", () => {
    expect(renderMarkdown("Flights:\n| a | b |\n|---|---|\n| 1 | 2 |\nCheapest first.")).toBe(
      '<p>Flights:</p><div class="table"><table><thead><tr><th>a</th><th>b</th></tr></thead>' +
        "<tbody><tr><td>1</td><td>2</td></tr></tbody></table></div><p>Cheapest first.</p>",
    );
  });

  it("renders the agent's flight table", () => {
    const md = "| Route | Depart → Arrive (local) | Price |\n|---|---|---|\n" +
      "| HKG → IST nonstop (Turkish TK171) | 21 Dec 09:45 → 21 Dec 16:45 | 468 · book (https://kiwi.com/u/w5qz9bm) |";
    const html = renderMarkdown(md);
    expect(html.match(/<td>/g)).toHaveLength(3);
    expect(html).toContain("<td>HKG → IST nonstop (Turkish TK171)</td>");
  });

  it("leaves text that only looks like a table alone", () => {
    expect(renderMarkdown("a | b\nno separator")).toBe("<p>a | b<br>no separator</p>");
    expect(renderMarkdown("| a | b |\n|---|")).not.toContain("<table>"); // columns don't match
    expect(renderMarkdown("x\n---")).not.toContain("<table>");
  });

  it("escapes raw HTML everywhere", () => {
    const evil = [
      `<script>alert(1)</script>`,
      `<img src=x onerror=alert(1)>`,
      `**<iframe src="javascript:alert(1)">**`,
      `[<svg onload=alert(1)>](https://x)`,
      "- <b onclick=x>item</b>",
      "# <style>body{}</style>",
      "`<code>` and ```\n</pre><script>x</script>\n```",
      `"><img src=x>`,
      "\u0000 0\u0001 placeholder injection",
      "| <img src=x onerror=alert(1)> | b |\n|:-:|---|\n| <script>x</script> | `</td><td>` |",
      '| a" onmouseover="x | b |\n|---|---|\n| 1 | 2 |',
    ];
    for (const input of evil) {
      const html = renderMarkdown(input);
      for (const t of tags(html)) expect(ALLOWED_TAGS.has(t), `${t} in ${html}`).toBe(true);
      expect(html).not.toMatch(/<[^>]*\son\w+=/i);
      expect(html).not.toContain("<script");
    }
  });

  it("handles empty and non-string input", () => {
    expect(renderMarkdown("")).toBe("");
    expect(renderMarkdown(undefined as unknown as string)).toBe("");
  });
});
