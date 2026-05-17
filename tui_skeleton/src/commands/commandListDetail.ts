export const renderSavedToLine = (subject: string, target: string): string => `${subject} saved to ${target}`

export const renderActionToLine = (action: string, subject: string, target: string): string => `${action} ${subject} to ${target}`

export const renderActionFromLine = (action: string, subject: string, source: string): string => `${action} ${subject} from ${source}`

export const renderActionArrowLine = (action: string, subject: string, target: string): string => `${action} ${subject} -> ${target}`

export const renderActionItemsLine = (action: string, items: readonly string[], fallback: string): string =>
  items.length > 0 ? `${action} ${items.join(", ")}` : fallback
