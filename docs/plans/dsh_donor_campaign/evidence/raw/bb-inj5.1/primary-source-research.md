# Primary-source research: bb-inj5.1

## Scope and evidence rule

Retrieval date: **2026-09-02**. The research records source identity, revision, runtime assumptions, archive retrieval, and reuse terms. It does not extract donor items or make a BreadBoard disposition.

`OBSERVED` means the cited first-party repository, release API, package metadata, documentation, or license file states the fact. `INFERENCE` means this note derives a conservative handling rule from observed terms. `NO_PRIMARY_ANCHOR` marks a claim that the available material does not pin.

## 1. Planner clues, not source truth

`DSH_MINING_PLANNER_CONVO.md` calls the attached snapshot `0.1.0-rc.8` and names the archive `deepseek-harness-master.zip`; it also names `packages/mcp/mcp-client/README.md` as a source path. Those are local claim-index clues only, not evidence of identity, revision, or license: [NO_PRIMARY_ANCHOR].

The conversation also names Cordis, AURA, and OMP as comparison material. The DSH source gives a first-party Cordis URL, but the conversation gives no first-party URL or revision for AURA or OMP. Those two comparison names remain outside this DSH pin: [NO_PRIMARY_ANCHOR].

## 2. Identity and exact pin

### DeepSeek Harness

- **OBSERVED:** The first-party README identifies **DeepSeek Harness (`dsh`)**, developed by DeepSeek AI, and gives the source repository `https://github.com/deepseek-ai/deepseek-harness`. It gives the documentation site `https://deepseek-harness.github.io/deepseek-harness/`, the source checkout procedure, and the npm command `npx @deepseek-ai/dsh web`: [A1].
- **OBSERVED:** At commit `141eb6fef83422698aef7a981029e843e8161534`, the root manifest names `@deepseek-ai/dsh-root`, sets version `0.1.0-rc.8`, declares ESM (`"type": "module"`), pins `pnpm@11.7.0`, and sets the Node engine to `^22.19.0 || >=24.0.0`: [A2].
- **OBSERVED:** The release API identifies immutable release tag `dsh-v0.1.0-rc.8`, version name `v0.1.0-rc.8`, release publication time `2026-08-19T15:37:57Z`, and generated source `zipball_url` and `tarball_url`; the release has no uploaded assets: [A3].
- **OBSERVED:** The tag ref points directly to commit `141eb6fef83422698aef7a981029e843e8161534`. The commit message is `release: dsh@0.1.0-rc.8`; GitHub reports the commit as unsigned: [A4], [A5].

**Exact campaign source pin:** use repository `deepseek-ai/deepseek-harness`, tag `dsh-v0.1.0-rc.8`, commit `141eb6fef83422698aef7a981029e843e8161534`. This pin matches the version named by the local archive clue and avoids treating a mutable `master` archive as the donor source: [A1], [A2], [A3], [A4].

### Published CLI package

- **OBSERVED:** The source manifest at `apps/cli/package.json` names package `@deepseek-ai/dsh`, version `0.1.0-rc.8`, repository directory `apps/cli`, ESM type, executable `dsh: lib/bin.js`, published files `lib/*.js` and `config`, and MIT license: [A6].
- **OBSERVED:** npm publishes `@deepseek-ai/dsh@0.1.0-rc.8` with repository `git+https://github.com/deepseek-ai/deepseek-harness.git`, directory `apps/cli`, npm tarball URL, SHA-1 `61bb2c44f1279329b128d47068240c36b32afa05`, and integrity `sha512-VQU5NlomrKLRgcXuOf+sxWFvqxPA8q9vMhrKPlPPXiOJEhGlGlAdiyxZvZxkCVI+v0zbhe21cY3/luLyxpSzzA==`: [A7].

The npm CLI tarball is a built distribution, not the full source checkout. Source-level reuse must therefore use the pinned Git revision rather than infer source contents from the package tarball: [A6], [A7].

### MCP package named by the planner

