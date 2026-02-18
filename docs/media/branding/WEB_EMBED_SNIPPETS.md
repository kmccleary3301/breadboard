# Web Embed Snippets (Branding v1)

Use these snippets when wiring BreadBoard branding into a website or docs shell.

## Favicon Snippets

```html
<link rel="icon" type="image/svg+xml" href="/docs/media/branding/favicon.svg" />
<link rel="icon" type="image/png" sizes="32x32" href="/docs/media/branding/favicon-32.png" />
<link rel="icon" type="image/png" sizes="64x64" href="/docs/media/branding/favicon-64.png" />
```

## OpenGraph / Social Snippets

```html
<meta property="og:title" content="BreadBoard" />
<meta property="og:description" content="The agent harness kernel." />
<meta property="og:image" content="/docs/media/branding/og_banner_v1.png" />
<meta property="og:type" content="website" />

<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="BreadBoard" />
<meta name="twitter:description" content="The agent harness kernel." />
<meta name="twitter:image" content="/docs/media/branding/og_banner_v1.png" />
```

## Hero/Header Snippet

```html
<img
  src="/docs/media/branding/breadboard_ascii_logo_v1.png"
  alt="BreadBoard logo"
  style="max-width: 100%; height: auto;"
/>
```

## Canonical Asset Policy

- Banner source of truth: `breadboard_ascii_logo_v1.svg`
- Do not edit generated PNGs by hand.
- Regenerate derivatives using commands in `docs/media/branding/README.md`.
