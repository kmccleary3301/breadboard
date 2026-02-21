import { useMemo, useState } from "react"
import { StreamingMarkdown } from "stream-mdx"
import { splitStableMarkdownForStreaming } from "./markdownStability"
import { hasUnsafeMarkdownContent } from "./markdownSecurity"

type MarkdownMessageProps = {
  text: string
  final: boolean
}

export default function MarkdownMessage({ text, final }: MarkdownMessageProps) {
  const [errored, setErrored] = useState(false)
  const unsafe = useMemo(() => hasUnsafeMarkdownContent(text), [text])
  const split = useMemo(() => (final ? { stablePrefix: text, unstableTail: "" } : splitStableMarkdownForStreaming(text)), [final, text])

  if (errored || unsafe) {
    return <pre>{text}</pre>
  }

  if (!split.stablePrefix && split.unstableTail) {
    return <pre>{split.unstableTail}</pre>
  }

  return (
    <div className="bbMarkdown">
      <StreamingMarkdown
        text={split.stablePrefix}
        worker="/workers/markdown-worker.js"
        features={{
          mdx: false,
          html: false,
          tables: true,
          footnotes: true,
          callouts: true,
          formatAnticipation: true,
        }}
        onError={() => setErrored(true)}
      />
      {split.unstableTail ? <pre className="bbMarkdownTail">{split.unstableTail}</pre> : null}
    </div>
  )
}
