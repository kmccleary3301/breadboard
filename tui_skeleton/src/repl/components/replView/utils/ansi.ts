export const stripAnsiCodes = (value: string): string => value.replace(/\u001B\[[0-9;]*m/g, "")
