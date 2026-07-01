const fs = require("fs");
const path = require("path");
const {
  AlignmentType,
  BorderStyle,
  Document,
  Footer,
  HeadingLevel,
  LevelFormat,
  Packer,
  PageNumber,
  Paragraph,
  ShadingType,
  Table,
  TableCell,
  TableOfContents,
  TableRow,
  TextRun,
  WidthType,
} = require("docx");

const inputPath = path.resolve("qwen_local_models_bench_report_20260701.md");
const outputPath = path.resolve("Qwen本地模型Benchloop评测报告_20260701.docx");
const markdown = fs.readFileSync(inputPath, "utf8").replace(/\r\n/g, "\n");

const contentWidth = 9360;
const border = { style: BorderStyle.SINGLE, size: 1, color: "D9E2EC" };
const borders = { top: border, bottom: border, left: border, right: border };

function cleanInline(text) {
  return text
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\\\|/g, "|")
    .trim();
}

function runsFromInline(text, options = {}) {
  const runs = [];
  const regex = /(\*\*([^*]+)\*\*|`([^`]+)`)/g;
  let last = 0;
  let match;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > last) {
      runs.push(new TextRun({ text: text.slice(last, match.index), ...options }));
    }
    if (match[2]) {
      runs.push(new TextRun({ text: match[2], bold: true, ...options }));
    } else if (match[3]) {
      runs.push(new TextRun({ text: match[3], font: "Consolas", color: "1F2937", ...options }));
    }
    last = regex.lastIndex;
  }
  if (last < text.length) {
    runs.push(new TextRun({ text: text.slice(last), ...options }));
  }
  return runs.length ? runs : [new TextRun({ text: "", ...options })];
}

function paragraph(text, opts = {}) {
  return new Paragraph({
    spacing: { before: opts.before ?? 80, after: opts.after ?? 100, line: 300 },
    alignment: opts.alignment,
    numbering: opts.numbering,
    children: runsFromInline(text, opts.run || {}),
  });
}

function heading(text, level) {
  const headingLevel =
    level === 1 ? HeadingLevel.HEADING_1 :
    level === 2 ? HeadingLevel.HEADING_2 :
    HeadingLevel.HEADING_3;
  return new Paragraph({
    heading: headingLevel,
    spacing: { before: level === 1 ? 360 : 240, after: 160 },
    children: [new TextRun(cleanInline(text))],
  });
}

function codeBlock(text) {
  const lines = text.replace(/\n$/, "").split("\n");
  return lines.map((line) =>
    new Paragraph({
      spacing: { before: 0, after: 0 },
      shading: { type: ShadingType.CLEAR, fill: "F3F4F6" },
      border: { left: { style: BorderStyle.SINGLE, size: 8, color: "94A3B8", space: 4 } },
      children: [new TextRun({ text: line || " ", font: "Consolas", size: 18, color: "111827" })],
    })
  );
}

function parseTable(lines, startIndex) {
  const rows = [];
  let i = startIndex;
  while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) {
    const raw = lines[i].trim();
    if (!/^\|[\s:|\-]+\|$/.test(raw)) {
      rows.push(raw.slice(1, -1).split("|").map((cell) => cleanInline(cell)));
    }
    i += 1;
  }
  return { rows, next: i };
}

function makeTable(rows) {
  if (!rows.length) return [];
  const colCount = Math.max(...rows.map((row) => row.length));
  const colWidth = Math.floor(contentWidth / colCount);
  const widths = Array(colCount).fill(colWidth);
  widths[widths.length - 1] += contentWidth - widths.reduce((a, b) => a + b, 0);

  return [new Table({
    width: { size: contentWidth, type: WidthType.DXA },
    columnWidths: widths,
    rows: rows.map((row, rowIndex) => new TableRow({
      children: widths.map((width, colIndex) => new TableCell({
        borders,
        width: { size: width, type: WidthType.DXA },
        shading: rowIndex === 0 ? { fill: "E8F1F8", type: ShadingType.CLEAR } : undefined,
        margins: { top: 90, bottom: 90, left: 120, right: 120 },
        children: [new Paragraph({
          spacing: { before: 0, after: 0 },
          children: [new TextRun({
            text: row[colIndex] || "",
            bold: rowIndex === 0,
            size: rowIndex === 0 ? 20 : 18,
          })],
        })],
      })),
    })),
  }), new Paragraph({ spacing: { before: 80, after: 120 }, children: [] })];
}

function parseMarkdown(md) {
  const lines = md.split("\n");
  const children = [
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 120, after: 320 },
      children: [new TextRun({
        text: "Qwen 本地模型 Benchloop 评测报告",
        bold: true,
        size: 40,
        color: "1F2937",
      })],
    }),
    new TableOfContents("目录", { hyperlink: true, headingStyleRange: "1-3" }),
    new Paragraph({ spacing: { before: 160, after: 240 }, children: [] }),
  ];

  let i = 0;
  while (i < lines.length) {
    let line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (line.startsWith("# ")) {
      i += 1;
      continue;
    }
    if (line.startsWith("```")) {
      const block = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith("```")) {
        block.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      children.push(...codeBlock(block.join("\n")));
      children.push(new Paragraph({ spacing: { before: 80, after: 120 }, children: [] }));
      continue;
    }
    if (/^#{2,4}\s+/.test(line)) {
      const match = line.match(/^(#{2,4})\s+(.*)$/);
      children.push(heading(match[2], Math.min(match[1].length - 1, 3)));
      i += 1;
      continue;
    }
    if (/^\|.*\|$/.test(line.trim()) && i + 1 < lines.length && /^\|[\s:|\-]+\|$/.test(lines[i + 1].trim())) {
      const parsed = parseTable(lines, i);
      children.push(...makeTable(parsed.rows));
      i = parsed.next;
      continue;
    }
    if (/^\d+\.\s+/.test(line.trim())) {
      children.push(paragraph(line.trim().replace(/^\d+\.\s+/, ""), {
        numbering: { reference: "numbers", level: 0 },
      }));
      i += 1;
      continue;
    }
    if (/^-\s+/.test(line.trim())) {
      children.push(paragraph(line.trim().replace(/^-\s+/, ""), {
        numbering: { reference: "bullets", level: 0 },
      }));
      i += 1;
      continue;
    }
    const paragraphLines = [line.trim()];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^#{1,4}\s+/.test(lines[i]) &&
      !lines[i].startsWith("```") &&
      !/^\|.*\|$/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim()) &&
      !/^-\s+/.test(lines[i].trim())
    ) {
      paragraphLines.push(lines[i].trim());
      i += 1;
    }
    children.push(paragraph(paragraphLines.join(" ")));
  }
  return children;
}