- **OBSERVED:** The pinned package manifest names `@deepseek-ai/dsh-mcp-client`, version `0.1.0-rc.8`, repository directory `packages/mcp/mcp-client`, ESM entry `lib/index.js`, declaration entry `lib/types/index.d.ts`, and MIT license. Its runtime dependencies are `@modelcontextprotocol/sdk`, `@deepseek-ai/schemastery`, and `zod`; its peer dependencies include `@deepseek-ai/cordis` and DSH packages: [A8].
- **OBSERVED:** npm publishes `@deepseek-ai/dsh-mcp-client@0.1.0-rc.8` from the same repository path. npm reports SHA-1 `09767efdfcbb79346e50cf36f49dd9cf27714942`, integrity `sha512-pcqXawjq9jRJQNHZTLN7pswJFMWjxCsEOdHUI9BdJqb9lRDsMi2cul1Hizfi51oKkcNo1u/K9Toqgh8o/ScAXg==`, and Node build version `24.19.0`: [A9].
- **OBSERVED:** The pinned package directory contains `src/connection.ts`, `src/index.ts`, `src/invariant.ts`, `src/tools.ts`, `src/transport.ts`, plus tests including `fixture-server.ts`, `mcp-client.spec.ts`, `mcp-client.e2e.ts`, `apply.spec.ts`, `load-path.spec.ts`, and `reconnect.spec.ts`: [A10], [A11].

The package path in the local clue resolves to this first-party DSH package, but the package archive and the source tree remain separate artifacts: [A8], [A9], [A10], [A11].

## 3. Mutable archive versus pinned archive

- **OBSERVED:** The mutable archive URL `https://github.com/deepseek-ai/deepseek-harness/archive/refs/heads/master.zip` retrieves a ZIP whose root is `deepseek-harness-master/`: [A12].
- **OBSERVED:** The current `master` ref points to commit `4e84901e6471b79ec0338099867ebb4606d12bb5`, and its root manifest reports version `0.1.2-alpha.4`, not `0.1.0-rc.8`: [A13], [A14].
- **INFERENCE:** The filename `deepseek-harness-master.zip` cannot identify the historical attached snapshot. Use the immutable release URL `https://github.com/deepseek-ai/deepseek-harness/archive/refs/tags/dsh-v0.1.0-rc.8.zip` or the API zipball URL from [A3], and record the commit separately: [A3], [A4], [A12], [A13], [A14].

### Retrieval and hashes

The following archives were retrievable on the retrieval date: [A15], [A16], [A17].

| Artifact | Retrieval URL | Observation |
|---|---|---|
| Full source ZIP | `https://github.com/deepseek-ai/deepseek-harness/archive/refs/tags/dsh-v0.1.0-rc.8.zip` | Archive reader returned root `deepseek-harness-dsh-v0.1.0-rc.8/`; observed SHA-256 of the redirected response bytes: `184bec8e7818440e5daebd68ffa2d418d16fd08136c28e88febaac942205501e`: [A15]. |
| CLI npm tarball | `https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.1.0-rc.8.tgz` | Archive reader returned root `package/`; observed SHA-256: `b8b0db6f3bcf3aed77c25bb901fdb9d0ef0f79bd8ca403b52e34c14a71d1487f`; npm SHA-1 and SHA-512 integrity appear in [A7]: [A16], [A7]. |
| MCP npm tarball | `https://registry.npmjs.org/@deepseek-ai/dsh-mcp-client/-/dsh-mcp-client-0.1.0-rc.8.tgz` | Archive reader returned root `package/`; observed SHA-256: `db4548b72c3e30e3217919b5386dc177fc0ed2a5a4d12f852e18a782a81efca0`; npm SHA-1 and SHA-512 integrity appear in [A9]: [A17], [A9]. |

The supervisor should hash the exact downloaded byte stream for the selected artifact, retain the final URL after redirects, and record the pinned commit/tag separately. For the npm artifacts, the supervisor should also compare the byte hash with npm's published integrity metadata. This is a provenance procedure based on the retrievable archive and npm metadata, not a new license term: [A3], [A7], [A9], [A15], [A16], [A17].

### Raw-store provenance

