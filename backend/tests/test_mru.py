"""
Unit and invariant test suite for Most Recently Used (MRU) Cache.
Verifies recency replacement behavior, cyclic workload performance,
counters, and integration with SimulationEngine.
"""
import pytest
import random
from backend.algorithms.lru import LRUCache
from backend.algorithms.mru import MRUCache
from backend.simulation.engine import SimulationEngine


def test_capacity_validation():
    """Verify capacity validation enforces positive integers."""
    with pytest.raises(ValueError):
        MRUCache(0)
    with pytest.raises(ValueError):
        MRUCache(-1)
    with pytest.raises(ValueError):
        MRUCache("3")  # type: ignore
    with pytest.raises(ValueError):
        MRUCache(3.5)  # type: ignore

    mru = MRUCache(4)
    assert mru.capacity == 4


def test_initial_empty_cache():
    """Verify initial state of an empty MRU cache."""
    mru = MRUCache(3)
    st = mru.get_state()
    assert st["capacity"] == 3
    assert st["size"] == 0
    assert st["pages"] == []
    assert st["mru"] is None
    assert st["lru"] is None
    assert st["requests"] == 0
    assert st["hits"] == 0
    assert st["misses"] == 0
    assert st["replacements"] == 0
    assert st["hit_ratio"] == 0.0


def test_first_page_request_and_insertion():
    """Verify first request causes a miss and sets page as MRU and LRU."""
    mru = MRUCache(3)
    res = mru.request("A")
    assert res["page"] == "A"
    assert res["hit"] is False
    assert res["hit_type"] == "MISS"
    assert res["evicted_page"] is None
    
    st = mru.get_state()
    assert st["pages"] == ["A"]
    assert st["mru"] == "A"
    assert st["lru"] == "A"
    assert mru.requests == 1
    assert mru.misses == 1
    assert mru.hits == 0


def test_warmup_until_full():
    """Verify cold misses fill cache in MRU order without evictions."""
    mru = MRUCache(3)
    mru.request(1)
    mru.request(2)
    res = mru.request(3)

    assert res["hit"] is False
    assert res["evicted_page"] is None
    st = mru.get_state()
    assert st["size"] == 3
    assert st["mru"] == 3
    assert st["lru"] == 1
    assert st["mru_to_lru"] == [3, 2, 1]
    assert mru.replacements == 0


def test_mru_promotion_on_hit():
    """Verify hitting a resident page moves it to MRU position."""
    mru = MRUCache(3)
    mru.request("A")
    mru.request("B")
    mru.request("C")
    # Current order (MRU to LRU): [C, B, A]
    
    res = mru.request("A")
    assert res["hit"] is True
    assert res["hit_type"] == "HIT"
    st = mru.get_state()
    assert st["mru"] == "A"
    assert st["lru"] == "B"
    assert st["mru_to_lru"] == ["A", "C", "B"]


def test_mru_eviction_on_full_cache():
    """Verify that when cache is full, the MRU page is evicted."""
    mru = MRUCache(3)
    mru.request(1)
    mru.request(2)
    mru.request(3)
    # MRU is 3, LRU is 1. Cache: [3, 2, 1]
    
    # Request 4: Cache full -> evicts MRU (3), inserts 4 at MRU
    res = mru.request(4)
    assert res["hit"] is False
    assert res["evicted_page"] == 3
    assert mru.replacements == 1
    
    st = mru.get_state()
    assert 3 not in st["pages"]
    assert st["mru"] == 4
    assert st["mru_to_lru"] == [4, 2, 1]


def test_mru_cyclic_workload_performance():
    """
    Verify MRU on cyclic workload [1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 4] with capacity 3.
    Demonstrates that MRU achieves 50% hit ratio (6 hits, 6 misses), avoiding
    the complete cyclic thrashing suffered by LRU (0 hits, 12 misses).
    """
    workload = [1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 4]
    capacity = 3
    
    mru = MRUCache(capacity)
    lru = LRUCache(capacity)
    
    for page in workload:
        mru.request(page)
        lru.request(page)
        
    # LRU suffers 100% miss rate (thrashing)
    assert lru.hits == 0
    assert lru.misses == 12
    assert lru.hit_ratio == 0.0
    
    # MRU retains older pages in the cycle, achieving 6 hits
    assert mru.hits == 6
    assert mru.misses == 6
    assert mru.hit_ratio == 0.5


def test_simulation_engine_integration():
    """Verify MRUCache runs seamlessly inside SimulationEngine."""
    mru = MRUCache(3)
    workload = [1, 2, 3, 4, 1, 2, 5, 1, 2]
    engine = SimulationEngine(mru, workload)
    result = engine.run()
    
    assert result["metrics"]["total_requests"] == len(workload)
    assert result["metrics"]["total_hits"] + result["metrics"]["total_misses"] == len(workload)
    assert len(result["steps"]) == len(workload)
    assert result["final_state"]["size"] <= 3


def test_decoupled_interface_contract():
    """Verify prepare_request, select_victim, evict_victim, and insert_page."""
    mru = MRUCache(2)
    
    # Prepare cold miss
    prep = mru.prepare_request("X")
    assert prep["status"] == "COLD_MISS"
    assert prep["requires_eviction"] is False
    
    mru.insert_page("X")
    assert mru.contains("X")
    
    prep_hit = mru.prepare_request("X")
    assert prep_hit["status"] == "HIT"
    
    mru.insert_page("Y")
    assert mru.is_full()
    
    # Select victim: MRU is Y (inserted most recently)
    victim = mru.select_victim()
    assert victim == "Y"
    
    # Evict victim
    mru.evict_victim("Y")
    assert not mru.contains("Y")
    assert mru.replacements == 1
    
    # Insert Z
    mru.insert_page("Z")
    assert mru.contains("Z")
    assert mru.contains("X")


def test_reset_functionality():
    """Verify reset clears all structures and counters to pristine initial state."""
    mru = MRUCache(3)
    for p in [1, 2, 3, 4, 1]:
        mru.request(p)
        
    assert mru.requests > 0
    assert mru.replacements > 0
    
    mru.reset()
    st = mru.get_state()
    assert st["size"] == 0
    assert st["requests"] == 0
    assert st["hits"] == 0
    assert st["misses"] == 0
    assert st["replacements"] == 0
    assert st["mru"] is None
    assert st["lru"] is None
    assert st["pages"] == []


def test_stress_invariant_random_workload():
    """Stress test with randomized requests verifying size and recency invariants."""
    random.seed(42)
    capacity = 5
    mru = MRUCache(capacity)
    
    for _ in range(500):
        page = random.randint(1, 15)
        mru.request(page)
        
        # Verify strict invariants
        assert len(mru.cache) <= capacity
        assert mru.requests == mru.hits + mru.misses
        if mru.cache:
            assert mru.get_state()["mru"] == next(reversed(mru.cache))
            assert mru.get_state()["lru"] == next(iter(mru.cache))

