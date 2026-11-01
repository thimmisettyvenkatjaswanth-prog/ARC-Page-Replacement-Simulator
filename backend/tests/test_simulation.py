"""
Unit and integration test suite for SimulationEngine and WorkloadGenerator.
Verifies trace recording, metric calculations, safe divisions, fair comparisons,
and deterministic workload generators.
"""
import pytest
from backend.algorithms.arc import ARCCache
from backend.algorithms.lru import LRUCache
from backend.simulation.engine import SimulationEngine
from backend.simulation.workload import (
    generate_custom,
    generate_looping,
    generate_random,
    generate_repeated_locality,
    generate_sequential,
    generate_workload,
)


# =====================================================================
# SIMULATION ENGINE TESTS
# =====================================================================

def test_empty_reference_string_and_zero_division():
    """Verify simulation engine handles empty reference string with zero-division safety."""
    lru = LRUCache(3)
    engine = SimulationEngine(lru, [])
    result = engine.run()

    metrics = result["metrics"]
    assert metrics["total_requests"] == 0
    assert metrics["total_hits"] == 0
    assert metrics["total_misses"] == 0
    assert metrics["total_page_faults"] == 0
    assert metrics["total_replacements"] == 0
    assert metrics["hit_ratio"] == 0.0
    assert metrics["miss_ratio"] == 0.0
    assert metrics["page_fault_rate"] == 0.0
    assert "execution_time_ms" in metrics
    assert result["steps"] == []


def test_single_page_request():
    """Verify simulation with a single page reference."""
    lru = LRUCache(2)
    engine = SimulationEngine(lru, [42])
    result = engine.run()

    assert len(result["steps"]) == 1
    step1 = result["steps"][0]
    assert step1["step"] == 1
    assert step1["page"] == 42
    assert step1["hit"] is False
    assert step1["cumulative_hits"] == 0
    assert step1["cumulative_misses"] == 1
    assert step1["cumulative_hit_ratio"] == 0.0
    assert result["metrics"]["total_requests"] == 1
    assert result["metrics"]["total_hits"] == 0
    assert result["metrics"]["total_misses"] == 1
    assert result["metrics"]["hit_ratio"] == 0.0
    assert result["metrics"]["miss_ratio"] == 1.0


def test_all_hits_after_warmup():
    """Verify simulation where all requests hit after initial cache loading."""
    lru = LRUCache(3)
    # Warmup with 1, 2, then repeatedly reference 1 and 2
    trace = [1, 2, 1, 2, 1, 2]
    engine = SimulationEngine(lru, trace)
    result = engine.run()

    metrics = result["metrics"]
    assert metrics["total_requests"] == 6
    assert metrics["total_hits"] == 4
    assert metrics["total_misses"] == 2
    assert metrics["total_replacements"] == 0
    assert metrics["hit_ratio"] == round(4 / 6, 4)
    assert metrics["miss_ratio"] == round(2 / 6, 4)


def test_all_misses_for_unique_pages():
    """Verify simulation where every page is unique, causing 100% misses."""
    lru = LRUCache(2)
    trace = [10, 20, 30, 40, 50]
    engine = SimulationEngine(lru, trace)
    result = engine.run()

    metrics = result["metrics"]
    assert metrics["total_requests"] == 5
    assert metrics["total_hits"] == 0
    assert metrics["total_misses"] == 5
    assert metrics["total_replacements"] == 3  # pages 30, 40, 50 cause evictions
    assert metrics["hit_ratio"] == 0.0
    assert metrics["miss_ratio"] == 1.0


def test_correct_lru_eviction_in_simulation():
    """Verify engine records exact LRU evictions and list movements."""
    lru = LRUCache(2)
    trace = [1, 2, 1, 3]
    engine = SimulationEngine(lru, trace)
    result = engine.run()

    steps = result["steps"]
    # Step 1: Miss on 1 -> cache: [1]
    assert steps[0]["hit"] is False
    assert steps[0]["evicted_page"] is None

    # Step 2: Miss on 2 -> cache: [2, 1]
    assert steps[1]["hit"] is False
    assert steps[1]["evicted_page"] is None

    # Step 3: Hit on 1 -> cache: [1, 2]
    assert steps[2]["hit"] is True
    assert steps[2]["evicted_page"] is None

    # Step 4: Miss on 3 -> cache full, 2 was LRU -> evicts 2
    assert steps[3]["hit"] is False
    assert steps[3]["evicted_page"] == 2
    assert steps[3]["pages"] == [3, 1]


def test_arc_simulation_state_recording():
    """Verify simulation engine records algorithm-specific ARC state (T1, T2, B1, B2, p)."""
    arc = ARCCache(3)
    trace = [1, 2, 3, 1]
    engine = SimulationEngine(arc, trace)
    result = engine.run()

    steps = result["steps"]
    assert len(steps) == 4

    # Step 4 is a hit on page 1 (promoted from T1 to T2)
    step4 = steps[3]
    assert step4["page"] == 1
    assert step4["hit"] is True
    assert "p" in step4
    assert "T1" in step4
    assert "T2" in step4
    assert "B1" in step4
    assert "B2" in step4
    assert 1 in step4["T2"]
    assert 1 not in step4["T1"]


