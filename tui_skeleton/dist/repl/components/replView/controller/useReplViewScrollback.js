import { useCallback, useEffect, useMemo } from "react";
import { HEADER_COLOR } from "../../../viewUtils.js";
import { useScrollbackFeed } from "../scrollback/useScrollbackFeed.js";
import { buildTranscript } from "../../../transcriptBuilder.js";
import { useToolRenderer } from "../renderers/toolRenderer.js";
import { useConversationMeasure, useConversationRenderer } from "../renderers/conversationRenderer.js";
import { sliceTailByLineBudget, trimTailByLineCount } from "../layout/windowing.js";
import { CHALK } from "../theme.js";
import { formatCostUsd, formatLatency } from "../utils/format.js";
import { useReplViewRenderNodes } from "./useReplViewRenderNodes.js";
import { buildLandingContext, buildScrollbackLanding } from "../landing/scrollbackLanding.js";
export const useReplViewScrollback = (context) => {
    const { filteredModels, modelMenu, modelOffset, MODEL_VISIBLE_ROWS, modelProviderCounts, normalizeProviderKey, formatProviderLabel, stats, mode, permissionMode, claudeChrome, pendingResponse, disconnected, status, contentWidth, rowCount, guardrailNotice, viewPrefs, overlayActive, filePickerActive, fileMenuMode, filePicker, fileIndexMeta, fileMenuRows, fileMenuWindow, fileMenuNeedlePending, selectedFileIsLarge, suggestions, suggestionWindow, hints, attachments, fileMentions, transcriptViewerOpen, transcriptNudge, completionHint, renderConversationEntryForFeed, renderToolEntryForFeed, renderConversationEntryRef, renderToolEntryRef, pushCommandResultRef, conversation, toolEvents, SCROLLBACK_MODE, ASCII_HEADER, setCollapsedVersion, collapsedEntriesRef, collapsedVersion, selectedCollapsibleEntryId, setSelectedCollapsibleEntryId, animationTick, spinner, liveSlots, keymap, } = context;
    const visibleModels = useMemo(() => {
        if (modelMenu.status !== "ready")
            return [];
        return filteredModels.slice(modelOffset, modelOffset + MODEL_VISIBLE_ROWS);
    }, [filteredModels, modelMenu.status, modelOffset, MODEL_VISIBLE_ROWS]);
    const visibleModelRows = useMemo(() => {
        if (modelMenu.status !== "ready")
            return [];
        const rows = [];
        for (let idx = 0; idx < visibleModels.length; idx += 1) {
            const item = visibleModels[idx];
            const globalIndex = modelOffset + idx;
            const prev = filteredModels[globalIndex - 1];
            const providerKey = normalizeProviderKey(item.provider);
            const prevKey = normalizeProviderKey(prev?.provider);
            if (idx === 0 || !prev || prevKey !== providerKey) {
                const label = formatProviderLabel(item.provider);
                const count = modelProviderCounts.get(providerKey) ?? null;
                rows.push({ kind: "header", label, count });
            }
            rows.push({ kind: "item", item, index: globalIndex });
        }
        return rows;
    }, [filteredModels, modelMenu.status, modelOffset, modelProviderCounts, normalizeProviderKey, visibleModels, formatProviderLabel]);
    const headerLines = useMemo(() => ["", ...ASCII_HEADER.map((line) => CHALK.hex(HEADER_COLOR)(line))], [ASCII_HEADER]);
    const usageSummary = useMemo(() => {
        const usage = stats.usage;
        if (!usage)
            return null;
        const parts = [];
        const hasTokenInputs = usage.totalTokens != null || usage.promptTokens != null || usage.completionTokens != null;
        if (hasTokenInputs) {
            const total = usage.totalTokens ??
                (usage.promptTokens != null || usage.completionTokens != null
                    ? (usage.promptTokens ?? 0) + (usage.completionTokens ?? 0)
                    : undefined);
            if (total != null && Number.isFinite(total)) {
                parts.push(`tok ${Math.round(total)}`);
            }
        }
        if (usage.costUsd != null && Number.isFinite(usage.costUsd)) {
            parts.push(`cost ${formatCostUsd(usage.costUsd)}`);
        }
        if (usage.latencyMs != null && Number.isFinite(usage.latencyMs)) {
            parts.push(`lat ${formatLatency(usage.latencyMs)}`);
        }
        return parts.length > 0 ? parts.join(" · ") : null;
    }, [stats.usage]);
    const modeBadge = useMemo(() => {
        const { label, color } = context.normalizeModeLabel(mode);
        return CHALK.hex(color)(`[${label}]`);
    }, [mode, context]);
    const permissionBadge = useMemo(() => {
        const { label, color } = context.normalizePermissionLabel(permissionMode);
        return CHALK.hex(color)(`[${label}]`);
    }, [permissionMode, context]);
    const headerSubtitleLines = useMemo(() => {
        if (!claudeChrome) {
            return [CHALK.cyan("breadboard — interactive session")];
        }
        const modelLine = stats.model ? `${stats.model} · API Usage Billing` : "Model unknown · API Usage Billing";
        const cwdLine = process.cwd();
        return [
            CHALK.cyan(`breadboard v${context.CLI_VERSION}`),
            CHALK.dim(modelLine),
            CHALK.dim(`mode ${modeBadge}  perms ${permissionBadge}`),
            CHALK.dim(cwdLine),
        ];
    }, [claudeChrome, modeBadge, permissionBadge, stats.model, context]);
    const promptRule = useMemo(() => "─".repeat(contentWidth), [contentWidth]);
    const pendingClaudeStatus = useMemo(() => (claudeChrome && pendingResponse ? "· Deciphering… (esc to interrupt · thinking)" : null), [claudeChrome, pendingResponse]);
    const networkBanner = useMemo(() => {
        if (disconnected) {
            return {
                tone: "error",
                label: "Disconnected",
                message: "Lost connection to the engine. Check network or restart the session.",
            };
        }
        if (status.toLowerCase().startsWith("reconnecting")) {
            return {
                tone: "warning",
                label: "Reconnecting",
                message: status,
            };
        }
        return null;
    }, [disconnected, status]);
    const compactMode = viewPrefs.virtualization === "compact" || (viewPrefs.virtualization === "auto" && rowCount <= 32);
    const transcript = useMemo(() => {
        const includeTools = viewPrefs.toolRail !== false || viewPrefs.rawStream === true;
        return buildTranscript({
            conversation,
            toolEvents: includeTools ? toolEvents : [],
            rawEvents: viewPrefs.rawStream ? context.rawEvents ?? [] : [],
        }, { includeRawEvents: viewPrefs.rawStream === true, pendingToolsInTail: true });
    }, [conversation, toolEvents, viewPrefs.rawStream, viewPrefs.toolRail, context.rawEvents]);
    const headerReserveRows = useMemo(() => {
        if (SCROLLBACK_MODE)
            return 0;
        return claudeChrome ? 0 : 3;
    }, [SCROLLBACK_MODE, claudeChrome]);
    const guardrailReserveRows = useMemo(() => {
        if (!guardrailNotice)
            return 0;
        const expanded = Boolean(guardrailNotice.detail && guardrailNotice.expanded);
        return expanded ? 7 : 6;
    }, [guardrailNotice]);
    const composerReserveRows = useMemo(() => {
        const outerMargin = 1;
        const promptLine = 1;
        const promptRuleRows = claudeChrome ? 2 : 0;
        const pendingStatusRows = claudeChrome && pendingClaudeStatus ? 1 : 0;
        const suggestionRows = (() => {
            if (overlayActive)
                return 1;
            if (filePickerActive) {
                const chromeRows = claudeChrome ? 0 : 2;
                const listMargin = claudeChrome ? 0 : 1;
                const base = chromeRows + listMargin;
                if (fileMenuMode === "tree") {
                    if (filePicker.status === "loading" || filePicker.status === "hidden")
                        return base + 1;
                    if (filePicker.status === "error")
                        return base + 2;
                }
                else {
                    if (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning") {
                        if (fileMenuRows.length === 0)
                            return base + 1;
                    }
                    if (fileIndexMeta.status === "error" && fileMenuRows.length === 0)
                        return base + 2;
                }
                if (fileMenuRows.length === 0)
                    return base + 1;
                const hiddenRows = (fileMenuWindow.hiddenAbove > 0 ? 1 : 0) + (fileMenuWindow.hiddenBelow > 0 ? 1 : 0);
                const fuzzyStatusRows = fileMenuMode === "fuzzy"
                    ? (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning" ? 1 : 0) +
                        (fileIndexMeta.truncated ? 1 : 0) +
                        (fileMenuNeedlePending ? 1 : 0)
                    : 0;
                const largeHintRows = selectedFileIsLarge ? 1 : 0;
                return base + fileMenuWindow.lineCount + hiddenRows + fuzzyStatusRows + largeHintRows;
            }
            if (suggestions.length === 0)
                return 1;
            const hiddenRows = (suggestionWindow.hiddenAbove > 0 ? 1 : 0) + (suggestionWindow.hiddenBelow > 0 ? 1 : 0);
            return 1 + suggestionWindow.lineCount + hiddenRows;
        })();
        const hintCount = overlayActive ? 0 : Math.min(4, hints.length);
        const claudeStatusRows = claudeChrome ? 1 : 0;
        const claudeShortcutRows = claudeChrome ? (context.shortcutsOpen ? 6 : 1) : 0;
        const hintRows = overlayActive
            ? 0
            : claudeChrome
                ? claudeStatusRows + claudeShortcutRows
                : hintCount > 0
                    ? 1 + hintCount
                    : 0;
        const attachmentRows = overlayActive ? 0 : attachments.length > 0 ? attachments.length + 3 : 0;
        const fileMentionRows = overlayActive ? 0 : fileMentions.length > 0 ? fileMentions.length + 3 : 0;
        return (outerMargin +
            pendingStatusRows +
            promptRuleRows +
            promptLine +
            suggestionRows +
            hintRows +
            attachmentRows +
            fileMentionRows);
    }, [
        attachments.length,
        claudeChrome,
        fileMentions.length,
        fileIndexMeta.status,
        fileIndexMeta.truncated,
        fileMenuRows.length,
        fileMenuWindow.hiddenAbove,
        fileMenuWindow.hiddenBelow,
        fileMenuWindow.lineCount,
        fileMenuMode,
        fileMenuNeedlePending,
        filePicker.status,
        filePickerActive,
        hints.length,
        overlayActive,
        pendingClaudeStatus,
        suggestions.length,
        suggestionWindow.hiddenAbove,
        suggestionWindow.hiddenBelow,
        suggestionWindow.lineCount,
        selectedFileIsLarge,
    ]);
    const overlayReserveRows = useMemo(() => {
        if (!overlayActive)
            return 0;
        return Math.min(Math.max(10, Math.floor(rowCount * 0.55)), Math.max(0, rowCount - 8));
    }, [overlayActive, rowCount]);
    const bodyTopMarginRows = 1;
    const bodyBudgetRows = useMemo(() => {
        const available = rowCount - headerReserveRows - guardrailReserveRows - composerReserveRows - bodyTopMarginRows - overlayReserveRows;
        return Math.max(0, available);
    }, [composerReserveRows, guardrailReserveRows, headerReserveRows, overlayReserveRows, rowCount]);
    const statusGlyph = pendingResponse ? spinner : CHALK.hex("#7CF2FF")("●");
    const modelGlyph = CHALK.hex("#B36BFF")("●");
    const remoteGlyph = stats.remote ? CHALK.hex("#7CF2FF")("●") : CHALK.hex("#475569")("○");
    const toolsGlyph = stats.toolCount > 0 ? CHALK.hex("#FBBF24")("●") : CHALK.hex("#475569")("○");
    const eventsGlyph = stats.eventCount > 0 ? CHALK.hex("#A855F7")("●") : CHALK.hex("#475569")("○");
    const turnGlyph = stats.lastTurn != null ? CHALK.hex("#34D399")("●") : CHALK.hex("#475569")("○");
    const transcriptCommitted = transcript.committed;
    const transcriptTail = transcript.tail;
    const chromeLabel = claudeChrome ? "Claude Code" : keymap === "codex" ? "Codex" : "Breadboard";
    const configLabel = chromeLabel;
    const modelLabel = useMemo(() => {
        const raw = stats.model ?? "";
        if (!raw)
            return "model";
        const parts = raw.split("/");
        return parts[parts.length - 1] || raw;
    }, [stats.model]);
    const landingWidth = useMemo(() => Math.max(10, contentWidth - (claudeChrome ? 1 : 0)), [claudeChrome, contentWidth]);
    const landingNode = useMemo(() => buildScrollbackLanding(buildLandingContext(landingWidth, modelLabel, chromeLabel, configLabel, process.cwd())), [chromeLabel, configLabel, landingWidth, modelLabel]);
    const toToolLogEntry = useCallback((entry) => {
        if (entry.kind === "tool") {
            return {
                id: entry.id,
                kind: entry.toolKind,
                text: entry.text,
                status: entry.status,
                callId: entry.callId ?? null,
                display: entry.display ?? null,
                createdAt: entry.createdAt,
            };
        }
        if (entry.kind === "system") {
            const mapKind = (kind) => {
                if (kind === "error")
                    return "error";
                if (kind === "reward")
                    return "reward";
                if (kind === "completion")
                    return "completion";
                return "status";
            };
            return {
                id: entry.id,
                kind: mapKind(entry.systemKind),
                text: entry.text,
                status: entry.status,
                createdAt: entry.createdAt,
            };
        }
        return null;
    }, []);
    const renderTranscriptEntryForFeed = useCallback((entry, key) => {
        if (entry.kind === "message") {
            return renderConversationEntryForFeed(entry, key);
        }
        const toolEntry = toToolLogEntry(entry);
        if (!toolEntry)
            return null;
        return renderToolEntryForFeed(toolEntry, key);
    }, [renderConversationEntryForFeed, renderToolEntryForFeed, toToolLogEntry]);
    const { staticFeed, pushCommandResult: pushCommandResultFromFeed, printedTranscriptIdsRef } = useScrollbackFeed({
        enabled: SCROLLBACK_MODE,
        sessionId: context.sessionId,
        viewClearAt: context.viewClearAt,
        headerLines,
        headerSubtitleLines,
        landingNode,
        transcriptEntries: transcriptCommitted,
        streamingEntries: transcriptTail,
        renderTranscriptEntry: renderTranscriptEntryForFeed,
        transcriptViewerOpen,
    });
    useEffect(() => {
        pushCommandResultRef.current = pushCommandResultFromFeed;
    }, [pushCommandResultFromFeed, pushCommandResultRef]);
    const { measureToolEntryLines, renderToolEntry } = useToolRenderer({
        claudeChrome,
        verboseOutput: context.verboseOutput,
        collapseThreshold: context.TOOL_COLLAPSE_THRESHOLD,
        collapseHead: context.TOOL_COLLAPSE_HEAD,
        collapseTail: context.TOOL_COLLAPSE_TAIL,
        labelWidth: context.TOOL_LABEL_WIDTH,
        contentWidth,
        diffLineNumbers: viewPrefs?.diffLineNumbers,
    });
    const { isEntryCollapsible, measureConversationEntryLines } = useConversationMeasure({
        viewPrefs,
        verboseOutput: context.verboseOutput,
        collapsedEntriesRef,
        collapsedVersion,
        contentWidth,
    });
    const measureTranscriptEntryLines = useCallback((entry) => {
        if (entry.kind === "message") {
            return measureConversationEntryLines(entry);
        }
        const toolEntry = toToolLogEntry(entry);
        if (!toolEntry)
            return 0;
        return measureToolEntryLines(toolEntry);
    }, [measureConversationEntryLines, measureToolEntryLines, toToolLogEntry]);
    const unprintedTranscriptEntries = useMemo(() => {
        if (!SCROLLBACK_MODE)
            return transcriptCommitted;
        const printed = printedTranscriptIdsRef.current;
        return transcriptCommitted.filter((entry) => !printed.has(entry.id));
    }, [transcriptCommitted, printedTranscriptIdsRef, SCROLLBACK_MODE]);
    const transcriptEntriesForWindow = useMemo(() => {
        if (!SCROLLBACK_MODE) {
            const base = transcriptTail.length > 0 ? [...transcriptCommitted, ...transcriptTail] : transcriptCommitted;
            if (transcriptNudge > 0) {
                return trimTailByLineCount(base, transcriptNudge, measureTranscriptEntryLines);
            }
            return base;
        }
        return transcriptTail.length > 0 ? [...unprintedTranscriptEntries, ...transcriptTail] : unprintedTranscriptEntries;
    }, [transcriptCommitted, transcriptTail, transcriptNudge, measureTranscriptEntryLines, unprintedTranscriptEntries, SCROLLBACK_MODE]);
    const transcriptLineBudget = overlayActive ? Math.min(10, bodyBudgetRows) : bodyBudgetRows;
    const conversationWindow = useMemo(() => sliceTailByLineBudget(transcriptEntriesForWindow, transcriptLineBudget, measureTranscriptEntryLines), [transcriptEntriesForWindow, measureTranscriptEntryLines, transcriptLineBudget]);
    const collapsibleEntries = useMemo(() => conversationWindow.items.filter((entry) => entry.kind === "message" && isEntryCollapsible(entry)), [conversationWindow, isEntryCollapsible]);
    const collapsibleMeta = useMemo(() => {
        const map = new Map();
        const total = collapsibleEntries.length;
        collapsibleEntries.forEach((entry, index) => {
            map.set(entry.id, { index, total });
        });
        return map;
    }, [collapsibleEntries]);
    useEffect(() => {
        const map = collapsedEntriesRef.current;
        let changed = false;
        const activeIds = new Set(collapsibleEntries.map((entry) => entry.id));
        for (const key of Array.from(map.keys())) {
            if (!activeIds.has(key)) {
                map.delete(key);
                changed = true;
            }
        }
        if (viewPrefs.collapseMode === "none") {
            for (const id of activeIds) {
                if (map.get(id) !== false) {
                    map.set(id, false);
                    changed = true;
                }
            }
        }
        else if (viewPrefs.collapseMode === "all") {
            for (const id of activeIds) {
                if (map.get(id) !== true) {
                    map.set(id, true);
                    changed = true;
                }
            }
        }
        else {
            for (const id of activeIds) {
                if (!map.has(id)) {
                    map.set(id, true);
                    changed = true;
                }
            }
        }
        if (changed) {
            setCollapsedVersion((value) => value + 1);
        }
    }, [collapsibleEntries, viewPrefs.collapseMode, setCollapsedVersion, collapsedEntriesRef]);
    useEffect(() => {
        setSelectedCollapsibleEntryId((prev) => {
            if (collapsibleEntries.length === 0) {
                return prev === null ? prev : null;
            }
            if (prev && collapsibleEntries.some((entry) => entry.id === prev)) {
                return prev;
            }
            const latest = collapsibleEntries[collapsibleEntries.length - 1];
            return latest?.id ?? null;
        });
    }, [collapsibleEntries, setSelectedCollapsibleEntryId]);
    const toggleCollapsedEntry = useCallback((entryId) => {
        const map = collapsedEntriesRef.current;
        const isExpanded = map.get(entryId) === false;
        map.set(entryId, isExpanded ? true : false);
        setCollapsedVersion((value) => value + 1);
    }, [collapsedEntriesRef, setCollapsedVersion]);
    const cycleCollapsibleSelection = useCallback((direction) => {
        if (collapsibleEntries.length === 0)
            return false;
        if (!selectedCollapsibleEntryId) {
            setSelectedCollapsibleEntryId(collapsibleEntries[collapsibleEntries.length - 1]?.id ?? null);
            return true;
        }
        const currentIndex = collapsibleEntries.findIndex((entry) => entry.id === selectedCollapsibleEntryId);
        const safeIndex = currentIndex === -1 ? collapsibleEntries.length - 1 : currentIndex;
        const nextIndex = (safeIndex + direction + collapsibleEntries.length) % collapsibleEntries.length;
        setSelectedCollapsibleEntryId(collapsibleEntries[nextIndex].id);
        return true;
    }, [collapsibleEntries, selectedCollapsibleEntryId, setSelectedCollapsibleEntryId]);
    const toggleSelectedCollapsibleEntry = useCallback(() => {
        if (!selectedCollapsibleEntryId)
            return false;
        toggleCollapsedEntry(selectedCollapsibleEntryId);
        return true;
    }, [selectedCollapsibleEntryId, toggleCollapsedEntry]);
    const { renderConversationEntry } = useConversationRenderer({
        viewPrefs,
        collapsedEntriesRef,
        collapsedVersion,
        collapsibleMeta,
        selectedCollapsibleEntryId,
        labelWidth: context.LABEL_WIDTH,
        contentWidth,
        isEntryCollapsible,
    });
    const renderTranscriptEntry = useCallback((entry, key) => {
        if (entry.kind === "message") {
            return renderConversationEntry(entry, key);
        }
        const toolEntry = toToolLogEntry(entry);
        if (!toolEntry)
            return null;
        return renderToolEntry(toolEntry, key);
    }, [renderConversationEntry, renderToolEntry, toToolLogEntry]);
    useEffect(() => {
        renderConversationEntryRef.current = renderConversationEntry;
        renderToolEntryRef.current = renderToolEntry;
    }, [renderConversationEntry, renderToolEntry, renderConversationEntryRef, renderToolEntryRef]);
    const renderNodes = useReplViewRenderNodes({
        claudeChrome,
        keymap,
        contentWidth,
        hints,
        completionHint,
        shortcutsOpen: context.shortcutsOpen,
        ctrlCPrimedAt: context.ctrlCPrimedAt,
        escPrimedAt: context.escPrimedAt,
        pendingResponse,
        scrollbackMode: SCROLLBACK_MODE,
        liveSlots,
        animationTick,
        collapsibleEntries,
        collapsibleMeta,
        selectedCollapsibleEntryId,
        compactMode,
        transcriptWindow: conversationWindow,
        renderTranscriptEntry,
    });
    return {
        visibleModels,
        visibleModelRows,
        headerLines,
        landingNode,
        usageSummary,
        modeBadge,
        permissionBadge,
        headerSubtitleLines,
        promptRule,
        pendingClaudeStatus,
        networkBanner,
        compactMode,
        headerReserveRows,
        guardrailReserveRows,
        composerReserveRows,
        overlayReserveRows,
        bodyBudgetRows,
        statusGlyph,
        modelGlyph,
        remoteGlyph,
        toolsGlyph,
        eventsGlyph,
        turnGlyph,
        staticFeed,
        conversationWindow,
        collapsibleEntries,
        collapsibleMeta,
        toggleCollapsedEntry,
        cycleCollapsibleSelection,
        toggleSelectedCollapsibleEntry,
        renderConversationEntry,
        renderToolEntry,
        ...renderNodes,
    };
};
