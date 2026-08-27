"""Repository implementations translating domain objects to ORM rows."""

from collections.abc import Callable, Sequence
from typing import TypeVar, cast

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from civitas.domain.planning import (
    SKU,
    DemandForecast,
    Organization,
    PlanningRun,
    Supplier,
    SupplierOffer,
    Warehouse,
)
from civitas.persistence.models import (
    ApprovalChallengeModel,
    ApprovalReceiptModel,
    DemandForecastModel,
    OrganizationModel,
    PlanningRunModel,
    SKUModel,
    SupplierModel,
    SupplierOfferModel,
    WarehouseModel,
)

DomainT = TypeVar("DomainT")
ModelT = TypeVar("ModelT")


class SQLAlchemyRepository[DomainT, ModelT]:
    def __init__(
        self,
        session: AsyncSession,
        model_type: type[ModelT],
        to_domain: Callable[[ModelT], DomainT],
        to_model: Callable[[DomainT], ModelT],
    ) -> None:
        self._session = session
        self._model_type = model_type
        self._to_domain = to_domain
        self._to_model = to_model

    async def get(self, entity_id: str) -> DomainT | None:
        row = await self._session.get(self._model_type, entity_id)
        return None if row is None else self._to_domain(row)

    async def add(self, entity: DomainT) -> None:
        self._session.add(self._to_model(entity))

    async def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[DomainT]:
        statement: Select[tuple[ModelT]] = select(self._model_type).limit(limit).offset(offset)
        rows = (await self._session.scalars(statement)).all()
        return tuple(self._to_domain(row) for row in rows)


def _organization_to_domain(row: OrganizationModel) -> Organization:
    return Organization(id=row.id, name=row.name, timezone=row.timezone)


def _organization_to_model(entity: Organization) -> OrganizationModel:
    return OrganizationModel(id=entity.id, name=entity.name, timezone=entity.timezone)


