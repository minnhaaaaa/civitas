"""Idempotently provision the local simulator tenant and catalog."""

from __future__ import annotations

import asyncio
import os
import sys

from civitas.persistence.database import Database
from civitas.persistence.models import (
    OrganizationModel,
    SKUModel,
    SupplierModel,
    WarehouseModel,
)


async def seed() -> None:
    if os.getenv("CIVITAS_ENV", "development") == "production":
        raise RuntimeError("demo tenant provisioning is forbidden in production")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    organization_id = os.getenv("CIVITAS_ORGANIZATION_ID", "org-local")
    sku_id = os.getenv("CIVITAS_SIMULATOR_SKU_ID", "sku-local")
    warehouse_id = os.getenv("CIVITAS_SIMULATOR_WAREHOUSE_ID", "warehouse-local")
    supplier_id = os.getenv("CIVITAS_SIMULATOR_SUPPLIER_ID", "supplier-local")
    database = Database(database_url)
    try:
        async with database.sessions() as session, session.begin():
            organization = await session.get(OrganizationModel, organization_id)
            if organization is None:
                session.add(
                    OrganizationModel(id=organization_id, name="Civitas local demo", timezone="UTC")
                )
            elif organization.timezone != "UTC":
                raise RuntimeError("existing demo organization must use UTC")
            await session.flush()
            if await session.get(SKUModel, sku_id) is None:
                session.add(
                    SKUModel(
                        id=sku_id,
                        organization_id=organization_id,
                        code="DEMO-SKU",
                        name="Demo food item",
                        unit_of_measure="each",
                        base_unit_scale=1,
                        attributes={"temperature_zone": "ambient"},
                    )
                )
            if await session.get(WarehouseModel, warehouse_id) is None:
                session.add(
                    WarehouseModel(
                        id=warehouse_id,
                        organization_id=organization_id,
                        code="DEMO-WH",
                        name="Demo warehouse",
                        timezone="UTC",
                        attributes={"capacity_units": 100},
                    )
                )
            if await session.get(SupplierModel, supplier_id) is None:
                session.add(
                    SupplierModel(
                        id=supplier_id,
                        organization_id=organization_id,
                        code="DEMO-SUPPLIER",
                        name="Demo supplier",
                        attributes={"simulated": True},
                    )
                )
    finally:
        await database.dispose()


def main() -> int:
    try:
        asyncio.run(seed())
    except Exception as error:
        print(f"Civitas demo provisioning error: {error}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
