import { useMemo } from "react"
import { parseUnifiedDiff } from "./diffParser"

type DiffViewerProps = {
  text: string
  mode: "unified" | "side-by-side"
  onCopyHunk?: (text: string) => void
  onCopyPath?: (path: string) => void
  onOpenPath?: (path: string) => void
}

export default function DiffViewer({ text, mode, onCopyHunk, onCopyPath, onOpenPath }: DiffViewerProps) {
  const parsed = useMemo(() => parseUnifiedDiff(text, 4000), [text])

  return (
    <div className="diffViewer">
      {parsed.files.map((file) => (
        <article key={`${file.oldPath}:${file.newPath}`} className="diffFile">
          <header className="diffFileHeader">
            <code>{file.displayPath}</code>
            <span>
              +{file.additions} / -{file.removals}
            </span>
            {onCopyPath && file.displayPath !== "raw" ? (
              <button type="button" onClick={() => onCopyPath(file.displayPath)}>
                Copy Path
              </button>
            ) : null}
            {onOpenPath && file.displayPath !== "raw" ? (
              <button type="button" onClick={() => onOpenPath(file.displayPath)}>
                Open
              </button>
            ) : null}
          </header>
          {file.hunks.map((hunk) => {
            const hunkText = [hunk.header, ...hunk.lines.map((line) => `${line.type === "add" ? "+" : line.type === "remove" ? "-" : " "}${line.text}`)].join(
              "\n",
            )
            return (
              <section key={`${file.displayPath}:${hunk.header}`} className="diffHunk">
                <div className="diffHunkHeader">
                  <code>{hunk.header}</code>
                  {onCopyHunk ? (
                    <button type="button" onClick={() => onCopyHunk(hunkText)}>
                      Copy Hunk
                    </button>
                  ) : null}
                </div>
                <div className={mode === "side-by-side" ? "diffLines sideBySide" : "diffLines unified"}>
                  {hunk.lines.map((line, index) => (
                    <div key={`${hunk.header}:${index}`} className={`diffLine ${line.type}`}>
                      <span className="diffNums">
                        <em>{line.oldNumber ?? ""}</em>
                        <em>{line.newNumber ?? ""}</em>
                      </span>
                      <code>{line.text}</code>
                    </div>
                  ))}
                </div>
              </section>
            )
          })}
        </article>
      ))}
      {parsed.truncated ? <p className="subtle">Diff truncated for UI safety.</p> : null}
      {parsed.malformed ? <p className="subtle">Malformed diff detected; fallback rendering applied.</p> : null}
    </div>
  )
}
