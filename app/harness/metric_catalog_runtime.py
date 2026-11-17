"""Choose ordinary request snapshots or an activated immutable worker catalog."""

from pathlib import Path

from app.config.settings import settings
from app.harness.mysql_coordination import CoordinationUnavailableError
from app.harness.shared_query_runtime import current_shared_runtime
from app.knowledge.metric_catalog import CatalogError, MetricCatalog, load_catalog


def configured_metric_catalog() -> MetricCatalog:
    if not settings.shared_worker_release:
        return load_catalog(settings.metric_catalog_path)
    runtime = current_shared_runtime()
    release = runtime.worker_release if runtime is not None else None
    if (
        runtime is None or not runtime.ready or release is None
        or release.path != Path(settings.shared_worker_release).resolve()
    ):
        raise CoordinationUnavailableError("Sealed metric catalog runtime is not active")
    catalog = release.catalog_for(settings.metric_catalog_path)
    if catalog is None:
        raise CatalogError("metric_query requires a sealed metric catalog")
    return catalog


async def verify_metric_discovery_runtime() -> None:
    """Discovery creates no session but still observes the shared configuration fence."""
    if settings.shared_worker_release:
        runtime = current_shared_runtime()
        if runtime is None or not runtime.ready or runtime.verify_configuration is None:
            raise CoordinationUnavailableError("Shared metric runtime is not ready")
        await runtime.verify_configuration()
