import { renderLabeledLine } from "./commandText.js"

export const renderCreatedPathLine = (target: string): string => `Created ${target}`

export const renderStoppedSessionLine = (sessionId: string): string => `Stopped session ${sessionId}`

export const renderEngineStartedLine = (baseUrl: string, pid: number): string => `Engine started: ${baseUrl} (pid ${pid})`

export const renderEngineLogsLine = (logPath: string): string => renderLabeledLine("Logs", logPath)

export const renderEngineServeLine = (baseUrl: string, pid: number | null | undefined, started: boolean): string =>
  `Engine: ${baseUrl}${pid ? ` (pid ${pid})` : ""}${started ? " [started]" : ""}`

export const renderPressCtrlCToStopLine = (): string => "Press Ctrl-C to stop."

export const renderEngineShutdownLine = (stopped: boolean): string => (stopped ? "Engine stopped." : "Engine stop requested.")

export const renderStoppedEngineLine = (pid: number | null | undefined): string =>
  `Stopped engine${pid ? ` (pid ${pid})` : ""}.`

export const renderNoManagedEngineStoppedLine = (pid: number | null | undefined): string =>
  `No managed engine stopped${pid ? ` (pid ${pid})` : ""}.`