class OrganizationRepository(SQLAlchemyRepository[Organization, OrganizationModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session,
            OrganizationModel,
            _organization_to_domain,
            _organization_to_model,
        )


def _sku_to_domain(row: SKUModel) -> SKU:
    return SKU(
        id=row.id,
        organization_id=row.organization_id,
        code=row.code,
        name=row.name,
        unit_of_measure=row.unit_of_measure,
        base_unit_scale=row.base_unit_scale,
        metadata=row.attributes,
    )


def _sku_to_model(entity: SKU) -> SKUModel:
    return SKUModel(
        id=entity.id,
        organization_id=entity.organization_id,
        code=entity.code,
        name=entity.name,
        unit_of_measure=entity.unit_of_measure,
        base_unit_scale=entity.base_unit_scale,
        attributes=dict(entity.metadata),
    )


class SKURepository(SQLAlchemyRepository[SKU, SKUModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SKUModel, _sku_to_domain, _sku_to_model)


def _warehouse_to_domain(row: WarehouseModel) -> Warehouse:
    return Warehouse(
        id=row.id,
        organization_id=row.organization_id,
        code=row.code,
        name=row.name,
        timezone=row.timezone,
        metadata=row.attributes,
    )


def _warehouse_to_model(entity: Warehouse) -> WarehouseModel:
    return WarehouseModel(
        id=entity.id,
        organization_id=entity.organization_id,
        code=entity.code,
        name=entity.name,
        timezone=entity.timezone,
        attributes=dict(entity.metadata),
    )


class WarehouseRepository(SQLAlchemyRepository[Warehouse, WarehouseModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, WarehouseModel, _warehouse_to_domain, _warehouse_to_model)


def _supplier_to_domain(row: SupplierModel) -> Supplier:
    return Supplier(
        id=row.id,
        organization_id=row.organization_id,
        code=row.code,
        name=row.name,
        metadata=row.attributes,
    )


def _supplier_to_model(entity: Supplier) -> SupplierModel:
    return SupplierModel(
        id=entity.id,
        organization_id=entity.organization_id,
        code=entity.code,
        name=entity.name,
        attributes=dict(entity.metadata),
    )


class SupplierRepository(SQLAlchemyRepository[Supplier, SupplierModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SupplierModel, _supplier_to_domain, _supplier_to_model)


def _planning_run_to_domain(row: PlanningRunModel) -> PlanningRun:
    return PlanningRun(
        id=row.id,
        organization_id=row.organization_id,
        horizon_start=row.horizon_start,
        horizon_end=row.horizon_end,
        bucket_duration=row.bucket_duration,
        timezone=row.timezone,
        input_data_version=row.input_data_version,
        status=row.status,
    )


def _planning_run_to_model(entity: PlanningRun) -> PlanningRunModel:
    return PlanningRunModel(
        id=entity.id,
        organization_id=entity.organization_id,
        horizon_start=entity.horizon_start,
        horizon_end=entity.horizon_end,
        bucket_duration=entity.bucket_duration,
        timezone=entity.timezone,
        input_data_version=entity.input_data_version,
        status=entity.status,
    )


class PlanningRunRepository(SQLAlchemyRepository[PlanningRun, PlanningRunModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PlanningRunModel, _planning_run_to_domain, _planning_run_to_model)


def _forecast_to_domain(row: DemandForecastModel) -> DemandForecast:
    return DemandForecast(
        id=row.id,
        planning_run_id=row.planning_run_id,
        bucket_id=row.bucket_id,
        sku_id=row.sku_id,
        warehouse_id=row.warehouse_id,
        quantity=row.quantity,
        unit_of_measure=row.unit_of_measure,
        priority=row.priority,
        source_version=row.source_version,
        raw_payload=row.raw_payload,
    )


def _forecast_to_model(entity: DemandForecast) -> DemandForecastModel:
    return DemandForecastModel(
        id=entity.id,
        planning_run_id=entity.planning_run_id,
        bucket_id=entity.bucket_id,
        sku_id=entity.sku_id,
        warehouse_id=entity.warehouse_id,
        quantity=entity.quantity,
        unit_of_measure=entity.unit_of_measure,
        priority=entity.priority,
        source_version=entity.source_version,
        raw_payload=None if entity.raw_payload is None else dict(entity.raw_payload),
    )


class DemandForecastRepository(SQLAlchemyRepository[DemandForecast, DemandForecastModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DemandForecastModel, _forecast_to_domain, _forecast_to_model)


def _offer_to_domain(row: SupplierOfferModel) -> SupplierOffer:
    return SupplierOffer(
        id=row.id,
        planning_run_id=row.planning_run_id,
        supplier_id=row.supplier_id,
        sku_id=row.sku_id,
        destination_warehouse_id=row.destination_warehouse_id,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        available_quantity=row.available_quantity,
        unit_of_measure=row.unit_of_measure,
        unit_price=row.unit_price,
        currency=row.currency,
        lead_time=row.lead_time,
        minimum_order_quantity=row.minimum_order_quantity,
        pack_size=row.pack_size,
        expected_shelf_life=row.expected_shelf_life,
        source_version=row.source_version,
        raw_payload=row.raw_payload,
    )


def _offer_to_model(entity: SupplierOffer) -> SupplierOfferModel:
    return SupplierOfferModel(
        id=entity.id,
        planning_run_id=entity.planning_run_id,
        supplier_id=entity.supplier_id,
        sku_id=entity.sku_id,
        destination_warehouse_id=entity.destination_warehouse_id,
        valid_from=entity.valid_from,
        valid_until=entity.valid_until,
        available_quantity=entity.available_quantity,
        unit_of_measure=entity.unit_of_measure,
        unit_price=entity.unit_price,
        currency=entity.currency,
        lead_time=entity.lead_time,
        minimum_order_quantity=entity.minimum_order_quantity,
        pack_size=entity.pack_size,
        expected_shelf_life=entity.expected_shelf_life,
        source_version=entity.source_version,
        raw_payload=None if entity.raw_payload is None else dict(entity.raw_payload),
    )


class SupplierOfferRepository(SQLAlchemyRepository[SupplierOffer, SupplierOfferModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SupplierOfferModel, _offer_to_domain, _offer_to_model)


class ApprovalRepository:
    """Organization-scoped approval ledger queries.

    The explicit predicates are intentional: authorization is enforced at the
    persistence boundary even if a transport adapter is compromised.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def challenge_for_operator_for_update(
        self, *, challenge_id: str, organization_id: str, operator_id: str
    ) -> ApprovalChallengeModel | None:
        return cast(
            ApprovalChallengeModel | None,
            await self._session.scalar(
                select(ApprovalChallengeModel)
                .where(
                    ApprovalChallengeModel.id == challenge_id,
                    ApprovalChallengeModel.organization_id == organization_id,
                    ApprovalChallengeModel.operator_id == operator_id,
                )
                .with_for_update()
            ),
        )

    async def receipt_for_operator_for_update(
        self, *, receipt_id: str, organization_id: str, operator_id: str
    ) -> ApprovalReceiptModel | None:
        return cast(
            ApprovalReceiptModel | None,
            await self._session.scalar(
                select(ApprovalReceiptModel)
                .where(
                    ApprovalReceiptModel.id == receipt_id,
                    ApprovalReceiptModel.organization_id == organization_id,
                    ApprovalReceiptModel.operator_id == operator_id,
                )
                .with_for_update()
            ),
        )

    async def receipt_for_challenge_for_update(
        self, *, challenge_id: str
    ) -> ApprovalReceiptModel | None:
        return cast(
            ApprovalReceiptModel | None,
            await self._session.scalar(
                select(ApprovalReceiptModel)
                .where(ApprovalReceiptModel.challenge_id == challenge_id)
                .with_for_update()
            ),
        )
