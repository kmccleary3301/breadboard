OpenCode-identical tools

This directory defines YAML tools that mirror OpenCode tool ids, parameter names, and behavior contracts:
 - bash, read, write, list, glob, grep, edit, patch

Use by setting in config:

tools:
  defs_dir: implementations/tools/defs_oc
provider_tools:
  use_native: true
  suppress_prompts: true



