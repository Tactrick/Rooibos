# Changelog — Rooibos integration

## v1.5.1 — Chart Engine runtime integration preparation

- Added the runtime integration contract for the historical Chart Engine v0.3.5.
- Added a 24-case A13/Chart Engine runtime acceptance matrix, including proof that analysis is not invoked on denied data.
- Added a heuristic AST-based access-audit utility to flag direct SQLite/collector/governance access in the Chart Engine runtime source.
- Added tests for clean source, direct SQLite imports/connections and protected-table SQL detection.
- No Chart Engine v0.3.5 runtime source was available; no runtime entry point was modified and no bypass-removal claim is made.
- Existing analysis algorithms, collector schemas and migrations are unchanged.
