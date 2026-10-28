"""The mirror's build half (ARCHITECTURE.md §2.0, §2.2; ADR-0015): the SQLite projection of one load result.

`core.schema` holds the surface (tables, columns, DDL), `core.rows` the rows (pure), `build` writes them into a fresh
file. The publish of a generation (pointer swap, reader pinning, GC) is not built yet (BL-0113).
"""
