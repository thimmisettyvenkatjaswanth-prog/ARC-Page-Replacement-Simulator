"""
Unit, invariant, and simulation compatibility test suite for FIFO Cache.
Verifies first-in first-out eviction, arrival order preservation on hits,
and seamless integration with SimulationEngine.
"""
import pytest
import random
from backend.algorithms.fifo import FIFOCache
from backend.algorithms.lru import LRUCache
from backend.simulation.engine import SimulationEngine


def test_capacity_validation():
    """Verify capacity validation enforces positive integers."""
    with pytest.raises(ValueError):
        FIFOCache(0)
    with pytest.raises(ValueError):
        FIFOCache(-2)
    with pytest.raises(ValueError):
        FIFOCache("3")  # type: ignore
    with pytest.raises(ValueError):
        FIFOCache(2.5)  # type: ignore

    fifo = FIFOCache(4)
    assert fifo.capacity == 4


def test_initial_empty_cache():
    """Verify initial state of an empty FIFO cache."""
    fifo = FIFOCache(3)
    st = fifo.get_state()
    assert st["capacity"] == 3
    assert st["size"] == 0
    assert st["pages"] == []
    assert st["oldest"] is None
    assert st["newest"] is None
    assert st["requests"] == 0
    assert st["hits"] == 0
    assert st["misses"] == 0
    assert st["replacements"] == 0
    assert st["hit_ratio"] == 0.0


def test_first_page_insertion():
    """Verify first page reference causes a miss and becomes both oldest and newest."""
    fifo = FIFOCache(3)
    res = fifo.request("A")
    assert res["page"] == "A"
    assert res["hit"] is False
    assert res["hit_type"] == "MISS"
    assert res["evicted_page"] is None

    st = fifo.get_state()
    assert st["size"] == 1
    assert st["pages"] == ["A"]
    assert st["oldest"] == "A"
    assert st["newest"] == "A"
    assert fifo.requests == 1
    assert fifo.misses == 1
    assert fifo.hits == 0


def test_page_fault_when_cache_not_full():
    """Verify cold misses append pages in arrival order without eviction."""
    fifo = FIFOCache(3)
    fifo.request(1)
    fifo.request(2)
    res3 = fifo.request(3)

    assert res3["hit"] is False
    assert res3["evicted_page"] is None
    st = fifo.get_state()
    assert st["size"] == 3
    assert st["oldest_to_newest"] == [1, 2, 3]  # 1 arrived first, 3 arrived last
    assert st["oldest"] == 1
    assert st["newest"] == 3
    assert fifo.replacements == 0


def test_page_hit_preserves_fifo_order():
    """
    Verify critical FIFO property:
    A page hit DOES NOT change the arrival order.
    """
    fifo = FIFOCache(3)
    fifo.request(1)
    fifo.request(2)
    fifo.request(3)
    # Arrival order: [1, 2, 3] (1 is oldest, 3 is newest)

    # Access page 1 (hit!)
    res = fifo.request(1)
    assert res["hit"] is True
    assert res["hit_type"] == "HIT"
    assert res["evicted_page"] is None

    st = fifo.get_state()
    # In FIFO, 1 MUST REMAIN at the oldest position (head), NOT promoted to newest!
    assert st["oldest_to_newest"] == [1, 2, 3]
    assert st["oldest"] == 1
    assert st["newest"] == 3
    assert fifo.hits == 1
    assert fifo.misses == 3


def test_fifo_vs_lru_behavioral_difference():
    """
    Direct behavioral comparison proving FIFO does not promote on hit,
    causing different eviction outcomes than LRU under identical inputs.
    """
    fifo = FIFOCache(3)
    lru = LRUCache(3)
    # Fill both with 1, 2, 3
    for p in [1, 2, 3]:
        fifo.request(p)
        lru.request(p)

    # Both hit on 1
    fifo.request(1)
    lru.request(1)

    # Request 4 (cache full):
    # In FIFO: 1 was never reordered, so 1 is still the oldest -> EVICTS 1!
    res_fifo = fifo.request(4)
    assert res_fifo["evicted_page"] == 1
    assert 1 not in fifo.get_state()["pages"]

    # In LRU: hit on 1 promoted it to MRU, so 2 became the LRU page -> EVICTS 2!
    res_lru = lru.request(4)
    assert res_lru["evicted_page"] == 2
    assert 1 in lru.get_state()["pages"]
    assert 2 not in lru.get_state()["pages"]


def test_eviction_of_correct_oldest_page():
    """Verify FIFO strictly evicts pages in order of initial arrival."""
    fifo = FIFOCache(3)
    fifo.request("A")
    fifo.request("B")
    fifo.request("C")
    # Oldest to newest: [A, B, C]

    # Miss on D: evicts A (oldest)
    res_d = fifo.request("D")
    assert res_d["evicted_page"] == "A"
    assert fifo.get_state()["oldest_to_newest"] == ["B", "C", "D"]

    # Miss on E: evicts B (next oldest)
    res_e = fifo.request("E")
    assert res_e["evicted_page"] == "B"
    assert fifo.get_state()["oldest_to_newest"] == ["C", "D", "E"]


