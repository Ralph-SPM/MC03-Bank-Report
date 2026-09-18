# MC03 persistence migrations

The foundation migration creates the SQLite tables, foreign-key constraints, WAL-compatible schema, and append-only mutation guards used by Task 1. Runtime startup uses the same schema helpers before services begin accepting work.
