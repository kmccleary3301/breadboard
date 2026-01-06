Call the Task tool exactly once, with the following arguments:
- description: "List repo root"
- prompt: "List files at the repository root and provide a concise summary of the results (files vs directories, notable items like README/LICENSE)."
- subagent_type: "reviewer"

After the Task tool returns, respond with a concise plain-text summary based only on its output. Do not call Task again. Do not use any other tools. Do not output diffs, patches, or code fences. Do not output the phrase "TASK COMPLETE".