const doc = new Document({
  styles: {
    default: {
      document: { run: { font: "Microsoft YaHei", size: 22 } },
    },
    paragraphStyles: [
      {
        id: "Heading1",
        name: "Heading 1",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 32, bold: true, font: "Microsoft YaHei", color: "111827" },
        paragraph: { spacing: { before: 300, after: 180 }, outlineLevel: 0 },
      },
      {
        id: "Heading2",
        name: "Heading 2",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 28, bold: true, font: "Microsoft YaHei", color: "1F2937" },
        paragraph: { spacing: { before: 240, after: 160 }, outlineLevel: 1 },
      },
      {
        id: "Heading3",
        name: "Heading 3",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 24, bold: true, font: "Microsoft YaHei", color: "334155" },
        paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 2 },
      },
    ],
  },
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{
          level: 0,
          format: LevelFormat.BULLET,
          text: "\u2022",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 520, hanging: 260 } } },
        }],
      },
      {
        reference: "numbers",
        levels: [{
          level: 0,
          format: LevelFormat.DECIMAL,
          text: "%1.",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 560, hanging: 280 } } },
        }],
      },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 },
      },
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [
            new TextRun("Page "),
            new TextRun({ children: [PageNumber.CURRENT] }),
          ],
        })],
      }),
    },
    children: parseMarkdown(markdown),
  }],
});

Packer.toBuffer(doc).then((buffer) => {
  fs.writeFileSync(outputPath, buffer);
  console.log(outputPath);
});