- **OBSERVED:** The GitHub tag archive was preserved for supervisor review at `docs_tmp/bb_direction_assessment/dsh_donor_campaign/raw/bb-inj5.1/deepseek-harness-dsh-v0.1.0-rc.8.tar.gz`.
- **OBSERVED:** The preserved file's SHA-256 is `f232ba127ad9120308436655c7c89ed1c81680c8eda0ff70d22c86c4331dfbdc`. The source anchor is GitHub's generated tag tarball URL `https://api.github.com/repos/deepseek-ai/deepseek-harness/tarball/dsh-v0.1.0-rc.8`, also exposed as `tarball_url` by the pinned release API: [A3], [A33].
- **INFERENCE:** The supervisor should hash the preserved bytes, not a later `master` archive or an unpinned package resolution, and should retain the source URL, tag, commit, retrieval date, and hash together: [A3], [A4], [A13], [A15], [A33].

## 4. Runtime and platform assumptions

- **OBSERVED:** DSH is an ESM TypeScript/Node workspace. The root requires Node `^22.19.0 || >=24.0.0`, Corepack-enabled pnpm `11.7.0`, and source setup uses `pnpm install`, `pnpm run build`, and `pnpm dsh web`: [A1], [A2], [A18].
- **OBSERVED:** The development guide lists Git `2.26+` as a prerequisite, optional `DEEPSEEK_API_KEY` for API-backed demos, and `DEEPSEEK_BASE_URL` as an optional public-API override. Credentials must not enter copied fixtures or commits: [A18].
- **OBSERVED:** The MCP package is ESM and its published metadata was built with Node `24.19.0`; its manifest declares a peer dependency graph that consumers must resolve: [A8], [A9].
- **OBSERVED:** The first-party Landlock launcher is a separate BSD-3-Clause package. Its entry package requires Node `>=20`; platform packages exist only for Linux `x64` and `arm64`, and the launcher requires a Linux kernel with Landlock enabled, kernel 5.13+ for the documented support floor. The source intentionally provides no macOS or Windows platform package: [A19], [A20], [A21].
- **OBSERVED:** The Python SDK has a separate package identity, `deepseek-harness-sdk`, version `0.0.0.dev0`, Python requirement `>=3.10`, MIT license, and a dependency on the pinned runtime-bin package: [A22], [A23].

**INFERENCE:** A direct TypeScript/Node implementation reuse needs the DSH ESM/module-resolution and peer-provider assumptions. A Python port can reuse documented behavior only after an independent Python implementation; the Python SDK package is not evidence that TypeScript source can run in Python: [A2], [A8], [A18], [A22].

## 5. Primary license and notice evidence

- **OBSERVED:** The DSH root license is MIT, copyright `(c) 2026 DeepSeek`. It grants the right “to deal in the Software without restriction,” including “use, copy, modify, merge, publish, distribute, sublicense, and/or sell.” The condition requires the copyright and permission notices in “all copies or substantial portions.” The license disclaims warranty and liability: [A24].
- **OBSERVED:** DSH's third-party notice says: “Each project remains under its own license; nothing in this file changes those terms.” It identifies the MCP SDK and zod as MIT, and identifies the vendored Cordis family as MIT with preserved upstream license files: [A25].
- **OBSERVED:** The DSH brand guidelines state that “DeepSeek Harness” is a registered trademark, permit truthful relationship descriptions such as “built on DeepSeek Harness” or “compatible with DeepSeek Harness,” and caution against names or promotions that imply official endorsement: [A26].
- **OBSERVED:** The pinned DSH MCP README is repository documentation, while its source and test files are separate repository artifacts at the same pinned revision: [A27], [A10], [A11].

### Reuse terms by artifact kind

The policy below is conservative handling guidance, not a legal opinion. Each row separates what the primary sources show from the campaign handling rule.

