from datetime import UTC, datetime, timedelta

import pytest

from civitas.optimization import PlanningBucket


@pytest.fixture
def bucket() -> PlanningBucket:
    start = datetime(2026, 8, 27, tzinfo=UTC)
    return PlanningBucket(
        bucket_id="day-1",
        start=start,
        end=start + timedelta(days=1),
        urgency=2,
    )
