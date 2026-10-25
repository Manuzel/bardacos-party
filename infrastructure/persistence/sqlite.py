"""
Repositório da biblioteca em SQLite (stdlib, sem dependência nova).
- Gravação atômica e transacional: uma falha no meio nunca deixa o arquivo pela metade.
- Esquema versionado por `PRAGMA user_version`, com migrações aplicadas em ordem.
- Uma única conexão, usada pela thread que criou o repositório.
"""
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from domain.models import (
    BackendHint, LibraryData, SourceKind, Tag, TagId, Track, TrackId, TrackKind, normalize_title
)

# Cada item é um script aplicado uma única vez, dentro de uma transação.
# Nunca altere uma migração já publicada: acrescente outra no fim da tupla.
MIGRATIONS: tuple[str, ...] = (
    # 1 — Faixas e tags
    """
    CREATE TABLE tracks (
        id             TEXT PRIMARY KEY,
        title          TEXT NOT NULL,
        title_norm     TEXT NOT NULL,
        kind           TEXT NOT NULL CHECK (kind IN ('sfx', 'ambience', 'music')),
        source_kind    TEXT NOT NULL CHECK (source_kind IN ('local', 'stream', 'cache')),
        local_path     TEXT UNIQUE,
        origin_url     TEXT,
        duration_s     REAL CHECK (duration_s IS NULL OR duration_s > 0),
        default_volume REAL NOT NULL DEFAULT 0.8 CHECK (default_volume BETWEEN 0 AND 1),
        default_loop   INTEGER NOT NULL DEFAULT 0,
        backend_hint   TEXT CHECK (backend_hint IN ('vlc', 'pygame')),
        missing        INTEGER NOT NULL DEFAULT 0,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        CHECK (source_kind <> 'local' OR local_path IS NOT NULL),
        CHECK (source_kind = 'local' OR origin_url IS NOT NULL)
    );
    CREATE INDEX ix_tracks_title_norm ON tracks(title_norm);

    CREATE TABLE tags (
        id       INTEGER PRIMARY KEY,
        name     TEXT NOT NULL UNIQUE COLLATE NOCASE,
        color    TEXT,
        position INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE track_tags (
        track_id TEXT    NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
        tag_id   INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
        PRIMARY KEY (track_id, tag_id)
    ) WITHOUT ROWID;
    CREATE INDEX ix_track_tags_tag ON track_tags(tag_id, track_id);
    """,
)

SCHEMA_VERSION = len(MIGRATIONS)


class SqliteLibraryRepository:
    """Implementa o port `LibraryRepository`."""

    def __init__(self, db_path: Path | str):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # isolation_level=None: nenhuma transação implícita; elas são abertas
        # explicitamente em `unit_of_work` e nas migrações.
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    @property
    def schema_version(self) -> int:
        return self._conn.execute("PRAGMA user_version").fetchone()[0]

    def _migrate(self) -> None:
        current = self.schema_version
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"O banco está na versão {current}, mais nova que a deste app ({SCHEMA_VERSION}). "
                "Atualize o Bardaco's Party antes de abrir esta biblioteca."
            )

        for version in range(current + 1, SCHEMA_VERSION + 1):
            script = MIGRATIONS[version - 1]
            try:
                self._conn.executescript(
                    f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
                )
            except sqlite3.Error:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def load(self) -> LibraryData:
        tags = tuple(
            Tag(id=row["id"], name=row["name"], color=row["color"], position=row["position"])
            for row in self._conn.execute("SELECT * FROM tags ORDER BY position, name")
        )

        tags_by_track: dict[TrackId, set[TagId]] = {}
        for row in self._conn.execute("SELECT track_id, tag_id FROM track_tags"):
            tags_by_track.setdefault(row["track_id"], set()).add(row["tag_id"])

        tracks = tuple(
            _row_to_track(row, frozenset(tags_by_track.get(row["id"], ())))
            for row in self._conn.execute("SELECT * FROM tracks ORDER BY title_norm, id")
        )
        return LibraryData(tracks=tracks, tags=tags)

    @contextmanager
    def unit_of_work(self) -> Iterator["SqliteUnitOfWork"]:
        # IMMEDIATE reserva a escrita já no início, em vez de falhar no meio da transação.
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield SqliteUnitOfWork(self._conn)
        except BaseException:
            # Alguns erros do SQLite já desfazem a transação sozinhos.
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def close(self) -> None:
        self._conn.close()


