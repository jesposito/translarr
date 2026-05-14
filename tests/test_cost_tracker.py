import pytest

from server import cost_tracker


@pytest.fixture(autouse=True)
def _reset():
    cost_tracker.reset_for_tests()
    yield
    cost_tracker.reset_for_tests()


def test_estimate_uses_known_model_pricing():
    cents = cost_tracker.estimate_cents("claude-sonnet-4-6", 1_000_000, 1_000_000)
    # 300c input + 1500c output = 1800c
    assert cents == 1800


def test_estimate_unknown_model_defaults_to_sonnet():
    cents = cost_tracker.estimate_cents("unknown-model", 1_000_000, 0)
    assert cents == 300


def test_record_accumulates_per_day():
    cost_tracker.record(100)
    cost_tracker.record(50)
    assert cost_tracker.daily_total_cents() == 150


def test_check_daily_cap_raises_after_cap():
    cost_tracker.record(1000)
    with pytest.raises(cost_tracker.CostCapExceeded):
        cost_tracker.check_daily_cap(max_daily_cents=1000)


def test_check_daily_cap_passes_under_cap():
    cost_tracker.record(500)
    # Should not raise
    cost_tracker.check_daily_cap(max_daily_cents=1000)


def test_check_job_cap_raises_over_estimate():
    with pytest.raises(cost_tracker.CostCapExceeded):
        cost_tracker.check_job_cap(estimated_cents=600, max_job_cents=500)


def test_check_job_cap_passes_under_estimate():
    cost_tracker.check_job_cap(estimated_cents=499, max_job_cents=500)
