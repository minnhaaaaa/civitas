"""SQLAlchemy mappings for the canonical PostgreSQL record."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Interval,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ID = String(64)
CODE = String(128)
QUANTITY = Numeric(24, 8)
MONEY = Numeric(24, 8)


class Base(DeclarativeBase):
    """Base for all persistence mappings."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrganizationModel(TimestampMixin, Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)


class SKUModel(TimestampMixin, Base):
    __tablename__ = "skus"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_skus_org_code"),
        CheckConstraint("base_unit_scale > 0", name="ck_skus_base_unit_scale_positive"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(CODE, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(32), nullable=False)
    base_unit_scale: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class WarehouseModel(TimestampMixin, Base):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_warehouses_org_code"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(CODE, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class SupplierModel(TimestampMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_suppliers_org_code"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(CODE, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class PlanningRunModel(TimestampMixin, Base):
    __tablename__ = "planning_runs"
    __table_args__ = (
        CheckConstraint("horizon_end > horizon_start", name="ck_planning_runs_horizon"),
        CheckConstraint(
            "bucket_duration > interval '0 seconds'", name="ck_planning_runs_bucket_duration"
        ),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    horizon_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_duration: Mapped[timedelta] = mapped_column(Interval, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    input_data_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")


class PlanningBucketModel(Base):
    __tablename__ = "planning_buckets"
    __table_args__ = (
        UniqueConstraint("planning_run_id", "sequence", name="uq_planning_buckets_sequence"),
        UniqueConstraint("planning_run_id", "starts_at", name="uq_planning_buckets_start"),
        CheckConstraint("sequence >= 0", name="ck_planning_buckets_sequence"),
        CheckConstraint("ends_at > starts_at", name="ck_planning_buckets_bounds"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    planning_run_id: Mapped[str] = mapped_column(
        ForeignKey("planning_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DemandForecastModel(TimestampMixin, Base):
    __tablename__ = "demand_forecasts"
    __table_args__ = (
        UniqueConstraint(
            "planning_run_id",
            "bucket_id",
            "sku_id",
            "warehouse_id",
            "source_version",
            name="uq_demand_forecasts_version",
        ),
        CheckConstraint("quantity >= 0", name="ck_demand_forecasts_quantity"),
        CheckConstraint("priority > 0", name="ck_demand_forecasts_priority"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    planning_run_id: Mapped[str] = mapped_column(ForeignKey("planning_runs.id"), nullable=False)
    bucket_id: Mapped[str] = mapped_column(ForeignKey("planning_buckets.id"), nullable=False)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("1"))
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class SupplierOfferModel(TimestampMixin, Base):
    __tablename__ = "supplier_offers"
    __table_args__ = (
        UniqueConstraint(
            "planning_run_id",
            "supplier_id",
            "sku_id",
            "destination_warehouse_id",
            "source_version",
            name="uq_supplier_offers_version",
        ),
        CheckConstraint("valid_until > valid_from", name="ck_supplier_offers_validity"),
        CheckConstraint("available_quantity >= 0", name="ck_supplier_offers_available"),
        CheckConstraint("unit_price >= 0", name="ck_supplier_offers_price"),
        CheckConstraint("minimum_order_quantity >= 0", name="ck_supplier_offers_moq"),
        CheckConstraint("pack_size > 0", name="ck_supplier_offers_pack_size"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    planning_run_id: Mapped[str] = mapped_column(ForeignKey("planning_runs.id"), nullable=False)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), nullable=False)
    destination_warehouse_id: Mapped[str] = mapped_column(
        ForeignKey("warehouses.id"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    lead_time: Mapped[timedelta] = mapped_column(Interval, nullable=False)
    minimum_order_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    pack_size: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    expected_shelf_life: Mapped[timedelta | None] = mapped_column(Interval)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class InventoryLotModel(TimestampMixin, Base):
    __tablename__ = "inventory_lots"
    __table_args__ = (
        CheckConstraint("initial_quantity > 0", name="ck_inventory_lots_initial_quantity"),
        CheckConstraint("expires_at > received_at", name="ck_inventory_lots_expiry"),
        CheckConstraint(
            "expiry_kind IN ('use_by', 'best_before')", name="ck_inventory_lots_expiry_kind"
        ),
        CheckConstraint(
            "status IN ('available', 'reserved', 'quarantined', 'expired', 'depleted')",
            name="ck_inventory_lots_status",
        ),
        Index("ix_inventory_lots_fefo", "warehouse_id", "sku_id", "expires_at", "id"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    manufactured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expiry_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    initial_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(255))


class InventoryMovementModel(TimestampMixin, Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            "lot_id",
            name="uq_inventory_movements_idempotency",
        ),
        CheckConstraint(
            "quantity_delta <> 0 OR reserved_delta <> 0", name="ck_inventory_movements_nonzero"
        ),
        CheckConstraint(
            "movement_type IN ('receipt', 'reservation', 'release', 'shipment', "
            "'transfer', 'waste', 'adjustment')",
            name="ck_inventory_movements_type",
        ),
        Index("ix_inventory_movements_lot_occurred", "lot_id", "occurred_at", "id"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    lot_id: Mapped[str] = mapped_column(
        ForeignKey("inventory_lots.id", ondelete="RESTRICT"), nullable=False
    )
    movement_type: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False, default=Decimal("0"))
    reserved_delta: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False, default=Decimal("0"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    business_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)


class InventoryReservationModel(TimestampMixin, Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_inventory_reservations_idempotency"
        ),
        CheckConstraint("quantity > 0", name="ck_inventory_reservations_quantity"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    business_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)


class SourceModel(TimestampMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("organization_id", "canonical_source_id", name="uq_sources_canonical"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    canonical_source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    upstream_dataset: Mapped[str | None] = mapped_column(String(255))


class MCPCallModel(TimestampMixin, Base):
    __tablename__ = "mcp_calls"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    planning_run_id: Mapped[str] = mapped_column(ForeignKey("planning_runs.id"), nullable=False)
    server: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    response_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ClaimModel(TimestampMixin, Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(
            "(valid_at IS NOT NULL AND valid_from IS NULL AND valid_until IS NULL) OR "
            "(valid_at IS NULL AND valid_from IS NOT NULL AND valid_until > valid_from)",
            name="ck_claims_validity",
        ),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    planning_run_id: Mapped[str] = mapped_column(ForeignKey("planning_runs.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    sku_id: Mapped[str | None] = mapped_column(ForeignKey("skus.id"))
    warehouse_id: Mapped[str | None] = mapped_column(ForeignKey("warehouses.id"))
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"))
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    predicate: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32))
    valid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    human_summary: Mapped[str] = mapped_column(Text, nullable=False)
    materiality: Mapped[str] = mapped_column(String(32), nullable=False, default="noncritical")


class EvidenceModel(TimestampMixin, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint(
            "planning_run_id",
            "source_id",
            "raw_response_sha256",
            "observation_version",
            name="uq_evidence_identity",
        ),
        CheckConstraint("origin IN ('external', 'agent_derived')", name="ck_evidence_origin"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    planning_run_id: Mapped[str] = mapped_column(ForeignKey("planning_runs.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    mcp_call_id: Mapped[str | None] = mapped_column(ForeignKey("mcp_calls.id"))
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(128))
    content_summary: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    raw_response_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class EvidenceClaimModel(Base):
    __tablename__ = "evidence_claims"
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
    claim_id: Mapped[str] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True
    )
    relationship: Mapped[str] = mapped_column(String(32), nullable=False, default="supports")


class LineageEdgeModel(Base):
    __tablename__ = "lineage_edges"
    __table_args__ = (
        UniqueConstraint(
            "planning_run_id",
            "from_type",
            "from_id",
            "relationship",
            "to_type",
            "to_id",
            name="uq_lineage_edges",
        ),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    planning_run_id: Mapped[str] = mapped_column(ForeignKey("planning_runs.id"), nullable=False)
    from_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_id: Mapped[str] = mapped_column(ID, nullable=False)
    relationship: Mapped[str] = mapped_column(String(32), nullable=False)
    to_type: Mapped[str] = mapped_column(String(32), nullable=False)
    to_id: Mapped[str] = mapped_column(ID, nullable=False)


class CandidatePlanModel(TimestampMixin, Base):
    __tablename__ = "candidate_plans"
    __table_args__ = (
        UniqueConstraint("planning_run_id", "stable_key", name="uq_candidate_plans_stable"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    planning_run_id: Mapped[str] = mapped_column(ForeignKey("planning_runs.id"), nullable=False)
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    feasibility: Mapped[str] = mapped_column(String(32), nullable=False)
    shortage_base_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    solver_version: Mapped[str] = mapped_column(String(128), nullable=False)
    selected: Mapped[bool] = mapped_column(nullable=False, default=False)


class ProcurementLineModel(Base):
    __tablename__ = "procurement_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND landed_cost >= 0", name="ck_procurement_lines_values"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_plans.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), nullable=False)
    destination_warehouse_id: Mapped[str] = mapped_column(
        ForeignKey("warehouses.id"), nullable=False
    )
    arrival_bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(32), nullable=False)
    landed_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


class DistributionLineModel(Base):
    __tablename__ = "distribution_lines"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_distribution_lines_quantity"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_plans.id", ondelete="CASCADE"), nullable=False
    )
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), nullable=False)
    source_warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    destination_warehouse_id: Mapped[str] = mapped_column(
        ForeignKey("warehouses.id"), nullable=False
    )
    departure_bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    arrival_bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(32), nullable=False)
    source_lot_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class JuryDecisionModel(TimestampMixin, Base):
    __tablename__ = "jury_decisions"
    __table_args__ = (
        UniqueConstraint(
            "planning_run_id", "plan_id", "policy_version", name="uq_jury_decisions_version"
        ),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    planning_run_id: Mapped[str] = mapped_column(ForeignKey("planning_runs.id"), nullable=False)
    plan_id: Mapped[str] = mapped_column(ForeignKey("candidate_plans.id"), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    implementation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    component_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    integrity_score: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    gate_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    final_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    per_claim_contributions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class DissentInvestigationModel(TimestampMixin, Base):
    """Append-only clean-room phase audit; raw evidence remains in evidence tables."""

    __tablename__ = "dissent_investigations"
    __table_args__ = (
        UniqueConstraint("planning_run_id", "cycle_key", "phase", name="uq_dissent_phase_audit"),
        CheckConstraint(
            "phase IN ('plan_recorded', 'fresh_retrieval_complete', "
            "'comparison_complete', 'failed')",
            name="ck_dissent_investigations_phase",
        ),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    planning_run_id: Mapped[str] = mapped_column(
        ForeignKey("planning_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cycle_key: Mapped[str] = mapped_column(String(128), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class WorkflowEventModel(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("planning_run_id", "sequence", name="uq_workflow_events_sequence"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    planning_run_id: Mapped[str] = mapped_column(ForeignKey("planning_runs.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    causation_id: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class WorkflowCheckpointModel(TimestampMixin, Base):
    """Durable queue state for one resumable planning workflow."""

    __tablename__ = "workflow_checkpoints"
    __table_args__ = (
        CheckConstraint("cycle >= 1", name="ck_workflow_checkpoints_cycle"),
        CheckConstraint("event_sequence >= 0", name="ck_workflow_checkpoints_event_sequence"),
        CheckConstraint("attempt_count >= 0", name="ck_workflow_checkpoints_attempt_count"),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL AND "
            "lease_expires_at IS NOT NULL)",
            name="ck_workflow_checkpoints_lease_complete",
        ),
        Index(
            "ix_workflow_checkpoints_queue",
            "completed",
            "available_at",
            "lease_expires_at",
        ),
    )
    planning_run_id: Mapped[str] = mapped_column(
        ForeignKey("planning_runs.id", ondelete="CASCADE"), primary_key=True
    )
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    workflow_limits: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    procurement_goal: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    policy_version: Mapped[str | None] = mapped_column(String(64))
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    event_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[str | None] = mapped_column(String(128), unique=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ServiceHeartbeatModel(Base):
    """Operational process presence; never used as execution authority."""

    __tablename__ = "service_heartbeats"
    __table_args__ = (
        CheckConstraint("service_kind IN ('mcp-server', 'worker')", name="ck_heartbeat_kind"),
        CheckConstraint("state IN ('starting', 'running', 'stopping')", name="ck_heartbeat_state"),
        Index("ix_service_heartbeats_kind_seen", "service_kind", "last_seen_at"),
    )
    service_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    service_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AuditLinkModel(Base):
    """Immutable, expiring capability for one organization-scoped audit snapshot."""

    __tablename__ = "audit_links"
    __table_args__ = (
        CheckConstraint("expires_at > issued_at", name="ck_audit_links_expiry"),
        CheckConstraint("maximum_event_sequence >= 0", name="ck_audit_links_cursor"),
        Index("ix_audit_links_org_run", "organization_id", "planning_run_id"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    reference_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    planning_run_id: Mapped[str] = mapped_column(
        ForeignKey("planning_runs.id", ondelete="RESTRICT"), nullable=False
    )
    selected_plan_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_plans.id", ondelete="RESTRICT"), nullable=False
    )
    maximum_event_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExecutionAuditModel(TimestampMixin, Base):
    __tablename__ = "execution_audits"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_execution_audits_idempotency"
        ),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    planning_run_id: Mapped[str] = mapped_column(ForeignKey("planning_runs.id"), nullable=False)
    approved_plan_id: Mapped[str] = mapped_column(ForeignKey("candidate_plans.id"), nullable=False)
    jury_decision_id: Mapped[str] = mapped_column(ForeignKey("jury_decisions.id"), nullable=False)
    approval_receipt_id: Mapped[str | None] = mapped_column(
        ForeignKey("approval_receipts.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    approval_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(128))
    compensation_status: Mapped[str | None] = mapped_column(String(32))
    external_references: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class ExecutionAuditEventModel(Base):
    """Append-only state transition history for an execution attempt."""

    __tablename__ = "execution_audit_events"
    __table_args__ = (
        UniqueConstraint("execution_id", "sequence", name="uq_execution_events_sequence"),
        CheckConstraint("sequence > 0", name="ck_execution_events_sequence_positive"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("execution_audits.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[str | None] = mapped_column(String(500))


class ProviderWriteModel(Base):
    """Durable ledger for each idempotent outbound provider write."""

    __tablename__ = "provider_writes"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_provider_writes_idempotency"
        ),
        CheckConstraint(
            "state IN ('pending', 'succeeded', 'failed', 'compensation_required', 'compensated')",
            name="ck_provider_writes_state",
        ),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("execution_audits.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_id: Mapped[str] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_reference: Mapped[str | None] = mapped_column(String(255))
    failure_code: Mapped[str | None] = mapped_column(String(128))


class ApprovalChallengeModel(Base):
    """A short-lived, organization-scoped authorization for one immutable plan."""

    __tablename__ = "approval_challenges"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'approved', 'invalidated', 'expired')",
            name="ck_approval_challenges_state",
        ),
        CheckConstraint("expires_at > issued_at", name="ck_approval_challenges_expiry"),
        Index("ix_approval_challenges_org_run", "organization_id", "planning_run_id"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    operator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    planning_run_id: Mapped[str] = mapped_column(
        ForeignKey("planning_runs.id", ondelete="RESTRICT"), nullable=False
    )
    selected_plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_totals: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(String(128))


class ApprovalReceiptModel(Base):
    """Persisted evidence that a challenge was approved by its bound operator."""

    __tablename__ = "approval_receipts"
    __table_args__ = (
        UniqueConstraint("challenge_id", name="uq_approval_receipts_challenge"),
        CheckConstraint("expires_at > approved_at", name="ck_approval_receipts_expiry"),
        Index("ix_approval_receipts_org_run", "organization_id", "planning_run_id"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    challenge_id: Mapped[str] = mapped_column(
        ForeignKey("approval_challenges.id", ondelete="RESTRICT"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    operator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    planning_run_id: Mapped[str] = mapped_column(
        ForeignKey("planning_runs.id", ondelete="RESTRICT"), nullable=False
    )
    selected_plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_totals: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_idempotency_key: Mapped[str | None] = mapped_column(String(255))