class SqliteUnitOfWork:
    """Implementa o port `UnitOfWork`. Só existe dentro de `unit_of_work()`."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save_track(self, track: Track) -> None:
        now = _utc_now()
        self._conn.execute(
            """
            INSERT INTO tracks (
                id, title, title_norm, kind, source_kind, local_path, origin_url, duration_s,
                default_volume, default_loop, backend_hint, missing, created_at, updated_at
            ) VALUES (
                :id, :title, :title_norm, :kind, :source_kind, :local_path, :origin_url, :duration_s,
                :default_volume, :default_loop, :backend_hint, :missing, :now, :now
            )
            ON CONFLICT (id) DO UPDATE SET
                title = excluded.title,
                title_norm = excluded.title_norm,
                kind = excluded.kind,
                source_kind = excluded.source_kind,
                local_path = excluded.local_path,
                origin_url = excluded.origin_url,
                duration_s = excluded.duration_s,
                default_volume = excluded.default_volume,
                default_loop = excluded.default_loop,
                backend_hint = excluded.backend_hint,
                missing = excluded.missing,
                updated_at = excluded.updated_at
            """,
            {
                "id": track.id,
                "title": track.title,
                "title_norm": normalize_title(track.title),
                "kind": track.kind.value,
                "source_kind": track.source_kind.value,
                "local_path": track.local_path,
                "origin_url": track.origin_url,
                "duration_s": track.duration_s,
                "default_volume": track.default_volume,
                "default_loop": int(track.default_loop),
                "backend_hint": track.backend_hint.value if track.backend_hint else None,
                "missing": int(track.missing),
                "now": now,
            },
        )
        self._conn.execute("DELETE FROM track_tags WHERE track_id = ?", (track.id,))
        self._conn.executemany(
            "INSERT INTO track_tags (track_id, tag_id) VALUES (?, ?)",
            [(track.id, tag_id) for tag_id in sorted(track.tag_ids)],
        )

    def delete_track(self, track_id: TrackId) -> None:
        self._conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))

    def save_tag(self, tag: Tag) -> Tag:
        params = {"id": tag.id, "name": tag.name.strip(), "color": tag.color, "position": tag.position}
        try:
            if tag.id is None:
                cursor = self._conn.execute(
                    "INSERT INTO tags (name, color, position) VALUES (:name, :color, :position)",
                    params,
                )
                return replace(tag, id=cursor.lastrowid, name=params["name"])

            cursor = self._conn.execute(
                "UPDATE tags SET name = :name, color = :color, position = :position WHERE id = :id",
                params,
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Já existe uma tag chamada {params['name']!r}.") from exc

        if cursor.rowcount == 0:
            raise KeyError(f"Tag {tag.id} não existe.")
        return replace(tag, name=params["name"])

    def delete_tag(self, tag_id: TagId) -> None:
        self._conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))


def _row_to_track(row: sqlite3.Row, tag_ids: frozenset[TagId]) -> Track:
    return Track(
        id=row["id"],
        title=row["title"],
        kind=TrackKind(row["kind"]),
        source_kind=SourceKind(row["source_kind"]),
        local_path=row["local_path"],
        origin_url=row["origin_url"],
        duration_s=row["duration_s"],
        default_volume=row["default_volume"],
        default_loop=bool(row["default_loop"]),
        backend_hint=BackendHint(row["backend_hint"]) if row["backend_hint"] else None,
        missing=bool(row["missing"]),
        tag_ids=tag_ids,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
