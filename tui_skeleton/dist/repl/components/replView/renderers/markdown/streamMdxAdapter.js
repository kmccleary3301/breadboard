import { maybeHighlightCode } from "../../../../shikiHighlighter.js";
import { CHALK, COLORS, ASCII_ONLY } from "../../theme.js";
import { stringWidth, stripAnsiCodes, visibleWidth } from "../../utils/ansi.js";
import { renderTokenLine, tokenLineFromThemed, tokenLineFromV1 } from "../../../../markdown/tokenRender.js";
export const stripFence = (raw) => {
    const normalized = raw.replace(/\r\n?/g, "\n");
    const lines = normalized.split("\n");
    if (lines.length === 0)
        return { code: raw };
    const first = lines[0].trim();
    if (!first.startsWith("```"))
        return { code: normalized };
    const langHint = first.slice(3).trim() || undefined;
    let end = lines.length - 1;
    while (end > 0 && lines[end].trim().length === 0)
        end -= 1;
    if (end > 0 && lines[end].trim().startsWith("```"))
        end -= 1;
    return { code: lines.slice(1, end + 1).join("\n"), langHint };
};
export const formatInlineNodes = (nodes) => {
    if (!nodes || nodes.length === 0)
        return "";
    return renderInlineSegments(inlineNodesToSegments(nodes));
};
const composeStyles = (styles) => {
    const filtered = styles.filter(Boolean);
    if (filtered.length === 0)
        return undefined;
    return (text) => filtered.reduce((acc, fn) => fn(acc), text);
};
const inlineNodesToPlain = (nodes) => {
    if (!nodes || nodes.length === 0)
        return "";
    const flatten = (node) => {
        switch (node.kind) {
            case "text":
                return node.text;
            case "strong":
            case "em":
            case "strike":
                return node.children.map(flatten).join("");
            case "code":
                return node.text;
            case "link":
                return `${node.children.map(flatten).join("")}${node.href ? ` (${node.href})` : ""}`;
            case "mention":
                return `@${node.handle}`;
            case "citation":
                return `[${node.id}]`;
            case "math-inline":
            case "math-display":
                return node.tex;
            case "footnote-ref":
                return `[^${node.label}]`;
            case "image":
                return node.alt ? `![${node.alt}]` : "[image]";
            case "br":
                return "\n";
            default:
                return "children" in node ? node.children?.map(flatten).join("") ?? "" : "";
        }
    };
    return nodes.map(flatten).join("");
};
const inlineNodesToSegments = (nodes, styles = []) => {
    if (!nodes || nodes.length === 0)
        return [];
    const segments = [];
    const unwrapDelimited = (value, marker) => {
        if (!value.startsWith(marker) || !value.endsWith(marker))
            return null;
        if (value.length <= marker.length * 2)
            return null;
        const inner = value.slice(marker.length, -marker.length);
        return inner.length > 0 ? inner : null;
    };
    const render = (node, stack) => {
        switch (node.kind) {
            case "text": {
                if (node.text.length === 0)
                    return;
                segments.push({ text: node.text, style: composeStyles(stack) });
                return;
            }
            case "strong":
                node.children.forEach((child) => {
                    if (child.kind === "text") {
                        const italicInner = unwrapDelimited(child.text, "*") ??
                            unwrapDelimited(child.text, "_");
                        if (italicInner != null) {
                            segments.push({
                                text: italicInner,
                                style: composeStyles([...stack, CHALK.bold, CHALK.italic]),
                            });
                            return;
                        }
                    }
                    render(child, [...stack, CHALK.bold]);
                });
                return;
            case "em":
                node.children.forEach((child) => {
                    if (child.kind === "text") {
                        const boldInner = unwrapDelimited(child.text, "**") ??
                            unwrapDelimited(child.text, "__");
                        if (boldInner != null) {
                            segments.push({
                                text: boldInner,
                                style: composeStyles([...stack, CHALK.italic, CHALK.bold]),
                            });
                            return;
                        }
                    }
                    render(child, [...stack, CHALK.italic]);
                });
                return;
            case "strike":
                node.children.forEach((child) => render(child, [...stack, CHALK.strikethrough]));
                return;
            case "code": {
                const codeStyle = (text) => CHALK.bgHex(COLORS.panel).hex(COLORS.textBright)(text);
                segments.push({ text: ` ${node.text} `, style: composeStyles([...stack, codeStyle]) });
                return;
            }
            case "link": {
                node.children.forEach((child) => render(child, [...stack, CHALK.underline]));
                if (node.href) {
                    segments.push({ text: ` (${node.href})`, style: composeStyles([...stack, CHALK.dim]) });
                }
                return;
            }
            case "mention":
                segments.push({ text: `@${node.handle}`, style: composeStyles([...stack, (text) => CHALK.hex(COLORS.info)(text)]) });
                return;
            case "citation":
                segments.push({ text: `[${node.id}]`, style: composeStyles([...stack, (text) => CHALK.hex(COLORS.info)(text)]) });
                return;
            case "math-inline":
            case "math-display":
                segments.push({ text: node.tex, style: composeStyles([...stack, (text) => CHALK.hex(COLORS.info)(text)]) });
                return;
            case "footnote-ref":
                segments.push({ text: `[^${node.label}]`, style: composeStyles([...stack, (text) => CHALK.hex(COLORS.accent)(text)]) });
                return;
            case "image":
                segments.push({ text: node.alt ? `![${node.alt}]` : "[image]", style: composeStyles(stack) });
                return;
            case "br":
                segments.push({ text: "\n", style: composeStyles(stack) });
                return;
            default:
                if ("children" in node && Array.isArray(node.children)) {
                    ;
                    node.children?.forEach((child) => render(child, stack));
                }
        }
    };
    nodes.forEach((node) => render(node, styles));
    return segments;
};
const renderInlineSegments = (segments) => segments.map((segment) => (segment.style ? segment.style(segment.text) : segment.text)).join("");
const splitTokenByWidth = (token, maxWidth) => {
    if (maxWidth <= 0)
        return [token];
    const parts = [];
    let current = "";
    let width = 0;
    for (const char of token) {
        const charWidth = stringWidth(char);
        if (width + charWidth > maxWidth && current.length > 0) {
            parts.push(current);
            current = "";
            width = 0;
        }
        current += char;
        width += charWidth;
    }
    if (current.length > 0)
        parts.push(current);
    return parts;
};
const wrapInlineSegments = (segments, maxWidth) => {
    const lines = [];
    let current = "";
    let currentWidth = 0;
    const flush = () => {
        lines.push(current);
        current = "";
        currentWidth = 0;
    };
    const append = (text, style) => {
        if (text.length === 0)
            return;
        current += style ? style(text) : text;
    };
    for (const segment of segments) {
        const parts = segment.text.split("\n");
        for (let i = 0; i < parts.length; i += 1) {
            const part = parts[i];
            if (part.length > 0) {
                const tokens = part.split(/(\s+)/);
                for (const token of tokens) {
                    if (token.length === 0)
                        continue;
                    const tokenWidth = stringWidth(token);
                    if (currentWidth === 0 && /^\s+$/.test(token))
                        continue;
                    if (tokenWidth <= maxWidth && currentWidth + tokenWidth <= maxWidth) {
                        append(token, segment.style);
                        currentWidth += tokenWidth;
                        continue;
                    }
                    if (tokenWidth > maxWidth) {
                        const pieces = splitTokenByWidth(token, maxWidth);
                        for (const piece of pieces) {
                            if (currentWidth > 0)
                                flush();
                            if (piece.length === 0)
                                continue;
                            append(piece, segment.style);
                            currentWidth = stringWidth(piece);
                            if (currentWidth >= maxWidth)
                                flush();
                        }
                        continue;
                    }
                    flush();
                    if (/^\s+$/.test(token))
                        continue;
                    append(token, segment.style);
                    currentWidth = tokenWidth;
                }
            }
            if (i < parts.length - 1) {
                flush();
            }
        }
    }
    if (current.length > 0 || lines.length === 0) {
        lines.push(current);
    }
    return lines;
};
const applyStyleToSegments = (segments, style) => segments.map((segment) => ({
    ...segment,
    style: composeStyles([segment.style, style]),
}));
const resolveTerminalWidth = () => {
    const envWidth = process.env.BREADBOARD_TUI_FRAME_WIDTH;
    const parsed = envWidth ? Number(envWidth) : NaN;
    const stdoutWidth = typeof process !== "undefined" && process.stdout ? process.stdout.columns : undefined;
    const base = Number.isFinite(parsed) ? parsed : stdoutWidth ?? 120;
    return Math.max(40, base - 4);
};
const normalizeRenderWidth = (value) => {
    if (typeof value !== "number")
        return null;
    if (!Number.isFinite(value))
        return null;
    const floored = Math.floor(value);
    return floored > 0 ? floored : null;
};
const resolveWidth = (options) => {
    const override = normalizeRenderWidth(options?.width);
    return override ?? resolveTerminalWidth();
};
const resolveTerminalRuleWidth = () => {
    const envWidth = process.env.BREADBOARD_TUI_FRAME_WIDTH;
    const parsed = envWidth ? Number(envWidth) : NaN;
    const stdoutWidth = typeof process !== "undefined" && process.stdout ? process.stdout.columns : undefined;
    const base = Number.isFinite(parsed) ? parsed : stdoutWidth ?? 120;
    return Math.max(10, base);
};
const RULE_GLYPH = ASCII_ONLY ? "-" : "─";
const TABLE_CHARS = ASCII_ONLY
    ? {
        h: "-",
        v: "|",
        tl: "+",
        tm: "+",
        tr: "+",
        ml: "+",
        mm: "+",
        mr: "+",
        bl: "+",
        bm: "+",
        br: "+",
    }
    : {
        h: "─",
        v: "│",
        tl: "┌",
        tm: "┬",
        tr: "┐",
        ml: "├",
        mm: "┼",
        mr: "┤",
        bl: "└",
        bm: "┴",
        br: "┘",
    };
