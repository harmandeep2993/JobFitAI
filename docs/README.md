# docs

Design docs and diagrams for JobsFitAI.

## Architecture diagram


```
docs/architecture.png     (or architecture.svg)
```

The root `README.md` references it with `![JobsFitAI architecture](docs/architecture.png)`.

Notes:
- Do not create a folder named `images/` - the repo `.gitignore` ignores any path
  component literally called `images`, so the file would be silently untracked.
  Keep diagram files directly in `docs/`.
- Export at a readable width (1600 px+ for PNG) or use SVG so it stays crisp on GitHub.
- Keep the editable source too (e.g. `architecture.excalidraw`, `architecture.drawio`)
  next to the exported image so it can be updated later.
