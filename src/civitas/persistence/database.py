"""Async engine, session, and unit-of-work lifecycle."""

from types import TracebackType

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from civitas.persistence.inventory import InventoryService
from civitas.persistence.repositories import (
    DemandForecastRepository,
    OrganizationRepository,
    PlanningRunRepository,
    SKURepository,
    SupplierOfferRepository,
    SupplierRepository,
    WarehouseRepository,
)


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.engine: AsyncEngine = create_async_engine(url, echo=echo, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    def unit_of_work(self) -> "SQLAlchemyUnitOfWork":
        return SQLAlchemyUnitOfWork(self.sessions)

    async def dispose(self) -> None:
        await self.engine.dispose()


class SQLAlchemyUnitOfWork:
    """Own exactly one session for one request or workflow transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> "SQLAlchemyUnitOfWork":
        self.session = self._sessions()
        await self.session.begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        try:
            if exc_type is None:
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()
            self.session = None

    def require_session(self) -> AsyncSession:
        if self.session is None:
            raise RuntimeError("unit of work has not been entered")
        return self.session

    @property
    def organizations(self) -> OrganizationRepository:
        return OrganizationRepository(self.require_session())

    @property
    def skus(self) -> SKURepository:
        return SKURepository(self.require_session())

    @property
    def warehouses(self) -> WarehouseRepository:
        return WarehouseRepository(self.require_session())

    @property
    def suppliers(self) -> SupplierRepository:
        return SupplierRepository(self.require_session())

    @property
    def planning_runs(self) -> PlanningRunRepository:
        return PlanningRunRepository(self.require_session())

    @property
    def demand_forecasts(self) -> DemandForecastRepository:
        return DemandForecastRepository(self.require_session())

    @property
    def supplier_offers(self) -> SupplierOfferRepository:
        return SupplierOfferRepository(self.require_session())

    @property
    def inventory(self) -> InventoryService:
        return InventoryService(self.require_session())

    async def commit(self) -> None:
        await self.require_session().commit()

    async def rollback(self) -> None:
        await self.require_session().rollback()
