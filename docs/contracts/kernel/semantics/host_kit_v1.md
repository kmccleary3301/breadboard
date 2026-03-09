# Host Kit v1

## Purpose

Host Kit is the standard embedding abstraction for serious TS hosts.

OpenClaw proved the seam. V3 should generalize that seam into a reusable standard rather than continuing with one-off bridges.

## Expected responsibilities

A Host Kit should:
- classify support for a host request
- map host input into BreadBoard runtime entrypoints
- map kernel events into host callback or stream shapes
- build transcript continuation patches suitable for host-owned persistence
- make fallback decisions explicit
- surface support claims

## Relationship to Backbone

Backbone is the public TS runtime surface.
Host Kit is the host-specific adaptation layer above Backbone.

## Current state

The repo has a real OpenClaw proving-ground seam, but not yet a generalized Host Kit package.
That is the next intended widening step after the Backbone and Workspace foundations are in place.
