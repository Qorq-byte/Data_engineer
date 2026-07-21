"""RAG index auto-refresh — schema change detection and LanceDB rebuild.

See SPEC §5.10 (Phase 5 roadmap).

Usage::

    from app.rag.index_refresher import IndexRefresher

    refresher = IndexRefresher(schema_rag=rag, metric_rag=mrag)
    needs = refresher.needs_refresh("my_db")
    if needs:
        await refresher.refresh("my_db")
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from datetime import UTC, datetime
from typing import Any


class IndexRefresher:
    """Detect schema changes and trigger LanceDB index rebuilds.

    Args:
        schema_rag: Pre-configured SchemaMetadataRAG instance.
        metric_rag: Pre-configured MetricRAG instance (optional).
        ttl_minutes: Minimum minutes between refreshes for the same database.
    """

    def __init__(
        self,
        schema_rag: Any,
        metric_rag: Any = None,
        ttl_minutes: int = 60,
    ) -> None:
        self._schema_rag = schema_rag
        self._metric_rag = metric_rag
        self._ttl_minutes = ttl_minutes
        self._version_cache: dict[str, str] = {}
        self._last_refresh: dict[str, datetime] = {}
        self._pending: set[str] = set()

    @staticmethod
    def _compute_schema_hash(schema_snapshot: Any) -> str:
        if hasattr(schema_snapshot, "to_dict"):
            data = schema_snapshot.to_dict()
        elif hasattr(schema_snapshot, "__dataclass_fields__"):
            from dataclasses import asdict

            data = asdict(schema_snapshot)
        else:
            data = str(schema_snapshot)
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def check_schema_version(self, db_id: str, schema_snapshot: Any) -> bool:
        new_hash = self._compute_schema_hash(schema_snapshot)
        old_hash = self._version_cache.get(db_id)
        if old_hash is None or old_hash != new_hash:
            self._version_cache[db_id] = new_hash
            return True
        return False

    def needs_refresh(self, db_id: str) -> bool:
        if db_id in self._pending:
            return True
        last = self._last_refresh.get(db_id)
        if last is None:
            return True
        age = (datetime.now(UTC) - last).total_seconds() / 60
        return age > self._ttl_minutes

    async def refresh(self, db_id: str) -> bool:
        self._pending.add(db_id)
        try:
            success = await self._rebuild_schema_index(db_id)
            if success:
                self._last_refresh[db_id] = datetime.now(UTC)
            return success
        finally:
            self._pending.discard(db_id)

    async def _rebuild_schema_index(self, db_id: str) -> bool:
        """Extract schema from a live connection and re-index into RAG.

        Uses the pre-injected SchemaMetadataRAG instance (not creating a new one).
        """
        try:
            from app.db.connections import ConnectionFactory
            from app.db.schema_extractor import SchemaExtractor
            from app.rag.converters import snapshot_to_schema_docs

            info = ConnectionFactory.get_info(db_id)
            conn = ConnectionFactory.get(db_id)
            snapshot = await SchemaExtractor.extract(
                conn, info.db_type, info.database_name
            )
            self._version_cache[db_id] = self._compute_schema_hash(snapshot)

            docs = snapshot_to_schema_docs(snapshot, db_id)
            await self._schema_rag.index_schemas(docs)
            return True
        except Exception:
            return False

    async def refresh_all(self) -> dict[str, bool]:
        try:
            from app.db.connections import ConnectionFactory

            infos = ConnectionFactory.list_all()
            db_ids = [i.db_id for i in infos]
        except Exception:
            db_ids = []

        results: dict[str, bool] = {}
        for db_id in db_ids:
            if self.needs_refresh(db_id):
                results[db_id] = await self.refresh(db_id)
        return results

    def schedule_refresh(self, interval_minutes: int = 60) -> asyncio.Task | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None

        async def _periodic() -> None:
            while True:
                await asyncio.sleep(interval_minutes * 60)
                with contextlib.suppress(Exception):
                    await self.refresh_all()

        return loop.create_task(_periodic())

    def get_refresh_status(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        last_times = {
            db_id: ts.isoformat() for db_id, ts in self._last_refresh.items()
        }
        return {
            "last_refresh_time": last_times,
            "pending_count": len(self._pending),
            "tracked_databases": len(self._version_cache),
            "ttl_minutes": self._ttl_minutes,
            "current_time": now.isoformat(),
        }
