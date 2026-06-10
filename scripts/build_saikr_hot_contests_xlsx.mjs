import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const artifactToolPath =
  process.env.ARTIFACT_TOOL_MODULE ||
  "C:/Users/lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const { SpreadsheetFile, Workbook } = await import(pathToFileURL(artifactToolPath).href);

const [jsonPath, outputPath] = process.argv.slice(2);
if (!jsonPath || !outputPath) {
  console.error("Usage: node build_saikr_hot_contests_xlsx.mjs <input.json> <output.xlsx>");
  process.exit(2);
}

const payload = JSON.parse((await fs.readFile(jsonPath, "utf8")).replace(/^\uFEFF/, ""));
const records = payload.records || [];

const headers = [
  "rank",
  "title",
  "detail_url",
  "organizer",
  "category",
  "registration_time",
  "contest_time",
  "participant_scope",
  "fee_or_status",
  "summary",
  "detail_text",
  "source_url",
  "fetched_at",
  "http_status",
];

function colLetter(index) {
  let n = index + 1;
  let s = "";
  while (n > 0) {
    const mod = (n - 1) % 26;
    s = String.fromCharCode(65 + mod) + s;
    n = Math.floor((n - mod) / 26);
  }
  return s;
}

function safeValue(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "number" || typeof value === "boolean") return value;
  return String(value);
}

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("原始数据库");
sheet.showGridLines = false;

const rows = [headers, ...records.map((record) => headers.map((key) => safeValue(record[key])))];
sheet.getRangeByIndexes(0, 0, rows.length, headers.length).values = rows;
sheet.getRangeByIndexes(0, 0, rows.length, headers.length).format = {
  borders: { preset: "all", style: "thin", color: "#D9E2F3" },
  wrapText: false,
};
sheet.getRangeByIndexes(0, 0, 1, headers.length).format = {
  fill: "#1F4E79",
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
};
sheet.freezePanes.freezeRows(1);
sheet.tables.add(`A1:${colLetter(headers.length - 1)}${Math.max(rows.length, 2)}`, true, "SaikrHotContestFields");

const widths = [56, 280, 300, 210, 120, 190, 190, 210, 160, 420, 760, 300, 170, 110];
widths.forEach((width, idx) => {
  sheet.getRange(`${colLetter(idx)}1:${colLetter(idx)}${Math.max(rows.length, 2)}`).format.columnWidthPx = width;
});

await workbook.inspect({
  kind: "sheet,table",
  maxChars: 2000,
  tableMaxRows: 5,
  tableMaxCols: 8,
});

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`Saved workbook: ${outputPath}`);
