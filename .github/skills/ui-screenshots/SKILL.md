---
name: ui-screenshots
description: "Capture and refresh Themarr README screenshots using Playwright in an agent-driven workflow where the agent selects representative UI states."
---

# UI Screenshots (Themarr)

Use this skill when asked to update README screenshots. The agent should decide
which UI states best represent current functionality, then capture both dark
and light theme variants.

## Prerequisites

```bash
pip install playwright Pillow -q
python3 -m playwright install chromium
```

## Agent Workflow

1. Start the app locally (or use an already-running instance).
2. Open the UI with Playwright.
3. Discover current, meaningful flows (do not assume stale selectors).
4. Choose representative screenshots for major functionality.
5. Decide the number of scenes (`N`) yourself.
6. Capture exactly the same `N` scenes in dark mode and in light mode.
7. Save files using the filename convention below.
8. Rewrite the README `## Screenshots` section so it matches exactly the files
   you produced.
9. Verify screenshots are crisp, current, and aligned with real UI behavior.

## Quality gate (must pass before finishing)

Before considering the run complete, verify all of the following:

- No visible error banners/messages in captured scenes (for example: "Error",
  "Failed", "No libraries found")
- Explicitly fail if known preview errors appear, including:
  - `Preview stream could not be loaded. Check connection and item availability.`
  - `Could not prepare preview: ...`
- Poster/grid scene shows multiple real poster images loaded (not mostly
  placeholder/fallback blocks)
- Modal scenes are full-screen captures with modal overlaying the main UI
  context (not modal-only crops)
- Dark and light sets are complete and 1:1 matched

If any check fails, iterate: adjust mocking/capture behavior and recapture.

## Data mocking guidance

When running against local Themarr, mock API responses so screenshots are
stable and representative:

- Mock both Plex and Jellyfin status as configured in `/api/status`
- Mock at least one Plex library and one Jellyfin library in `/api/libraries`
- Mock library items and media actions needed by the selected scenes
- For Plex-download modal scenes, mock provider preview endpoints so audio preview
  is available and does not emit UI error text:
  - `/api/items/<provider>/<item_id>/theme/preview/check` -> `{ "available": true }`
  - `/api/items/<provider>/<item_id>/theme/preview` -> playable audio stream
- Do not rely on real external servers for screenshot generation

This prevents sidebar states like "No libraries found" for one provider when
capturing product screenshots.

## Poster/thumbnail guidance

For poster-heavy views, prioritize real poster art whenever feasible:

- use public metadata/image sources
  (for example TVMaze for shows and iTunes Search for movies/shows) and cache
  results in-memory for the run.
- Keep a deterministic fallback generator (SVG/PIL artwork) only for items that
  cannot be resolved from external sources.
- Route `/api/poster/*` and YouTube thumbnail URLs to fetched/cached assets.
- Ensure posters are diverse and recognizable, not mostly placeholders.

## Modal screenshot quality guidance

For modal-focused scenes, capture the full application view with the modal
overlaid (not the modal box alone):

- Use full-page screenshots so context behind the modal remains visible
- Ensure the underlying page is in a representative state before opening modal
- Before taking the Plex-download screenshot, assert:
  - `#preview-audio-error` remains hidden/empty
  - no visible `.error` text exists inside the modal

## Filename guidance (agent-managed)

Use:

- `screenshots/{index}_{slug}_dark.png`
- `screenshots/{index}_{slug}_light.png`

Where:

- `index` is zero-padded (`01`, `02`, ...)
- `slug` is short, stable, and feature-oriented (for example: `library_grid`,
  `youtube_modal`, `copy_theme_modal`)
- each `(index, slug)` must have both `_dark` and `_light` files
- the set of dark and light files must be identical except for the theme suffix

## Selection guidance

The agent should prioritize screenshots that show:

- Authentication and onboarding states when requested (login screen and welcome
  screen)
- Library browsing (poster and list views)
- A media action modal for YouTube download
- A media action modal for copying local themes
- A media action modal for Plex download

If the UI changes, the agent can choose equivalent updated screens as long as
the same feature categories are represented in both dark and light themes.

## README update contract

After capturing screenshots, the agent should rewrite the entire
`README.md` screenshot section:

- Update scene titles/captions to match selected screenshots
- Update image links to match saved files
- Keep dark/light parity explicit in the markdown layout
- Replace stale entries (do not keep links to deleted screenshot files)

## Notes

- This workflow is intentionally agent-driven (not tied to one fixed script).
- Do not run in CI automatically; run on demand when screenshots are requested.