| Kind | OBSERVED primary evidence | Conservative reuse policy |
|---|---|---|
| **Ideas** | DSH documents and package contracts live at the pinned repository revision; the root license grants rights over the “Software” and associated documentation files: [A1], [A24], [A27]. | Re-express an idea independently and record DSH tag/commit attribution in provenance. Do not copy source wording or implementation merely because the idea appears in documentation. If expression or code moves, use the Implementation or Quotation rule: [A24], [A27]. |
| **Quotation** | README and package docs are DSH repository files; MIT notice obligations apply to copies or substantial portions: [A24], [A27]. | Keep quotations short, exact, and attributed with repository path, pinned revision, and URL. Do not quote third-party text or use DSH branding in a way that implies endorsement. If a quotation becomes a substantial copied portion, preserve the MIT copyright and permission notices: [A24], [A25], [A26]. |
| **Schemas** | The candidate package declares and consumes schemas, but this source review did not pin a specific DSH schema file from the planner clue: [A8], [A27]. | Treat any copied schema text or schema-bearing source as expressive repository material. Copy only after pinning the exact file and checking its provenance; preserve applicable notices. Otherwise independently author an equivalent schema. Exact schema-file copyright/provenance for this task is **NO_PRIMARY_ANCHOR**: [A24], [A25]. |
| **Fixtures** | The pinned MCP package has a first-party `tests/fixture-server.ts` and test suite: [A10], [A11]. The notice file says third-party projects retain their own terms: [A25]. | A DSH-authored fixture may be copied only with the MIT notices retained. Do not copy fixture payloads, provider responses, tokens, or third-party samples until each file's origin and terms are checked. No per-fixture authorship audit was performed here: **NO_PRIMARY_ANCHOR** for any particular payload: [A10], [A11], [A24], [A25]. |
| **Tests** | The package exposes first-party test files at the pinned revision; the package and root declare MIT: [A8], [A10], [A11], [A24]. | Directly copied DSH test code must retain the MIT copyright and permission notices. Reused test dependencies, MCP servers, and payloads keep their own licenses and must be reviewed separately. A behavioral reimplementation avoids copying expression but still needs provenance for copied fixtures: [A24], [A25]. |
| **Implementation** | DSH source files are pinned by repository commit; the CLI and MCP package manifests declare MIT; npm metadata identifies built distributions and dependency/peer graphs: [A4], [A6], [A7], [A8], [A9], [A10]. | MIT permits copying, modifying, publishing, distributing, sublicensing, and selling, subject to retaining the copyright and permission notices in copies or substantial portions. Carry the pinned DSH notice into any copied substantial source, preserve third-party notices, and do not treat npm's semver-resolved dependency graph as source provenance. Do not ship Cordis or native launcher code without its separate notices and terms: [A24], [A25], [A29], [A30]. |

## 6. Cordis provenance and unresolved version boundary

- **OBSERVED:** DSH's vendored-package manifest identifies upstream `cordis` at `https://github.com/cordiverse/cordis`, upstream version `4.0.0-rc.7`, and upstream commit `56b3d4f725681cf4556c1a8695a709cc3b6eed74`. The same table records local modifications and says upstream MIT license files remain in each package: [A28].
- **OBSERVED:** The DSH vendored package at the campaign commit is renamed `@deepseek-ai/cordis`, reports version `4.0.1`, and carries the Shigma MIT license: [A29], [A30]. The upstream commit's own package manifest reports `cordis` version `4.0.0-rc.7` and MIT: [A31], [A32].
- **OBSERVED:** npm metadata for `@deepseek-ai/dsh-mcp-client@0.1.0-rc.8` normalizes the Cordis peer range to `^4.0.1`, while the source workspace manifest uses `workspace:^`: [A8], [A9].

The exact interpretation is: upstream Cordis source is pinned to commit `56b3d4f...` and version `4.0.0-rc.7`; DSH republished a locally modified, scoped package as `@deepseek-ai/cordis` `4.0.1`. The DSH vendor manifest and package metadata do not provide an upstream Cordis commit corresponding to the republished `4.0.1` label: **NO_PRIMARY_ANCHOR** for such a mapping. Any direct Cordis reuse must preserve both the Shigma MIT notice and DSH's documented local-modification boundary: [A28], [A29], [A30], [A31], [A32].

## 7. Conservative decision rule

Use only the pinned DSH repository commit and the exact package metadata above as source identity. Treat `master.zip`, unpinned package ranges, third-party payloads, unspecified schemas, and unspecified fixtures as unpinned. When a file's author, license, or upstream provenance is not identified by a cited primary anchor, do not copy it; mark it **NO_PRIMARY_ANCHOR** and independently reimplement or obtain a separate permission record: [A3], [A7], [A9], [A24], [A25], [A28].