def test_fair_comparison_arc_vs_lru():
    """
    Verify fair side-by-side comparison:
    Both algorithms receive identical reference string and identical capacity.
    """
    capacity = 4
    reference_string = [1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5]

    arc = ARCCache(capacity)
    lru = LRUCache(capacity)

    engine_arc = SimulationEngine(arc, reference_string)
    res_arc = engine_arc.run()

    engine_lru = SimulationEngine(lru, reference_string)
    res_lru = engine_lru.run()

    # Both must process the exact same number of requests
    assert res_arc["metrics"]["total_requests"] == len(reference_string)
    assert res_lru["metrics"]["total_requests"] == len(reference_string)

    # ARC and LRU step streams must match on requested page per step
    for step_a, step_l in zip(res_arc["steps"], res_lru["steps"]):
        assert step_a["step"] == step_l["step"]
        assert step_a["page"] == step_l["page"]


def test_engine_initialization_validation():
    """Verify engine validates algorithm presence."""
    with pytest.raises(ValueError):
        SimulationEngine(None)  # type: ignore


# =====================================================================
# WORKLOAD GENERATOR TESTS
# =====================================================================

def test_workload_custom():
    """Verify custom workload generation with optional length repetition."""
    pages = [1, 2, 3]
    w1 = generate_custom(pages)
    assert w1 == [1, 2, 3]

    w2 = generate_custom(pages, length=5)
    assert w2 == [1, 2, 3, 1, 2]
    assert len(w2) == 5

    w3 = generate_custom(pages, length=2)
    assert w3 == [1, 2]


def test_workload_sequential():
    """Verify sequential workload cycles across num_pages."""
    w = generate_sequential(length=7, num_pages=3, start_page=10)
    assert len(w) == 7
    assert w == [10, 11, 12, 10, 11, 12, 10]


def test_workload_random_and_seed_determinism():
    """Verify random workload length and deterministic seed reproducibility."""
    length = 20
    num_pages = 5
    seed = 42

    w1 = generate_random(length, num_pages, start_page=1, seed=seed)
    w2 = generate_random(length, num_pages, start_page=1, seed=seed)

    assert len(w1) == length
    assert len(w2) == length
    # Deterministic: identical seeds produce identical traces
    assert w1 == w2
    # Verify all generated pages fall within range [1, 5]
    for page in w1:
        assert 1 <= page <= 5

    # Different seed produces different trace
    w3 = generate_random(length, num_pages, start_page=1, seed=999)
    assert w1 != w3


def test_workload_repeated_locality():
    """Verify repeated locality generates working-set skewed references."""
    length = 100
    num_pages = 10
    hot_pages_count = 2  # Hot set: [1, 2]
    hot_prob = 0.9       # 90% hot references

    w = generate_repeated_locality(
        length=length,
        num_pages=num_pages,
        hot_pages_count=hot_pages_count,
        hot_prob=hot_prob,
        start_page=1,
        seed=123
    )

    assert len(w) == length
    hot_count = sum(1 for p in w if p in (1, 2))
    # With 90% probability over 100 samples, hot references should dominate (> 75%)
    assert hot_count >= 75


def test_workload_looping():
    """Verify looping workload generates cyclic sequence."""
    w1 = generate_looping(length=8, loop=3, start_page=1)
    assert w1 == [1, 2, 3, 1, 2, 3, 1, 2]
    assert len(w1) == 8

    w2 = generate_looping(length=6, loop=["A", "B"])
    assert w2 == ["A", "B", "A", "B", "A", "B"]


def test_workload_factory_function():
    """Verify unified generate_workload factory generates expected types."""
    w_seq = generate_workload("sequential", length=4, num_pages=2)
    assert w_seq == [1, 2, 1, 2]

    w_loop = generate_workload("looping", length=5, loop=2)
    assert w_loop == [1, 2, 1, 2, 1]

    w_rand = generate_workload("random", length=10, num_pages=4, seed=7)
    assert len(w_rand) == 10

    w_cust = generate_workload("custom", length=3, pages=[10, 20])
    assert w_cust == [10, 20, 10]

    with pytest.raises(ValueError):
        generate_workload("unknown_type", length=5)


def test_workload_invalid_parameters():
    """Verify validation errors for non-positive or malformed parameters."""
    with pytest.raises(ValueError):
        generate_sequential(length=-1, num_pages=5)
    with pytest.raises(ValueError):
        generate_sequential(length=10, num_pages=0)
    with pytest.raises(ValueError):
        generate_random(length=0, num_pages=5)
    with pytest.raises(ValueError):
        generate_custom([])
    with pytest.raises(ValueError):
        generate_custom(None)  # type: ignore
    with pytest.raises(ValueError):
        generate_repeated_locality(length=10, num_pages=5, hot_pages_count=10)  # > num_pages
    with pytest.raises(ValueError):
        generate_repeated_locality(length=10, num_pages=5, hot_pages_count=2, hot_prob=1.5)  # > 1.0

