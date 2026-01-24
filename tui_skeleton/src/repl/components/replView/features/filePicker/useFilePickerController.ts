import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { SessionFileInfo } from "../../../../../api/types.js"
import type { FileMentionConfig } from "../../../../fileMentions.js"
import type { FilePickerConfig, FilePickerResource } from "../../../../filePicker.js"
import { rankFuzzyFileItems } from "../../../../fileRanking.js"
import { parseAtMentionQuery } from "../../utils/text.js"
import { displayPathForCwd } from "./path.js"
import { findActiveAtMention } from "./mentions.js"
import type {
  ActiveAtMention,
  FileIndexMeta,
  FileIndexStore,
  FileMenuRow,
  FilePickerState,
  QueuedFileMention,
} from "./types.js"

interface FilePickerControllerOptions {
  readonly input: string
  readonly cursor: number
  readonly inputLocked: boolean
  readonly inputTextVersion: number
  readonly claudeChrome: boolean
  readonly rowCount: number
  readonly activeAtCommand: boolean
  readonly filePickerConfig: FilePickerConfig
  readonly filePickerResources: ReadonlyArray<FilePickerResource>
  readonly fileMentionConfig: FileMentionConfig
  readonly onListFiles: (path?: string) => Promise<SessionFileInfo[]>
  readonly handleLineEdit: (value: string, cursor: number) => void
}

