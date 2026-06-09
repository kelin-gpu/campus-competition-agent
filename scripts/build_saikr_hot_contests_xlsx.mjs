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
const meta = payload.meta || {};

const mainHeaders = [
  "rank",
  "title",
  "detail_url",
  "organizer",
  "summary",
  "view_count",
  "follow_count",
  "signup_deadline",
  "contest_time",
  "participant_scope",
  "fee_or_status",
  "tags",
  "source_page",
  "scraped_at",
  "parse_status",
  "parse_notes",
];

const fieldDescriptions = [
  ["rank", "热门页展示顺序，从 1 开始。"],
  ["title", "竞赛标题。"],
  ["detail_url", "赛氪详情页链接。"],
  ["organizer", "列表页或详情页解析到的主办方/组织单位。"],
  ["summary", "列表页或详情页提取的竞赛简介。"],
  ["view_count", "浏览量，已尽量转换为整数。"],
  ["follow_count", "关注/收藏量，已尽量转换为整数。"],
  ["signup_deadline", "报名截止或报名时间字段，按页面原文保留。"],
  ["contest_time", "比赛/竞赛/活动时间字段，按页面原文保留。"],
  ["participant_scope", "参赛对象或参赛资格字段，按页面原文保留。"],
  ["fee_or_status", "费用、报名状态或相关状态字段，按页面原文保留。"],
  ["tags", "页面标签，多个标签使用顿号分隔。"],
  ["source_page", "热门竞赛列表页。"],
  ["scraped_at", "详情抓取时间。"],
  ["parse_status", "detail_ok / detail_partial / detail_failed / list_only。"],
  ["parse_notes", "字段缺失、失败原因或解析说明。"],
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

function applyHeaderStyle(range) {
  range.format = {
    fill: "#1F4E79",
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
  };
}

function setWidths(sheet, widths, rowCount) {
  widths.forEach((width, idx) => {
    sheet.getRange(`${colLetter(idx)}1:${colLetter(idx)}${rowCount}`).format.columnWidthPx = width;
  });
}

const workbook = Workbook.create();
const mainSheet = workbook.worksheets.add("热门竞赛Top50");
const notesSheet = workbook.worksheets.add("抓取说明");
const rawSheet = workbook.worksheets.add("原始详情摘要");

for (const sheet of [mainSheet, notesSheet, rawSheet]) {
  sheet.showGridLines = false;
}

const mainRows = [
  mainHeaders,
  ...records.map((record) => mainHeaders.map((key) => safeValue(record[key]))),
];
mainSheet.getRangeByIndexes(0, 0, mainRows.length, mainHeaders.length).values = mainRows;
applyHeaderStyle(mainSheet.getRangeByIndexes(0, 0, 1, mainHeaders.length));
mainSheet.getRangeByIndexes(0, 0, mainRows.length, mainHeaders.length).format = {
  borders: { preset: "all", style: "thin", color: "#D9E2F3" },
  wrapText: true,
};
applyHeaderStyle(mainSheet.getRangeByIndexes(0, 0, 1, mainHeaders.length));
mainSheet.freezePanes.freezeRows(1);
mainSheet.tables.add(`A1:${colLetter(mainHeaders.length - 1)}${mainRows.length}`, true, "SaikrHotContests");
setWidths(
  mainSheet,
  [56, 260, 290, 180, 360, 90, 90, 150, 170, 180, 140, 170, 290, 170, 110, 260],
  Math.max(mainRows.length, 2),
);

const notesRows = [
  ["赛氪热门竞赛抓取说明", ""],
  ["来源 URL", safeValue(meta.source_url)],
  ["请求数量", safeValue(meta.requested_limit)],
  ["实际抓取数量", safeValue(meta.actual_count)],
  ["开始时间", safeValue(meta.started_at)],
  ["完成时间", safeValue(meta.finished_at)],
  ["抓取范围", "仅抓取赛氪热门竞赛列表页和公开竞赛详情页，不抓取登录态、报名后台、用户数据或评论。"],
  ["解析说明", "字段来自页面可见文本；时间和对象等字段无法标准化时保留页面原文；缺失或失败原因写入 parse_notes。"],
  ["", ""],
  ["字段", "说明"],
  ...fieldDescriptions,
];
notesSheet.getRangeByIndexes(0, 0, notesRows.length, 2).values = notesRows;
notesSheet.getRange("A1:B1").merge();
notesSheet.getRange("A1").format = {
  fill: "#1F4E79",
  font: { bold: true, color: "#FFFFFF", size: 14 },
};
notesSheet.getRangeByIndexes(9, 0, 1, 2).format = {
  fill: "#5B9BD5",
  font: { bold: true, color: "#FFFFFF" },
};
notesSheet.getRangeByIndexes(0, 0, notesRows.length, 2).format = {
  borders: { preset: "all", style: "thin", color: "#D9E2F3" },
  wrapText: true,
};
notesSheet.getRange("A1:B1").format.rowHeightPx = 32;
setWidths(notesSheet, [160, 720], notesRows.length);

const rawHeaders = ["rank", "title", "detail_url", "detail_text_excerpt"];
const rawRows = [
  rawHeaders,
  ...records.map((record) => rawHeaders.map((key) => safeValue(record[key]))),
];
rawSheet.getRangeByIndexes(0, 0, rawRows.length, rawHeaders.length).values = rawRows;
rawSheet.getRangeByIndexes(0, 0, rawRows.length, rawHeaders.length).format = {
  borders: { preset: "all", style: "thin", color: "#D9E2F3" },
  wrapText: true,
};
applyHeaderStyle(rawSheet.getRangeByIndexes(0, 0, 1, rawHeaders.length));
rawSheet.freezePanes.freezeRows(1);
rawSheet.tables.add(`A1:${colLetter(rawHeaders.length - 1)}${rawRows.length}`, true, "SaikrDetailExcerpts");
setWidths(rawSheet, [56, 280, 310, 760], Math.max(rawRows.length, 2));

await workbook.inspect({
  kind: "sheet,table",
  maxChars: 3000,
  tableMaxRows: 5,
  tableMaxCols: 6,
});
await workbook.render({ sheetName: "热门竞赛Top50", range: "A1:P12", scale: 1, format: "png" });

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`Saved workbook: ${outputPath}`);
