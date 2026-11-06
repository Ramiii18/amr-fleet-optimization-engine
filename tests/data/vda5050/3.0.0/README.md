# VDA 5050 3.0.0 JSON schemas (test data)

These files are copied, unchanged, from the official VDA 5050 repository, so that the tests can
validate the adapter's messages against the specification's own schemas without network access
(D30).

- **Source:** https://github.com/VDA5050/VDA5050, tag `3.0.0` (commit
  `e9ba560b0e2d3f66526550ad5d61b8a2ad936172`), folder `json_schemas/`.
- **Files:** `order.schema`, `state.schema`, `instantActions.schema`, `connection.schema`.
- **License:** MIT, copyright 2024 Verband der Automobilindustrie; see `LICENSE.txt` in this folder,
  copied from the same tag.

**A known defect.** The published `order.schema` has a trailing comma (line 314) that strict JSON
rejects. The files are kept as published; the test loader removes trailing commas before parsing.
