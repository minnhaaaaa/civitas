"""Organization-scoped repository APIs for authenticated application paths.

Legacy unscoped repositories remain migration/import primitives.  Inbound request
and worker composition roots must obtain repositories through ``TenantRepositories``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from civitas.domain.planning import (
    SKU,
    DemandForecast,
    PlanningRun,
    Supplier,
    SupplierOffer,
    Warehouse,
)
from civitas.persistence.models import (
    DemandForecastModel,
    PlanningRunModel,
    SKUModel,
    SupplierModel,
    SupplierOfferModel,
    WarehouseModel,
)
from civitas.persistence.repositories import (
    _forecast_to_domain,
    _forecast_to_model,
    _offer_to_domain,
    _offer_to_model,
    _planning_run_to_domain,
    _planning_run_to_model,
    _sku_to_domain,
    _sku_to_model,
    _supplier_to_domain,
    _supplier_to_model,
    _warehouse_to_domain,
    _warehouse_to_model,
)

DomainT = TypeVar("DomainT")
ModelT = TypeVar("ModelT")


def _column(model_type: type[object], name: str) -> Any:
    return getattr(model_type, name)


def _entity_organization(entity: object) -> str:
    return str(object.__getattribute__(entity, "organization_id"))


class DirectTenantRepository[DomainT, ModelT]:
    """Scope rows carrying an ``organization_id`` column at every operation."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        organization_id: str,
        model_type: type[ModelT],
        to_domain: Callable[[ModelT], DomainT],
        to_model: Callable[[DomainT], ModelT],
        entity_organization: Callable[[DomainT], str],
    ) -> None:
        if not organization_id:
            raise ValueError("organization_id is required")
        self._session = session
        self._organization_id = organization_id
        self._model_type = model_type
        self._to_domain = to_domain
        self._to_model = to_model
        self._entity_organization = entity_organization

    async def get(self, entity_id: str) -> DomainT | None:
        organization_column = _column(self._model_type, "organization_id")
        id_column = _column(self._model_type, "id")
        row = await self._session.scalar(
            select(self._model_type).where(
                id_column == entity_id,
                organization_column == self._organization_id,
            )
        )
        return None if row is None else self._to_domain(row)

    async def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[DomainT]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid pagination bounds")
        organization_column = _column(self._model_type, "organization_id")
        rows = (
            await self._session.scalars(
                select(self._model_type)
                .where(organization_column == self._organization_id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return tuple(self._to_domain(row) for row in rows)

    async def add(self, entity: DomainT) -> None:
        if self._entity_organization(entity) != self._organization_id:
            raise PermissionError("cannot add an entity for another organization")
        self._session.add(self._to_model(entity))


class PlanningRunChildRepository[DomainT, ModelT]:
    """Scope child rows through their owning planning run."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        organization_id: str,
        model_type: type[ModelT],
        to_domain: Callable[[ModelT], DomainT],
        to_model: Callable[[DomainT], ModelT],
        planning_run_id: Callable[[DomainT], str],
    ) -> None:
        self._session = session
        self._organization_id = organization_id
        self._model_type = model_type
        self._to_domain = to_domain
        self._to_model = to_model
        self._planning_run_id = planning_run_id

    async def get(self, entity_id: str) -> DomainT | None:
        row = await self._session.scalar(
            select(self._model_type)
            .join(
                PlanningRunModel,
                _column(self._model_type, "planning_run_id") == PlanningRunModel.id,
            )
            .where(
                _column(self._model_type, "id") == entity_id,
                PlanningRunModel.organization_id == self._organization_id,
            )
        )
        return None if row is None else self._to_domain(row)

    async def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[DomainT]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid pagination bounds")
        rows = (
            await self._session.scalars(
                select(self._model_type)
                .join(
                    PlanningRunModel,
                    _column(self._model_type, "planning_run_id") == PlanningRunModel.id,
                )
                .where(PlanningRunModel.organization_id == self._organization_id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return tuple(self._to_domain(row) for row in rows)

    async def add(self, entity: DomainT) -> None:
        owner = await self._session.scalar(
            select(PlanningRunModel.organization_id).where(
                PlanningRunModel.id == self._planning_run_id(entity),
                PlanningRunModel.organization_id == self._organization_id,
            )
        )
        if owner is None:
            raise PermissionError("planning run is not owned by this organization")
        self._session.add(self._to_model(entity))


class TenantRepositories:
    """Request/unit-of-work repository factory bound to exactly one tenant."""

    def __init__(self, session: AsyncSession, organization_id: str) -> None:
        if not organization_id:
            raise ValueError("organization_id is required")
        self._session = session
        self.organization_id = organization_id

    @property
    def skus(self) -> DirectTenantRepository[SKU, SKUModel]:
        return self._direct(SKUModel, _sku_to_domain, _sku_to_model)

    @property
    def warehouses(self) -> DirectTenantRepository[Warehouse, WarehouseModel]:
        return self._direct(WarehouseModel, _warehouse_to_domain, _warehouse_to_model)

    @property
    def suppliers(self) -> DirectTenantRepository[Supplier, SupplierModel]:
        return self._direct(SupplierModel, _supplier_to_domain, _supplier_to_model)

    @property
    def planning_runs(self) -> DirectTenantRepository[PlanningRun, PlanningRunModel]:
        return self._direct(PlanningRunModel, _planning_run_to_domain, _planning_run_to_model)

    @property
    def demand_forecasts(
        self,
    ) -> PlanningRunChildRepository[DemandForecast, DemandForecastModel]:
        return PlanningRunChildRepository(
            session=self._session,
            organization_id=self.organization_id,
            model_type=DemandForecastModel,
            to_domain=_forecast_to_domain,
            to_model=_forecast_to_model,
            planning_run_id=lambda entity: entity.planning_run_id,
        )

    @property
    def supplier_offers(
        self,
    ) -> PlanningRunChildRepository[SupplierOffer, SupplierOfferModel]:
        return PlanningRunChildRepository(
            session=self._session,
            organization_id=self.organization_id,
            model_type=SupplierOfferModel,
            to_domain=_offer_to_domain,
            to_model=_offer_to_model,
            planning_run_id=lambda entity: entity.planning_run_id,
        )

    def _direct(
        self,
        model_type: type[ModelT],
        to_domain: Callable[[ModelT], DomainT],
        to_model: Callable[[DomainT], ModelT],
    ) -> DirectTenantRepository[DomainT, ModelT]:
        return DirectTenantRepository(
            session=self._session,
            organization_id=self.organization_id,
            model_type=model_type,
            to_domain=to_domain,
            to_model=to_model,
            entity_organization=_entity_organization,
        )
