# @breadboard/host-kits

`@breadboard/host-kits` is the first reusable host-integration abstraction package for the V3 backbone program.

It standardizes:
- request classification
- support claims
- invocation results
- fallback-vs-supported mode reporting

This package does not own host-specific request schemas by itself. It provides the reusable abstraction layer that concrete host kits, such as OpenClaw, should implement.