const isHorizontalRuleLine = (line) => {
    const trimmed = line.trim();
    return /^([-*_])(?:\s*\1){2,}$/.test(trimmed);
};
const resolveRuleWidth = (options) => {
    const override = normalizeRenderWidth(options?.width);
    return override ?? resolveTerminalRuleWidth();
};
const renderHorizontalRule = (options) => CHALK.hex(COLORS.textMuted)(RULE_GLYPH.repeat(Math.max(1, resolveRuleWidth(options))));
const colorDiffLine = (line) => {
    if (line.startsWith("+++ ") || line.startsWith("--- "))
        return CHALK.hex(COLORS.info)(line);
    if (line.startsWith("@@"))
        return CHALK.hex(COLORS.accent)(line);
    if (line.startsWith("+"))
        return CHALK.bgHex("#1b6a3b").hex(COLORS.success)(line);
    if (line.startsWith("-"))
        return CHALK.bgHex("#7a1f4d").hex(COLORS.error)(line);
    return line;
};
const looksLikeDiffBlock = (lines) => lines.some((line) => line.startsWith("diff --git") ||
    line.startsWith("index ") ||
    line.startsWith("@@") ||
    line.startsWith("+++ ") ||
    line.startsWith("--- "));
const DIFF_BG_COLORS = {
    add: "#123225",
    del: "#3a1426",
};
const MAX_TOKENIZED_DIFF_LINES = 400;
const tokenStyleFns = {
    color: (text, color) => CHALK.hex(color)(text),
    bold: (text) => CHALK.bold(text),
    italic: (text) => CHALK.italic(text),
    underline: (text) => CHALK.underline(text),
};
const normalizeDiffKind = (kind) => {
    if (kind === "add")
        return "add";
    if (kind === "remove")
        return "del";
    if (kind === "hunk")
        return "hunk";
    if (kind === "meta")
        return "meta";
    return "context";
};
const styleDiffLine = (line, kind) => {
    if (kind === "add")
        return CHALK.bgHex(DIFF_BG_COLORS.add)(line);
    if (kind === "del")
        return CHALK.bgHex(DIFF_BG_COLORS.del)(line);
    if (kind === "hunk")
        return CHALK.hex(COLORS.accent)(line);
    if (kind === "meta")
        return CHALK.hex(COLORS.info)(line);
    return line;
};
const renderDiffLine = (raw, kind, tokens) => {
    if (!tokens || tokens.length === 0) {
        return styleDiffLine(raw, kind);
    }
    const marker = raw.startsWith("+") || raw.startsWith("-") || raw.startsWith(" ") ? raw[0] : "";
    const content = renderTokenLine(tokens, tokenStyleFns);
    const hasMarker = marker && tokens[0]?.content?.startsWith(marker);
    const combined = marker && !hasMarker ? `${marker}${content}` : content;
    return styleDiffLine(combined, kind);
};
const renderDiffBlocks = (diffBlocks) => {
    const lines = [];
    const totalLines = diffBlocks.reduce((acc, block) => acc + (block.lines?.length ?? 0), 0);
    const allowTokens = totalLines <= MAX_TOKENIZED_DIFF_LINES;
    for (const block of diffBlocks) {
        for (const line of block.lines ?? []) {
            const kind = line.kind;
            const tokens = allowTokens ? tokenLineFromThemed(line.tokens ?? null) : null;
            const raw = typeof line.raw === "string" ? line.raw : "";
            lines.push(renderDiffLine(raw, kind, tokens));
        }
    }
    return lines;
};
const renderDiffFromCodeLines = (codeLines) => {
    const isDiff = codeLines.some((line) => line.diffKind != null);
    const allowTokens = !isDiff || codeLines.length <= MAX_TOKENIZED_DIFF_LINES;
    return codeLines.map((line) => {
        const kind = normalizeDiffKind(line.diffKind ?? null);
        const raw = line.text ?? "";
        const tokens = allowTokens ? tokenLineFromV1(line.tokens, "dark") : null;
        return renderDiffLine(raw, kind, tokens);
    });
};
export const renderCodeLines = (raw, lang) => {
    const { code, langHint } = stripFence(raw);
    const finalLang = lang ?? langHint;
    const content = (code || raw).replace(/\r\n?/g, "\n");
    const isDiff = finalLang ? finalLang.toLowerCase().includes("diff") : false;
    const shikiLines = maybeHighlightCode(content, finalLang);
    if (shikiLines)
        return shikiLines;
    const lines = content.split("\n");
    return lines.map((line) => {
        if (isDiff)
            return colorDiffLine(line);
        if (line.startsWith("+"))
            return CHALK.hex(COLORS.success)(line);
        if (line.startsWith("-"))
            return CHALK.hex(COLORS.error)(line);
        if (line.startsWith("@@"))
            return CHALK.hex(COLORS.info)(line);
        return CHALK.hex(COLORS.text)(line);
    });
};
const renderListLines = (raw, items) => {
    const lines = raw.split(/\r?\n/);
    if (!items || items.length === 0)
        return lines;
    let index = 0;
    return lines.map((line) => {
        const match = /^(\s*)([*+-]|\d+\.)\s+(.*)$/.exec(line);
        if (!match)
            return line;
        const content = items[index];
        if (!content)
            return line;
        index += 1;
        const formatted = renderInlineSegments(inlineNodesToSegments(content));
        return `${match[1]}${match[2]} ${formatted}`;
    });
};
const parseTableFromMeta = (meta) => {
    const headerRaw = Array.isArray(meta.header) ? meta.header : null;
    const rowsRaw = Array.isArray(meta.rows) ? meta.rows : null;
    if (!headerRaw && !rowsRaw)
        return null;
    const align = Array.isArray(meta.align) ? meta.align : [];
    const toCell = (nodes) => ({
        segments: inlineNodesToSegments(nodes ?? []),
        plain: inlineNodesToPlain(nodes ?? []),
    });
    const header = headerRaw ? headerRaw.map((cell) => toCell(cell)) : undefined;
    const rows = rowsRaw ? rowsRaw.map((row) => row.map((cell) => toCell(cell))) : [];
    return { header, rows, align };
};
const parseInlineMarkdownSegments = (text) => {
    const segments = [];
    const pattern = /(`[^`]+`|\*\*\*[^*]+\*\*\*|___[^_]+___|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|~~[^~]+~~|\[[^\]]+\]\([^)]+\))/g;
    let lastIndex = 0;
    const pushPlain = (value) => {
        if (value.length > 0)
            segments.push({ text: value });
    };
    for (const match of text.matchAll(pattern)) {
        const start = match.index ?? 0;
        const full = match[0] ?? "";
        if (start > lastIndex) {
            pushPlain(text.slice(lastIndex, start));
        }
        if (full.startsWith("`")) {
            const content = full.slice(1, -1);
            segments.push({ text: ` ${content} `, style: (value) => CHALK.bgHex(COLORS.panel).hex(COLORS.textBright)(value) });
        }
        else if (full.startsWith("***") || full.startsWith("___")) {
            const content = full.slice(3, -3);
            segments.push({ text: content, style: composeStyles([CHALK.bold, CHALK.italic]) });
        }
        else if (full.startsWith("**") || full.startsWith("__")) {
            const content = full.slice(2, -2);
            segments.push({ text: content, style: CHALK.bold });
        }
        else if (full.startsWith("~~")) {
            const content = full.slice(2, -2);
            segments.push({ text: content, style: CHALK.strikethrough });
        }
        else if (full.startsWith("*") || full.startsWith("_")) {
            const content = full.slice(1, -1);
            segments.push({ text: content, style: CHALK.italic });
        }
        else if (full.startsWith("[")) {
            const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(full);
            if (linkMatch) {
                segments.push({ text: linkMatch[1], style: CHALK.underline });
                segments.push({ text: ` (${linkMatch[2]})`, style: CHALK.dim });
            }
            else {
                segments.push({ text: full });
            }
        }
        else {
            segments.push({ text: full });
        }
        lastIndex = start + full.length;
    }
    if (lastIndex < text.length) {
        pushPlain(text.slice(lastIndex));
    }
    return segments;
};
const parseTableFromRaw = (raw) => {
    const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0);
    if (lines.length === 0)
        return null;
    const rows = lines.map((line) => {
        const parts = line.split("|").map((part) => part.trim());
        if (parts.length > 1 && parts[0] === "")
            parts.shift();
        if (parts.length > 1 && parts[parts.length - 1] === "")
            parts.pop();
        return parts;
    });
    const isSeparator = (cells) => cells.every((cell) => /^:?-{2,}:?$/.test(cell));
    const separatorIndex = rows.findIndex((cells) => isSeparator(cells));
    const align = separatorIndex >= 0
        ? rows[separatorIndex].map((cell) => {
            const trimmed = cell.trim();
            if (trimmed.startsWith(":") && trimmed.endsWith(":"))
                return "center";
            if (trimmed.startsWith(":"))
                return "left";
            if (trimmed.endsWith(":"))
                return "right";
            return null;
        })
        : [];
    const toCell = (value) => ({
        segments: parseInlineMarkdownSegments(value),
        plain: stripAnsiCodes(value),
    });
    if (separatorIndex === 1) {
        const header = rows[0].map((cell) => toCell(cell));
        const body = rows.slice(2).map((row) => row.map((cell) => toCell(cell)));
        return { header, rows: body, align };
    }
    const body = rows.filter((row) => !isSeparator(row)).map((row) => row.map((cell) => toCell(cell)));
    return { rows: body, align };
};
const padCellContent = (value, width, align) => {
    const currentWidth = visibleWidth(value);
    const pad = Math.max(0, width - currentWidth);
    if (align === "right")
        return `${" ".repeat(pad)}${value}`;
    if (align === "center") {
        const left = Math.floor(pad / 2);
        const right = pad - left;
        return `${" ".repeat(left)}${value}${" ".repeat(right)}`;
    }
    return `${value}${" ".repeat(pad)}`;
};
const renderTableLines = (raw, meta, options) => {
    const table = parseTableFromMeta(meta) ?? parseTableFromRaw(raw);
    if (!table)
        return raw.split(/\r?\n/).filter((line) => line.trim().length > 0);
    const rows = table.rows;
    const header = table.header;
    const colCount = Math.max(header?.length ?? 0, ...rows.map((row) => row.length));
    if (colCount === 0)
        return [];
    const emptyCell = { segments: [], plain: "" };
    const normalizeRow = (row) => {
        const next = row.slice(0, colCount);
        while (next.length < colCount)
            next.push(emptyCell);
        return next;
    };
    const normalizedHeader = header ? normalizeRow(header) : undefined;
    const normalizedRows = rows.map((row) => normalizeRow(row));
    const desiredWidths = new Array(colCount).fill(0);
    const updateWidth = (cell, index) => {
        const parts = cell.plain.split(/\r?\n/);
        const max = Math.max(0, ...parts.map((part) => stringWidth(part)));
        desiredWidths[index] = Math.max(desiredWidths[index], max);
    };
    normalizedHeader?.forEach((cell, idx) => updateWidth(cell, idx));
    normalizedRows.forEach((row) => row.forEach((cell, idx) => updateWidth(cell, idx)));
    const tableWidth = resolveWidth(options);
    const shouldStack = colCount > 1 && (tableWidth < 60 || tableWidth / colCount < 14);
    if (shouldStack) {
        const headerLabels = normalizedHeader
            ? normalizedHeader.map((cell) => cell.plain.trim())
            : new Array(colCount).fill(0).map((_, idx) => `col ${idx + 1}`);
        const lines = [];
        normalizedRows.forEach((row, rowIndex) => {
            if (rowIndex > 0)
                lines.push("");
            for (let col = 0; col < colCount; col += 1) {
                const label = headerLabels[col] ? headerLabels[col] : `col ${col + 1}`;
                const labelSegments = [{ text: `${label}: `, style: CHALK.dim }];
                const valueSegments = row[col]?.segments ?? [];
                const wrapped = wrapInlineSegments([...labelSegments, ...valueSegments], Math.max(1, tableWidth));
                lines.push(...wrapped);
            }
        });
        return lines;
    }
    const maxContentWidth = Math.max(1, tableWidth - (3 * colCount + 1));
    const minWidth = 4;
    let widths = desiredWidths.map((width) => Math.max(minWidth, width));
    let total = widths.reduce((acc, width) => acc + width, 0);
    if (total > maxContentWidth) {
        const minWidths = desiredWidths.map(() => minWidth);
        const minTotal = minWidths.reduce((acc, width) => acc + width, 0);
        if (minTotal >= maxContentWidth) {
            const base = Math.max(1, Math.floor(maxContentWidth / colCount));
            widths = new Array(colCount).fill(base);
            let remaining = maxContentWidth - base * colCount;
            let idx = 0;
            while (remaining > 0) {
                widths[idx % colCount] += 1;
                remaining -= 1;
                idx += 1;
            }
        }
        else {
            widths = desiredWidths.slice();
            total = widths.reduce((acc, width) => acc + width, 0);
            while (total > maxContentWidth) {
                let maxIdx = -1;
                let maxOver = -1;
                for (let i = 0; i < widths.length; i += 1) {
                    const over = widths[i] - minWidths[i];
                    if (over > maxOver) {
                        maxOver = over;
                        maxIdx = i;
                    }
                }
                if (maxIdx < 0 || maxOver <= 0)
                    break;
                widths[maxIdx] -= 1;
                total -= 1;
            }
        }
    }
    const buildRule = (left, mid, right) => {
        const segments = widths.map((width) => TABLE_CHARS.h.repeat(width + 2));
        return `${left}${segments.join(mid)}${right}`;
    };
    const renderRow = (row, alignOverride) => {
        const cellLines = row.map((cell, idx) => wrapInlineSegments(cell.segments, widths[idx]));
        const rowHeight = Math.max(1, ...cellLines.map((lines) => lines.length));
        const lines = [];
        for (let lineIndex = 0; lineIndex < rowHeight; lineIndex += 1) {
            const lineCells = cellLines.map((lines, idx) => {
                const value = lines[lineIndex] ?? "";
                const alignment = alignOverride ?? table.align[idx] ?? null;
                return padCellContent(value, widths[idx], alignment);
            });
            lines.push(`${TABLE_CHARS.v} ${lineCells.join(` ${TABLE_CHARS.v} `)} ${TABLE_CHARS.v}`);
        }
        return lines;
    };
    const output = [];
    output.push(buildRule(TABLE_CHARS.tl, TABLE_CHARS.tm, TABLE_CHARS.tr));
    if (normalizedHeader) {
        const headerCells = normalizedHeader.map((cell) => ({
            ...cell,
            segments: applyStyleToSegments(cell.segments, CHALK.bold),
        }));
        output.push(...renderRow(headerCells, "center"));
        output.push(buildRule(TABLE_CHARS.ml, TABLE_CHARS.mm, TABLE_CHARS.mr));
    }
    normalizedRows.forEach((row, rowIndex) => {
        output.push(...renderRow(row));
        if (rowIndex < normalizedRows.length - 1) {
            output.push(buildRule(TABLE_CHARS.ml, TABLE_CHARS.mm, TABLE_CHARS.mr));
        }
    });
    output.push(buildRule(TABLE_CHARS.bl, TABLE_CHARS.bm, TABLE_CHARS.br));
    return output;
};
const renderInlineMarkdown = (text) => renderInlineSegments(parseInlineMarkdownSegments(text));
const isListLine = (line) => /^(\s*)([*+-]|\d+\.)\s+/.test(line);
const isTableSeparatorLine = (line) => {
    const parts = line.split("|").map((part) => part.trim());
    const trimmed = parts.filter((part) => part.length > 0);
    if (trimmed.length === 0)
        return false;
    return trimmed.every((cell) => /^:?-{2,}:?$/.test(cell));
};
export const renderMarkdownFallbackLines = (rawText, options) => {
    const lines = rawText.replace(/\r\n?/g, "\n").split("\n");
    const output = [];
    let index = 0;
    while (index < lines.length) {
        const line = lines[index];
        if (line.trim().length === 0) {
            output.push("");
            index += 1;
            continue;
        }
        if (isHorizontalRuleLine(line)) {
            output.push(renderHorizontalRule(options));
            index += 1;
            continue;
        }
        if (line.trim().startsWith("```")) {
            const fence = line.trim();
            const lang = fence.slice(3).trim() || undefined;
            const codeLines = [];
            index += 1;
            while (index < lines.length && !lines[index].trim().startsWith("```")) {
                codeLines.push(lines[index]);
                index += 1;
            }
            if (index < lines.length)
                index += 1;
            const fenced = lang ? `\`\`\`${lang}\n${codeLines.join("\n")}\n\`\`\`` : codeLines.join("\n");
            output.push(...renderCodeLines(fenced, lang));
            continue;
        }
        if (line.includes("|") && index + 1 < lines.length && isTableSeparatorLine(lines[index + 1])) {
            const tableLines = [line, lines[index + 1]];
            index += 2;
            while (index < lines.length && lines[index].trim().length > 0 && lines[index].includes("|")) {
                tableLines.push(lines[index]);
                index += 1;
            }
            output.push(...renderTableLines(tableLines.join("\n"), {}, options));
            continue;
        }
        if (line.startsWith("#")) {
            const match = /^(#+)\s*(.*)$/.exec(line.trim());
            const level = match ? Math.min(6, match[1].length) : 1;
            const prefix = "#".repeat(level);
            const content = match ? match[2] : line.replace(/^#+\s*/, "");
            output.push(CHALK.bold.hex(COLORS.accent)(`${prefix} ${renderInlineMarkdown(content)}`));
            index += 1;
            continue;
        }
        if (line.trim().startsWith(">")) {
            const content = line.replace(/^>\s?/, "");
            output.push(CHALK.hex(COLORS.muted)(`> ${renderInlineMarkdown(content)}`));
            index += 1;
            continue;
        }
        if (isListLine(line)) {
            const listLines = [];
            while (index < lines.length && (isListLine(lines[index]) || lines[index].trim().length === 0)) {
                listLines.push(lines[index]);
                index += 1;
            }
            const formatted = listLines.map((entry) => {
                if (!isListLine(entry))
                    return entry;
                const match = /^(\s*)([*+-]|\d+\.)\s+(.*)$/.exec(entry);
                if (!match)
                    return entry;
                const rendered = renderInlineMarkdown(match[3]);
                return `${match[1]}${match[2]} ${rendered}`;
            });
            output.push(...formatted.map((entry) => CHALK.hex(COLORS.textBright)(entry)));
            continue;
        }
        output.push(renderInlineMarkdown(line));
        index += 1;
    }
    return output;
};
const blockToLines = (block, options) => {
    const meta = (block.payload?.meta ?? {});
    switch (block.type) {
        case "thematic-break":
        case "thematicBreak":
        case "horizontal-rule":
        case "hr":
            return [renderHorizontalRule(options)];
        case "paragraph": {
            const value = block.payload.inline ? formatInlineNodes(block.payload.inline) : block.payload.raw ?? "";
            if (!block.payload.inline && isHorizontalRuleLine(value)) {
                return [renderHorizontalRule(options)];
            }
            const lines = value.split(/\r?\n/);
            return looksLikeDiffBlock(lines) ? lines.map((line) => colorDiffLine(line)) : lines;
        }
        case "heading": {
            const levelRaw = typeof meta.level === "number" ? meta.level : typeof meta.depth === "number" ? meta.depth : 1;
            const level = Math.min(6, Math.max(1, levelRaw || 1));
            const prefix = "#".repeat(level);
            const text = block.payload.inline ? formatInlineNodes(block.payload.inline) : block.payload.raw ?? "";
            return [CHALK.bold.hex(COLORS.accent)(`${prefix} ${text}`)];
        }
        case "blockquote": {
            const content = block.payload.inline ? formatInlineNodes(block.payload.inline) : block.payload.raw ?? "";
            return content.split(/\r?\n/).map((line) => CHALK.hex(COLORS.muted)(`> ${line}`));
        }
        case "code": {
            const lang = typeof meta.lang === "string"
                ? meta.lang
                : typeof meta.language === "string"
                    ? meta.language
                    : typeof meta.info === "string"
                        ? meta.info
                        : undefined;
            const diffBlocks = Array.isArray(meta.diffBlocks) ? meta.diffBlocks : null;
            if (diffBlocks && diffBlocks.length > 0) {
                return renderDiffBlocks(diffBlocks);
            }
            const codeLines = Array.isArray(meta.codeLines) ? meta.codeLines : null;
            if (codeLines && codeLines.length > 0) {
                return renderDiffFromCodeLines(codeLines);
            }
            const raw = block.payload.raw ?? "";
            return renderCodeLines(raw, lang);
        }
        case "list": {
            const raw = block.payload.raw ?? "";
            const items = Array.isArray(meta.items) ? meta.items : undefined;
            return renderListLines(raw, items).map((line) => CHALK.hex(COLORS.textBright)(line));
        }
        case "footnotes":
        case "footnote-def":
            return (block.payload.raw ?? "").split(/\r?\n/);
        case "table": {
            const raw = block.payload.raw ?? "";
            return renderTableLines(raw, meta, options);
        }
        case "mdx":
        case "html": {
            const raw = block.payload.raw ?? "";
            const lines = raw.split(/\r?\n/);
            return lines.map((line) => CHALK.dim(line));
        }
        default:
            return (block.payload.raw ?? "").split(/\r?\n/);
    }
};
const needsBlockGap = (prevType, nextType) => {
    if (!prevType)
        return false;
    const blockTypes = new Set([
        "paragraph",
        "heading",
        "blockquote",
        "code",
        "list",
        "table",
        "html",
        "mdx",
    ]);
    return blockTypes.has(prevType) && blockTypes.has(nextType);
};
export const blocksToLines = (blocks, options) => {
    if (!blocks || blocks.length === 0)
        return [];
    const lines = [];
    let lastType = null;
    for (const block of blocks) {
        const rendered = blockToLines(block, options);
        if (rendered.length === 0)
            continue;
        if (needsBlockGap(lastType, block.type) && lines.length > 0) {
            lines.push("");
        }
        lines.push(...rendered);
        lastType = block.type;
    }
    return lines;
};
