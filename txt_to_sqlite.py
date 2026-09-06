#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from typing import Iterable, Iterator, List, Optional, Sequence

DEFAULT_DB = "feed.sqlite"
_META_TABLE = "_file_import_meta"
_BATCH_SIZE = 5000
# mtime granularity can be coarse on some filesystems; treat sub-second
# differences as "unchanged".
_MTIME_EPSILON = 1e-6


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def table_name_for(txt_path: str) -> str:
    """Return the table name derived from a ``.txt`` file name."""
    return os.path.splitext(os.path.basename(txt_path))[0]


def _quote_ident(name: str) -> str:
    """Quote an identifier for safe use in SQL."""
    return '"' + name.replace('"', '""') + '"'


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_quote_ident(_META_TABLE)} ("
        "    filename TEXT PRIMARY KEY,"
        "    mtime    REAL NOT NULL,"
        "    rows     INTEGER,"
        "    imported_at TEXT DEFAULT CURRENT_TIMESTAMP"
        ")"
    )


def _recorded_mtime(conn: sqlite3.Connection, filename: str) -> Optional[float]:
    cur = conn.execute(
        f"SELECT mtime FROM {_quote_ident(_META_TABLE)} WHERE filename = ?",
        (filename,),
    )
    row = cur.fetchone()
    return None if row is None else float(row[0])


def _record_import(
    conn: sqlite3.Connection, filename: str, mtime: float, rows: int
) -> None:
    conn.execute(
        f"INSERT INTO {_quote_ident(_META_TABLE)} (filename, mtime, rows) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(filename) DO UPDATE SET "
        "    mtime = excluded.mtime,"
        "    rows = excluded.rows,"
        "    imported_at = CURRENT_TIMESTAMP",
        (filename, mtime, rows),
    )


def _dedupe_header(header: Sequence[str]) -> List[str]:
    """Make column names non-empty and unique."""
    seen: dict[str, int] = {}
    out: List[str] = []
    for i, raw in enumerate(header):
        name = (raw or "").strip() or f"column_{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


def _sniff_dialect(sample: str) -> type[csv.Dialect] | csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel  # default: comma separated


def _rows_in_batches(
    reader: Iterator[List[str]], width: int, batch: int
) -> Iterator[List[List[str]]]:
    chunk: List[List[str]] = []
    for row in reader:
        if len(row) < width:
            row = row + [None] * (width - len(row))
        elif len(row) > width:
            row = row[:width]
        chunk.append(row)
        if len(chunk) >= batch:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def file_has_changed(
    txt_path: str, db_path: str = DEFAULT_DB, conn: Optional[sqlite3.Connection] = None
) -> bool:
    """Return True if *txt_path* is new or its mtime differs from the last import."""
    filename = os.path.basename(txt_path)
    mtime = os.path.getmtime(txt_path)
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(db_path)
    try:
        _ensure_meta_table(conn)
        recorded = _recorded_mtime(conn, filename)
    finally:
        if own_conn:
            conn.close()
    return recorded is None or abs(recorded - mtime) > _MTIME_EPSILON


def create_table_from_txt(
    txt_path: str,
    db_path: str = DEFAULT_DB,
    conn: Optional[sqlite3.Connection] = None,
    force: bool = False,
) -> bool:
    """Create/replace the SQLite table for a single ``.txt`` file.

    The table is (re)built only when the file's filesystem modification date has
    changed since the previous import, unless *force* is true.

    Parameters
    ----------
    txt_path : str
        Path to the ``.txt`` file.  The table is named after the file stem.
    db_path : str
        SQLite database file.  Ignored when *conn* is supplied.
    conn : sqlite3.Connection, optional
        Reuse an existing connection instead of opening *db_path*.
    force : bool
        Rebuild even if the file looks unchanged.

    Returns
    -------
    bool
        True if the table was (re)created, False if it was skipped as unchanged.
    """
    if not os.path.isfile(txt_path):
        raise FileNotFoundError(txt_path)

    filename = os.path.basename(txt_path)
    table = table_name_for(txt_path)
    mtime = os.path.getmtime(txt_path)

    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(db_path)

    try:
        _ensure_meta_table(conn)

        if not force:
            recorded = _recorded_mtime(conn, filename)
            if recorded is not None and abs(recorded - mtime) <= _MTIME_EPSILON:
                return False  # unchanged -> nothing to do

        with open(txt_path, "r", newline="", encoding="utf-8-sig") as fh:
            sample = fh.read(64 * 1024)
            fh.seek(0)
            dialect = _sniff_dialect(sample)
            reader = csv.reader(fh, dialect)

            try:
                raw_header = next(reader)
            except StopIteration:
                raw_header = []

            columns = _dedupe_header(raw_header)

            with conn:  # single transaction: drop + create + fill + meta
                conn.execute(f"DROP TABLE IF EXISTS {_quote_ident(table)}")

                if not columns:
                    # empty file: keep an empty placeholder table
                    conn.execute(
                        f"CREATE TABLE {_quote_ident(table)} (dummy TEXT)"
                    )
                    _record_import(conn, filename, mtime, 0)
                    return True

                col_defs = ", ".join(f"{_quote_ident(c)} TEXT" for c in columns)
                conn.execute(f"CREATE TABLE {_quote_ident(table)} ({col_defs})")

                placeholders = ", ".join("?" * len(columns))
                insert_sql = (
                    f"INSERT INTO {_quote_ident(table)} VALUES ({placeholders})"
                )

                total = 0
                for chunk in _rows_in_batches(reader, len(columns), _BATCH_SIZE):
                    conn.executemany(insert_sql, chunk)
                    total += len(chunk)

                _record_import(conn, filename, mtime, total)

        return True
    finally:
        if own_conn:
            conn.close()


def build_database(
    folder: str = ".",
    db_path: str = DEFAULT_DB,
    force: bool = False,
    files: Optional[Iterable[str]] = None,
) -> dict[str, bool]:
    """Import every ``*.txt`` in *folder* (or an explicit *files* list) into SQLite.

    Returns a mapping ``{filename: was_imported}``.
    """
    if files is None:
        files = sorted(
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(".txt")
        )
    else:
        files = list(files)

    results: dict[str, bool] = {}
    conn = sqlite3.connect(db_path)
    try:
        for path in files:
            results[os.path.basename(path)] = create_table_from_txt(
                path, conn=conn, force=force
            )
    finally:
        conn.close()
    return results


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load .txt files into a SQLite database, skipping unchanged files."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=".",
        help="folder to scan for *.txt files (default: current folder)",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="optional explicit .txt files to import instead of scanning the folder",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"SQLite database file to write (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild every table even if the file is unchanged",
    )
    args = parser.parse_args(argv)

    results = build_database(
        folder=args.folder,
        db_path=args.db,
        force=args.force,
        files=args.files or None,
    )

    if not results:
        print("No .txt files found.", file=sys.stderr)
        return 1

    for name, imported in results.items():
        print(f"{'imported ' if imported else 'unchanged'}  {name}")
    print(f"-> {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
