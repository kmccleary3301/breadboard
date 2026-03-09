# @breadboard/host-t3

`@breadboard/host-t3` is the first thin-host DX proving-ground package for the V3 backbone program.

It is intentionally small:
- compose `@breadboard/backbone`
- compose `@breadboard/workspace`
- compose `@breadboard/transport-ai-sdk`
- give T3 Code-class hosts a one-package prompt-turn integration surface

Current scope:
- supported provider-backed prompt turns
- AI SDK-style transport projection
- transcript-continuation-aware prompt execution

This package exists to prove that BreadBoard can be attractive for thin TypeScript hosts without exposing kernel substrate details directly.