def test_repeated_page_hits():
    """Verify repeated hits increment hit counter without altering arrival order."""
    fifo = FIFOCache(3)
    fifo.request(10)
    fifo.request(20)

    for _ in range(5):
        res = fifo.request(10)
        assert res["hit"] is True
        st = fifo.get_state()
        assert st["oldest_to_newest"] == [10, 20]
        assert st["oldest"] == 10
        assert st["newest"] == 20

    assert fifo.hits == 5
    assert fifo.misses == 2
    assert fifo.requests == 7


def test_capacity_one():
    """Verify FIFO operation with minimal capacity c = 1."""
    fifo = FIFOCache(1)
    res1 = fifo.request(1)
    assert res1["hit"] is False
    assert res1["evicted_page"] is None
    assert fifo.get_state()["pages"] == [1]

    # Next access evicts 1
    res2 = fifo.request(2)
    assert res2["hit"] is False
    assert res2["evicted_page"] == 1
    assert fifo.get_state()["pages"] == [2]

    # Hit on 2
    res3 = fifo.request(2)
    assert res3["hit"] is True
    assert fifo.get_state()["pages"] == [2]


def test_reset_functionality():
    """Verify reset clears resident pages and zeroes all counters."""
    fifo = FIFOCache(3)
    for p in [1, 2, 3, 1, 4]:
        fifo.request(p)

    assert fifo.requests == 5
    assert fifo.hits == 1
    assert fifo.misses == 4
    assert fifo.replacements == 1

    fifo.reset()
    st = fifo.get_state()
    assert st["size"] == 0
    assert st["pages"] == []
    assert st["oldest"] is None
    assert st["newest"] is None
    assert fifo.requests == 0
    assert fifo.hits == 0
    assert fifo.misses == 0
    assert fifo.replacements == 0
    assert fifo.hit_ratio == 0.0


def test_counter_accounting():
    """Verify exact counting of requests, hits, misses, replacements, and hit ratio."""
    fifo = FIFOCache(2)
    # Trace: [1, 2, 1, 3, 2, 3]
    # req 1: miss (1) -> [1]
    # req 2: miss (2) -> [1, 2]
    # req 1: hit (1)  -> order remains [1, 2] (1 is oldest)
    # req 3: miss (evict 1) -> [2, 3] (2 is oldest)
    # req 2: hit (2)  -> order remains [2, 3] (2 is oldest)
    # req 3: hit (3)  -> order remains [2, 3]
    trace = [1, 2, 1, 3, 2, 3]
    for page in trace:
        fifo.request(page)

    assert fifo.requests == 6
    assert fifo.hits == 3
    assert fifo.misses == 3
    assert fifo.replacements == 1  # only page 3 caused an eviction
    assert fifo.hit_ratio == 0.5


def test_random_stress_and_invariants():
    """Stress test with 1,000 random references asserting capacity invariant and uniqueness."""
    capacity = 6
    fifo = FIFOCache(capacity)
    pages = list(range(1, 25))

    random.seed(99)
    for _ in range(1000):
        page = random.choice(pages)
        res = fifo.request(page)
        st = res["state"]

        assert st["size"] <= capacity
        assert len(st["pages"]) <= capacity
        assert len(set(st["pages"])) == st["size"]
        assert fifo.requests == fifo.hits + fifo.misses


# =====================================================================
# SIMULATION ENGINE COMPATIBILITY TEST
# =====================================================================

def test_fifo_simulation_engine_integration():
    """
    Verify that existing SimulationEngine seamlessly runs FIFOCache
    without requiring any modifications to the engine.
    """
    capacity = 3
    trace = [1, 2, 3, 1, 4, 1, 5, 2]
    # FIFO execution trace:
    # 1: miss -> [1]
    # 2: miss -> [1, 2]
    # 3: miss -> [1, 2, 3] (1 is oldest)
    # 1: hit  -> [1, 2, 3] (1 remains oldest!)
    # 4: miss -> evicts 1 -> [2, 3, 4] (2 is oldest)
    # 1: miss -> evicts 2 -> [3, 4, 1] (3 is oldest)
    # 5: miss -> evicts 3 -> [4, 1, 5] (4 is oldest)
    # 2: miss -> evicts 4 -> [1, 5, 2] (1 is oldest)

    fifo = FIFOCache(capacity)
    engine = SimulationEngine(fifo, trace)
    result = engine.run()

    # Verify metrics
    metrics = result["metrics"]
    assert metrics["total_requests"] == 8
    assert metrics["total_hits"] == 1
    assert metrics["total_misses"] == 7
    assert metrics["total_page_faults"] == 7
    assert metrics["total_replacements"] == 4
    assert metrics["hit_ratio"] == round(1 / 8, 4)

    # Verify steps
    steps = result["steps"]
    assert len(steps) == 8

    # Step 4: Hit on 1
    assert steps[3]["page"] == 1
    assert steps[3]["hit"] is True
    assert steps[3]["evicted_page"] is None

    # Step 5: Miss on 4 -> evicts 1
    assert steps[4]["page"] == 4
    assert steps[4]["hit"] is False
    assert steps[4]["evicted_page"] == 1

    # Step 6: Miss on 1 -> evicts 2
    assert steps[5]["page"] == 1
    assert steps[5]["hit"] is False
    assert steps[5]["evicted_page"] == 2

    # Final state
    final_st = result["final_state"]
    assert final_st["size"] == 3
    assert final_st["oldest_to_newest"] == [1, 5, 2]
    assert final_st["oldest"] == 1
    assert final_st["newest"] == 2

