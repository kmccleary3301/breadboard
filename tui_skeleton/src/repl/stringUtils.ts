export const stripAnsi = (value: string): string => value.replace(/\u001b\[[0-9;]*m/g, "")