export const useFilePickerController = (options: FilePickerControllerOptions) => {
  const {
    input,
    cursor,
    inputLocked,
    inputTextVersion,
    claudeChrome,
    rowCount,
    activeAtCommand,
    filePickerConfig,
    filePickerResources,
    fileMentionConfig,
    onListFiles,
    handleLineEdit,
  } = options

  const [fileMentions, setFileMentions] = useState<QueuedFileMention[]>([])
  const [filePicker, setFilePicker] = useState<FilePickerState>({
    status: "hidden",
    cwd: ".",
    items: [],
    index: 0,
  })
  const filePickerIndexRef = useRef(0)
  const [fileMenuItems, setFileMenuItems] = useState<SessionFileInfo[]>([])
  const fileMenuCacheRef = useRef<{ key: string; status: FileIndexMeta["status"]; mode: "tree" | "fuzzy" } | null>(
    null,
  )
  const fileMenuRowsRef = useRef<FileMenuRow[]>([])
  const filePickerLoadSeq = useRef(0)
  const [filePickerDismissed, setFilePickerDismissed] = useState<{ tokenStart: number; textVersion: number } | null>(
    null,
  )
  const [filePickerNeedle, setFilePickerNeedle] = useState("")
  const filePickerNeedleSeq = useRef(0)
  const fileIndexRef = useRef<FileIndexStore>({
    generation: 0,
    running: false,
    visited: new Set<string>(),
    queue: [],
    files: new Map<string, SessionFileInfo>(),
    dirs: new Map<string, SessionFileInfo>(),
    lastMetaUpdateMs: 0,
  })
  const [fileIndexMeta, setFileIndexMeta] = useState<FileIndexMeta>({
    status: "idle",
    fileCount: 0,
    dirCount: 0,
    scannedDirs: 0,
    queuedDirs: 0,
    truncated: false,
    message: undefined,
    version: 0,
  })

  const fileMenuMaxRows = useMemo(() => {
    if (claudeChrome) {
      return Math.max(8, Math.min(16, Math.floor(rowCount * 0.5)))
    }
    return 8
  }, [claudeChrome, rowCount])

  const activeAtMention = useMemo(
    () => (inputLocked ? null : findActiveAtMention(input, cursor)),
    [cursor, input, inputLocked],
  )
  const activeAtMentionQuery = activeAtMention?.query ?? ""
  const filePickerQueryParts = useMemo(
    () => (activeAtMention ? parseAtMentionQuery(activeAtMentionQuery) : { cwd: ".", needle: "" }),
    [activeAtMention, activeAtMentionQuery],
  )
  const rawFilePickerNeedle = filePickerQueryParts.needle.trim()
  const filePickerActive =
    activeAtMention != null &&
    !activeAtCommand &&
    (!filePickerDismissed ||
      filePickerDismissed.tokenStart !== activeAtMention.start ||
      filePickerDismissed.textVersion !== inputTextVersion)

  const filePickerResourcesVisible = useMemo(() => {
    if (!claudeChrome || !filePickerActive) return [] as FilePickerResource[]
    if (filePickerQueryParts.cwd !== ".") return [] as FilePickerResource[]
    const needle = filePickerQueryParts.needle.trim().toLowerCase()
    if (!needle) return [...filePickerResources]
    return filePickerResources.filter((entry) => entry.label.toLowerCase().includes(needle))
  }, [claudeChrome, filePickerActive, filePickerQueryParts.cwd, filePickerQueryParts.needle, filePickerResources])

  useEffect(() => {
    if (!filePickerActive || filePickerConfig.mode === "tree") {
      setFilePickerNeedle("")
      return
    }
    const trimmed = filePickerQueryParts.needle.trim()
    if (!trimmed) {
      setFilePickerNeedle("")
      return
    }
    if (trimmed === filePickerNeedle) return
    const seq = (filePickerNeedleSeq.current += 1)
    const timer = setTimeout(() => {
      if (filePickerNeedleSeq.current !== seq) return
      setFilePickerNeedle(trimmed)
    }, 80)
    return () => clearTimeout(timer)
  }, [filePickerActive, filePickerConfig.mode, filePickerNeedle, filePickerQueryParts.needle])

  useEffect(() => {
    filePickerIndexRef.current = filePicker.index
  }, [filePicker.index])

  useEffect(() => {
    if (!activeAtMention) {
      setFilePickerDismissed(null)
    }
  }, [activeAtMention])

  const closeFilePicker = useCallback(() => {
    setFilePicker((prev) =>
      prev.status === "hidden"
        ? prev
        : {
            status: "hidden",
            cwd: ".",
            items: [],
            index: 0,
          },
    )
  }, [])

  const shouldIndexDirectory = useCallback(
    (dirPath: string): boolean => {
      const name = dirPath.split("/").filter(Boolean).pop() ?? dirPath
      if (!name) return true
      if (name === ".git") return false
      if (!filePickerConfig.indexNodeModules && name === "node_modules") return false
      if (name === ".venv" || name === "__pycache__") return false
      if (!filePickerConfig.indexHiddenDirs && name.startsWith(".") && name !== ".github") return false
      return true
    },
    [filePickerConfig.indexHiddenDirs, filePickerConfig.indexNodeModules],
  )

  const ensureFileIndexScan = useCallback(() => {
    if (filePickerConfig.mode === "tree") return
    if (fileIndexMeta.status === "ready") return
    const store = fileIndexRef.current
    if (store.running) return

    if (fileIndexMeta.status === "error") {
      store.visited.clear()
      store.queue.length = 0
      store.files.clear()
      store.dirs.clear()
    }

    store.running = true
    store.generation += 1
    const generation = store.generation
    store.lastMetaUpdateMs = 0

    if (store.queue.length === 0 && store.visited.size === 0) {
      store.queue.push(".")
    } else if (!store.visited.has(".") && !store.queue.includes(".")) {
      store.queue.push(".")
    }

    const updateMeta = (force: boolean, statusOverride?: FileIndexMeta["status"], message?: string) => {
      if (fileIndexRef.current.generation !== generation) return
      const now = Date.now()
      if (!force && now - store.lastMetaUpdateMs < 250) return
      store.lastMetaUpdateMs = now
      const truncated = store.files.size >= filePickerConfig.maxIndexFiles
      setFileIndexMeta((prev) => ({
        status: statusOverride ?? prev.status,
        fileCount: store.files.size,
        dirCount: store.dirs.size,
        scannedDirs: store.visited.size,
        queuedDirs: store.queue.length,
        truncated,
        message,
        version: prev.version + 1,
      }))
    }

    setFileIndexMeta((prev) => ({
      ...prev,
      status: "scanning",
      message: undefined,
      queuedDirs: store.queue.length,
      scannedDirs: store.visited.size,
      fileCount: store.files.size,
      dirCount: store.dirs.size,
      truncated: store.files.size >= filePickerConfig.maxIndexFiles,
      version: prev.version + 1,
    }))

    const maxFiles = filePickerConfig.maxIndexFiles
    const concurrency = Math.max(1, Math.min(16, Math.floor(filePickerConfig.indexConcurrency)))

    const worker = async () => {
      while (fileIndexRef.current.generation === generation) {
        const cwd = store.queue.shift()
        if (!cwd) return
        if (store.visited.has(cwd)) continue
        store.visited.add(cwd)
        try {
          const scope = cwd === "." ? undefined : cwd
          const entries = await onListFiles(scope)
          for (const entry of entries) {
            if (entry.type === "directory") {
              if (!shouldIndexDirectory(entry.path)) continue
              if (!store.dirs.has(entry.path)) {
                store.dirs.set(entry.path, entry)
              }
              if (!store.visited.has(entry.path)) {
                store.queue.push(entry.path)
              }
              continue
            }
            if (!store.files.has(entry.path)) {
              store.files.set(entry.path, entry)
            }
            if (store.files.size >= maxFiles) {
              store.queue.length = 0
              break
            }
          }
        } catch (error) {
          if (cwd === ".") {
            throw error
          }
        } finally {
          updateMeta(false, "scanning")
        }
      }
    }

    void (async () => {
      try {
        const workers = Array.from({ length: concurrency }, () => worker())
        await Promise.all(workers)
        if (fileIndexRef.current.generation !== generation) return
        store.running = false
        updateMeta(true, "ready")
      } catch (error) {
        if (fileIndexRef.current.generation !== generation) return
        store.running = false
        const message = (error as Error).message || String(error)
        updateMeta(true, "error", message)
      }
    })()
  }, [
    fileIndexMeta.status,
    filePickerConfig.indexConcurrency,
    filePickerConfig.maxIndexFiles,
    filePickerConfig.mode,
    onListFiles,
    shouldIndexDirectory,
  ])

  const fuzzyNeedle = filePickerNeedle
  const lastFuzzyNeedleRef = useRef<string>("")
  useEffect(() => {
    if (!filePickerActive) {
      lastFuzzyNeedleRef.current = ""
      return
    }
    if (filePickerConfig.mode === "tree") {
      lastFuzzyNeedleRef.current = ""
      return
    }
    if (!fuzzyNeedle) return
    if (lastFuzzyNeedleRef.current === fuzzyNeedle) return
    lastFuzzyNeedleRef.current = fuzzyNeedle
    ensureFileIndexScan()
  }, [ensureFileIndexScan, filePickerActive, filePickerConfig.mode, fuzzyNeedle])

  const loadFilePickerDirectory = useCallback(
    async (cwd: string) => {
      const seq = (filePickerLoadSeq.current += 1)
      setFilePicker((prev) => ({
        status: "loading",
        cwd,
        items: prev.status === "hidden" ? [] : prev.items,
        index: 0,
      }))
      try {
        const scope = cwd === "." ? undefined : cwd
        const items = await onListFiles(scope)
        if (filePickerLoadSeq.current !== seq) return
        setFilePicker({
          status: "ready",
          cwd,
          items,
          index: 0,
        })
      } catch (error) {
        if (filePickerLoadSeq.current !== seq) return
        setFilePicker({
          status: "error",
          cwd,
          items: [],
          index: 0,
          message: (error as Error).message,
        })
      }
    },
    [onListFiles],
  )

  useEffect(() => {
    if (!filePickerActive) {
      closeFilePicker()
      return
    }
    if (filePicker.status === "hidden" || filePicker.cwd !== filePickerQueryParts.cwd) {
      void loadFilePickerDirectory(filePickerQueryParts.cwd)
    }
  }, [closeFilePicker, filePicker.status, filePicker.cwd, filePickerActive, filePickerQueryParts.cwd, loadFilePickerDirectory])

  const filePickerFilteredItems = useMemo(() => {
    if (!filePickerActive) return []
    const normalized = filePickerQueryParts.needle.trim().toLowerCase()
    const filtered = normalized
      ? filePicker.items.filter((item) => displayPathForCwd(item.path, filePicker.cwd).toLowerCase().includes(normalized))
      : [...filePicker.items]
    filtered.sort((a, b) => {
      if (a.type !== b.type) return a.type === "directory" ? -1 : 1
      const aDisplay = displayPathForCwd(a.path, filePicker.cwd).toLowerCase()
      const bDisplay = displayPathForCwd(b.path, filePicker.cwd).toLowerCase()
      return aDisplay.localeCompare(bDisplay)
    })
    return filtered
  }, [filePicker.cwd, filePicker.items, filePickerActive, filePickerQueryParts.needle])

  const filePickerIndex = useMemo(() => {
    if (!filePickerActive) return 0
    if (filePickerFilteredItems.length === 0) return 0
    return Math.max(0, Math.min(filePicker.index, filePickerFilteredItems.length - 1))
  }, [filePicker.index, filePickerActive, filePickerFilteredItems.length])

  const fileIndexItems = useMemo<SessionFileInfo[]>(() => {
    const store = fileIndexRef.current
    return [...store.dirs.values(), ...store.files.values()]
  }, [fileIndexMeta.version])

  const fileMenuMode = useMemo<"tree" | "fuzzy">(() => {
    if (!filePickerActive) return "tree"
    if (filePickerConfig.mode === "tree") return "tree"
    if (rawFilePickerNeedle.length === 0) return "tree"
    return "fuzzy"
  }, [filePickerActive, filePickerConfig.mode, rawFilePickerNeedle])

  const fileMenuNeedle = fileMenuMode === "fuzzy" ? filePickerNeedle : filePickerQueryParts.needle
  const fileMenuNeedlePending =
    fileMenuMode === "fuzzy" && rawFilePickerNeedle.length > 0 && rawFilePickerNeedle !== filePickerNeedle

  useEffect(() => {
    if (!filePickerActive) {
      if (fileMenuItems.length > 0) {
        setFileMenuItems([])
      }
      fileMenuCacheRef.current = null
      return
    }
    if (fileMenuMode === "tree") {
      setFileMenuItems([...filePickerFilteredItems])
      fileMenuCacheRef.current = {
        key: `${filePickerQueryParts.cwd}|${fileMenuNeedle}`,
        status: fileIndexMeta.status,
        mode: "tree",
      }
      return
    }
    if (fileMenuNeedlePending) {
      return
    }
    const key = `${filePickerQueryParts.cwd}|${fileMenuNeedle}`
    const previous = fileMenuCacheRef.current
    const shouldRefresh =
      !previous ||
      previous.mode !== "fuzzy" ||
      previous.key !== key ||
      (previous.status !== "ready" && fileIndexMeta.status === "ready")
    if (!shouldRefresh) return
    const cwd = filePickerQueryParts.cwd
    const prefix = cwd === "." ? "" : `${cwd}/`
    const candidates = prefix
      ? fileIndexItems.filter((item) => item.path === cwd || item.path.startsWith(prefix))
      : fileIndexItems
    const ranked = rankFuzzyFileItems(
      candidates,
      fileMenuNeedle,
      filePickerConfig.maxResults,
      (item) => displayPathForCwd(item.path, cwd),
    )
    setFileMenuItems([...ranked])
    fileMenuCacheRef.current = { key, status: fileIndexMeta.status, mode: "fuzzy" }
  }, [
    fileIndexItems,
    fileIndexMeta.status,
    fileMenuItems.length,
    fileMenuMode,
    filePickerActive,
    filePickerConfig.maxResults,
    filePickerFilteredItems,
    filePickerQueryParts.cwd,
    fileMenuNeedle,
    fileMenuNeedlePending,
  ])

  const fileMenuRows = useMemo<FileMenuRow[]>(() => {
    if (!filePickerActive) return []
    const rows: FileMenuRow[] = []
    for (const resource of filePickerResourcesVisible) {
      rows.push({ kind: "resource", resource })
    }
    for (const item of fileMenuItems) {
      rows.push({ kind: "file", item })
    }
    return rows
  }, [filePickerActive, fileMenuItems, filePickerResourcesVisible])

  useEffect(() => {
    fileMenuRowsRef.current = fileMenuRows
  }, [fileMenuRows])

  const fileMenuHasLarge = useMemo(
    () =>
      fileMenuRows.some(
        (row) =>
          row.kind === "file" && row.item.size != null && row.item.size > fileMentionConfig.maxInlineBytesPerFile,
      ),
    [fileMenuRows, fileMentionConfig.maxInlineBytesPerFile],
  )

  const fileMenuIndex = useMemo(() => {
    if (!filePickerActive) return 0
    if (fileMenuRows.length === 0) return 0
    return Math.max(0, Math.min(filePicker.index, fileMenuRows.length - 1))
  }, [fileMenuRows.length, filePicker.index, filePickerActive])

  const selectedFileRow = useMemo(() => {
    if (!filePickerActive) return null
    if (fileMenuRows.length === 0) return null
    return fileMenuRows[fileMenuIndex] ?? null
  }, [fileMenuIndex, fileMenuRows, filePickerActive])

  const selectedFileIsLarge = useMemo(() => {
    if (!selectedFileRow || selectedFileRow.kind !== "file") return false
    const size = selectedFileRow.item.size
    return size != null && size > fileMentionConfig.maxInlineBytesPerFile
  }, [fileMentionConfig.maxInlineBytesPerFile, selectedFileRow])

  const fileMenuWindow = useMemo(() => {
    if (fileMenuRows.length === 0) {
      return { items: [] as FileMenuRow[], hiddenAbove: 0, hiddenBelow: 0, start: 0, lineCount: 0 }
    }
    const maxRows = fileMenuMaxRows
    if (fileMenuRows.length <= maxRows) {
      const lineCount = fileMenuRows.reduce(
        (sum, row) => sum + (row.kind === "resource" && row.resource.detail ? 2 : 1),
        0,
      )
      return { items: fileMenuRows, hiddenAbove: 0, hiddenBelow: 0, start: 0, lineCount }
    }
    const half = Math.floor(maxRows / 2)
    const start = Math.min(
      Math.max(0, fileMenuIndex - half),
      Math.max(0, fileMenuRows.length - maxRows),
    )
    const items = fileMenuRows.slice(start, start + maxRows)
    const lineCount = items.reduce(
      (sum, row) => sum + (row.kind === "resource" && row.resource.detail ? 2 : 1),
      0,
    )
    return {
      items,
      hiddenAbove: start,
      hiddenBelow: Math.max(0, fileMenuRows.length - (start + items.length)),
      start,
      lineCount,
    }
  }, [fileMenuIndex, fileMenuMaxRows, fileMenuRows])

  const lastFileMenuNeedleRef = useRef<string>("")
  useEffect(() => {
    if (!filePickerActive) return
    if (fileMenuNeedle === lastFileMenuNeedleRef.current) return
    lastFileMenuNeedleRef.current = fileMenuNeedle
    setFilePicker((prev) => (prev.status === "hidden" ? prev : { ...prev, index: 0 }))
  }, [fileMenuNeedle, filePickerActive])

  const insertFileMention = useCallback(
    (filePath: string, activeMention: ActiveAtMention) => {
      const mentionToken = /\s/.test(filePath) ? `@"${filePath}"` : `@${filePath}`
      if (!fileMentionConfig.insertPath) {
        const before = input.slice(0, activeMention.start)
        const after = input.slice(activeMention.end)
        const trimmedAfter = before.endsWith(" ") && after.startsWith(" ") ? after.slice(1) : after
        const nextValue = `${before}${trimmedAfter}`
        handleLineEdit(nextValue, before.length)
        return
      }
      const before = input.slice(0, activeMention.start)
      const after = input.slice(activeMention.end)
      const trail = after.length === 0 || !/^\s/.test(after) ? " " : ""
      const inserted = `${mentionToken}${trail}`
      const nextValue = `${before}${inserted}${after}`
      handleLineEdit(nextValue, before.length + inserted.length)
    },
    [fileMentionConfig.insertPath, handleLineEdit, input],
  )

  const insertDirectoryMention = useCallback(
    (dirPath: string, activeMention: ActiveAtMention) => {
      const normalized = dirPath.replace(/\/+$/, "")
      const withSlash = normalized ? `${normalized}/` : `${dirPath}/`
      const mentionToken = /\s/.test(withSlash) ? `@"${withSlash}"` : `@${withSlash}`
      const before = input.slice(0, activeMention.start)
      const after = input.slice(activeMention.end)
      const nextValue = `${before}${mentionToken}${after}`
      handleLineEdit(nextValue, before.length + mentionToken.length)
    },
    [handleLineEdit, input],
  )

  const insertResourceMention = useCallback(
    (resource: FilePickerResource, activeMention: ActiveAtMention) => {
      const label = resource.label.trim()
      const tokenBody = /\s/.test(label) ? `resource:\"${label}\"` : `resource:${label}`
      const mentionToken = `@${tokenBody}`
      const before = input.slice(0, activeMention.start)
      const after = input.slice(activeMention.end)
      const trail = after.length === 0 || !/^\s/.test(after) ? " " : ""
      const inserted = `${mentionToken}${trail}`
      const nextValue = `${before}${inserted}${after}`
      handleLineEdit(nextValue, before.length + inserted.length)
    },
    [handleLineEdit, input],
  )

  const queueFileMention = useCallback(
    (file: SessionFileInfo) => {
      if (fileMentionConfig.mode === "reference") return
      const now = Date.now()
      setFileMentions((prev) => {
        const existingIndex = prev.findIndex((entry) => entry.path === file.path)
        const next = existingIndex >= 0 ? prev.filter((_, idx) => idx !== existingIndex) : [...prev]
        next.push({
          id: `file-mention-${now.toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
          path: file.path,
          size: file.size ?? null,
          requestedMode: fileMentionConfig.mode,
          addedAt: now,
        })
        return next
      })
    },
    [fileMentionConfig.mode],
  )

  return {
    fileMentions,
    setFileMentions,
    filePicker,
    setFilePicker,
    filePickerIndexRef,
    fileMenuRowsRef,
    filePickerDismissed,
    setFilePickerDismissed,
    filePickerNeedle,
    fileIndexMeta,
    fileMenuMaxRows,
    activeAtMention,
    filePickerQueryParts,
    rawFilePickerNeedle,
    filePickerActive,
    filePickerResourcesVisible,
    filePickerFilteredItems,
    filePickerIndex,
    fileIndexItems,
    fileMenuMode,
    fileMenuNeedle,
    fileMenuNeedlePending,
    fileMenuRows,
    fileMenuHasLarge,
    fileMenuIndex,
    selectedFileRow,
    selectedFileIsLarge,
    fileMenuWindow,
    closeFilePicker,
    ensureFileIndexScan,
    loadFilePickerDirectory,
    insertFileMention,
    insertDirectoryMention,
    insertResourceMention,
    queueFileMention,
  }
}
