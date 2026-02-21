import { useState } from "react"
import { StreamingMarkdown } from "stream-mdx"

type MarkdownMessageProps = {
  text: string
}

export default function MarkdownMessage({ text }: MarkdownMessageProps) {
  const [errored, setErrored] = useState(false)

  if (errored) {
    return <pre>{text}</pre>
  }

  return (
    <div className="bbMarkdown">
      <StreamingMarkdown
        text={text}
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
    </div>
  )
}