## Primary anchor index

- **[A1]** DSH README at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/README.md
- **[A2]** DSH root manifest at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/package.json
- **[A3]** DSH release API: https://api.github.com/repos/deepseek-ai/deepseek-harness/releases/tags/dsh-v0.1.0-rc.8
- **[A4]** DSH tag ref API: https://api.github.com/repos/deepseek-ai/deepseek-harness/git/ref/tags/dsh-v0.1.0-rc.8
- **[A5]** DSH commit API: https://api.github.com/repos/deepseek-ai/deepseek-harness/commits/141eb6fef83422698aef7a981029e843e8161534
- **[A6]** CLI manifest at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/apps/cli/package.json
- **[A7]** npm CLI metadata: https://registry.npmjs.org/@deepseek-ai/dsh/0.1.0-rc.8
- **[A8]** MCP package manifest at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/packages/mcp/mcp-client/package.json
- **[A9]** npm MCP metadata: https://registry.npmjs.org/@deepseek-ai/dsh-mcp-client/0.1.0-rc.8
- **[A10]** MCP source directory listing at pinned commit: https://api.github.com/repos/deepseek-ai/deepseek-harness/contents/packages/mcp/mcp-client/src?ref=141eb6fef83422698aef7a981029e843e8161534
- **[A11]** MCP test directory listing at pinned commit: https://api.github.com/repos/deepseek-ai/deepseek-harness/contents/packages/mcp/mcp-client/tests?ref=141eb6fef83422698aef7a981029e843e8161534
- **[A12]** Mutable master archive: https://github.com/deepseek-ai/deepseek-harness/archive/refs/heads/master.zip
- **[A13]** Current master ref API: https://api.github.com/repos/deepseek-ai/deepseek-harness/git/ref/heads/master
- **[A14]** Current master root manifest: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/package.json
- **[A15]** Pinned release archive: https://github.com/deepseek-ai/deepseek-harness/archive/refs/tags/dsh-v0.1.0-rc.8.zip
- **[A16]** npm CLI tarball: https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.1.0-rc.8.tgz
- **[A17]** npm MCP tarball: https://registry.npmjs.org/@deepseek-ai/dsh-mcp-client/-/dsh-mcp-client-0.1.0-rc.8.tgz
- **[A18]** DSH development guide at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/docs/development.md
- **[A19]** DSH Landlock README at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/native/landlock-run/README.md
- **[A20]** Landlock entry manifest at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/native/landlock-run/packages/entry/package.json
- **[A21]** Landlock Linux ARM64 manifest at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/native/landlock-run/packages/linux-arm64/package.json
- **[A22]** DSH Python SDK manifest at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/python/sdk/pyproject.toml
- **[A23]** DSH Python runtime-bin manifest at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/python/sdk-runtime/pyproject.toml
- **[A24]** DSH MIT license at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/LICENSE
- **[A25]** DSH third-party notices at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/THIRD_PARTY_NOTICES.md
- **[A26]** DSH brand guidelines at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/BRAND_GUIDELINES.md
- **[A27]** DSH MCP README at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/packages/mcp/mcp-client/README.md
- **[A28]** DSH vendored provenance manifest at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/vendor/README.md
- **[A29]** DSH vendored Cordis manifest at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/vendor/cordis/package.json
- **[A30]** DSH vendored Cordis license at pinned commit: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/141eb6fef83422698aef7a981029e843e8161534/vendor/cordis/LICENSE
- **[A31]** Upstream Cordis manifest at claimed source commit: https://raw.githubusercontent.com/cordiverse/cordis/56b3d4f725681cf4556c1a8695a709cc3b6eed74/packages/core/package.json
- **[A32]** Upstream Cordis license at claimed source commit: https://raw.githubusercontent.com/cordiverse/cordis/56b3d4f725681cf4556c1a8695a709cc3b6eed74/LICENSE
- **[A33]** DSH generated tag tarball endpoint: https://api.github.com/repos/deepseek-ai/deepseek-harness/tarball/dsh-v0.1.0-rc.8
