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
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.auth.tenant import get_current_tenant_id


@dataclass
class _RefreshState:
    versions: dict[str, str] = field(default_factory=dict)
    last_refresh: dict[str, datetime] = field(default_factory=dict)
    pending: Counter[str] = field(default_factory=Counter)


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
        self._states: dict[int, _RefreshState] = {}

    def _tenant_state(self) -> _RefreshState:
        tenant_id = get_current_tenant_id()
        if tenant_id not in self._states:
            self._states[tenant_id] = _RefreshState()
        return self._states[tenant_id]

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
        versions = self._tenant_state().versions
        new_hash = self._compute_schema_hash(schema_snapshot)
        old_hash = versions.get(db_id)
        if old_hash is None or old_hash != new_hash:
            versions[db_id] = new_hash
            return True
        return False

    def needs_refresh(self, db_id: str) -> bool:
        state = self._tenant_state()
        if db_id in state.pending:
            return True
        last = state.last_refresh.get(db_id)
        if last is None:
            return True
        age = (datetime.now(UTC) - last).total_seconds() / 60
        return age > self._ttl_minutes

    def invalidate_database(self, db_id: str) -> None:
        """Forget only this database's successful publication metadata after removal."""
        state = self._tenant_state()
        state.versions.pop(db_id, None)
        state.last_refresh.pop(db_id, None)

    def invalidate_all(self) -> None:
        """Forget successful refresh metadata for the current tenant."""
        state = self._tenant_state()
        state.versions.clear()
        state.last_refresh.clear()

    async def refresh(self, db_id: str) -> bool:
        state = self._tenant_state()
        state.pending[db_id] += 1
        try:
            success = await self._rebuild_schema_index(db_id)
            if success:
                state.last_refresh[db_id] = datetime.now(UTC)
            return success
        finally:
            state.pending[db_id] -= 1
            if state.pending[db_id] == 0:
                del state.pending[db_id]

    async def _rebuild_schema_index(self, db_id: str) -> bool:
        """Extract schema from a live connection and re-index into RAG.

        Uses the pre-injected SchemaMetadataRAG instance (not creating a new one).
        """
        try:
            from app.db.schema_refresh import refresh_database_schema
            from app.rag.converters import snapshot_to_schema_docs

            result = await refresh_database_schema(
                db_id, self._schema_rag, document_builder=snapshot_to_schema_docs,
            )
            if not result.rag_indexed:
                return False
            self._tenant_state().versions[db_id] = self._compute_schema_hash(result.snapshot)
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

    def schedule_refresh(self, interval_minutes: int = 60) -> asyncio.Task[None] | None:
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
        state = self._tenant_state()
        last_times = {db_id: ts.isoformat() for db_id, ts in state.last_refresh.items()}
        return {
            "last_refresh_time": last_times,
            "pending_count": len(state.pending),
            "tracked_databases": len(state.versions),
            "ttl_minutes": self._ttl_minutes,
            "current_time": now.isoformat(),
        }
