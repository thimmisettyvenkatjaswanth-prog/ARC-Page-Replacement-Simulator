"""
Unit and invariant test suite for Least Frequently Used (LFU) Cache.
Verifies frequency-based replacement, deterministic LRU tie-breaking,
counters, and integration with SimulationEngine.
"""
import pytest
import random
from backend.algorithms.lfu import LFUCache
from backend.simulation.engine import SimulationEngine


def test_capacity_validation():
    """Verify capacity validation enforces positive integers."""
    with pytest.raises(ValueError):
        LFUCache(0)
    with pytest.raises(ValueError):
        LFUCache(-1)
    with pytest.raises(ValueError):
        LFUCache("3")  # type: ignore
    with pytest.raises(ValueError):
        LFUCache(3.5)  # type: ignore

    lfu = LFUCache(4)
    assert lfu.capacity == 4


def test_initial_empty_cache():
    """Verify initial state of an empty LFU cache."""
    lfu = LFUCache(3)
    st = lfu.get_state()
    assert st["capacity"] == 3
    assert st["size"] == 0
    assert st["pages"] == []
    assert st["frequencies"] == {}
    assert st["min_frequency"] is None
    assert st["requests"] == 0
    assert st["hits"] == 0
    assert st["misses"] == 0
    assert st["replacements"] == 0
    assert st["hit_ratio"] == 0.0


def test_first_page_request_and_insertion():
    """Verify first request causes a miss and sets frequency to 1."""
    lfu = LFUCache(3)
    res = lfu.request("A")
    assert res["page"] == "A"
    assert res["hit"] is False
    assert res["hit_type"] == "MISS"
    assert res["evicted_page"] is None
    
    st = lfu.get_state()
    assert st["pages"] == ["A"]
    assert st["frequencies"] == {"A": 1}
    assert st["min_frequency"] == 1
    assert lfu.requests == 1
    assert lfu.misses == 1
    assert lfu.hits == 0


def test_warmup_until_full():
    """Verify cold misses fill cache with frequency 1 without evictions."""
    lfu = LFUCache(3)
    lfu.request(1)
    lfu.request(2)
    res = lfu.request(3)

    assert res["hit"] is False
    assert res["evicted_page"] is None
    st = lfu.get_state()
    assert st["size"] == 3
    assert st["frequencies"] == {1: 1, 2: 1, 3: 1}
    assert st["min_frequency"] == 1
    assert lfu.replacements == 0


def test_frequency_increment_on_hit():
    """Verify hit increments frequency and updates min_frequency pointer appropriately."""
    lfu = LFUCache(3)
    lfu.request("A")  # freq 1
    lfu.request("B")  # freq 1
    
    hit_res = lfu.request("A")  # hit! freq becomes 2
    assert hit_res["hit"] is True
    assert hit_res["hit_type"] == "HIT"
    assert lfu.hits == 1
    assert lfu.page_to_freq["A"] == 2
    assert lfu.min_freq == 1  # B is still at freq 1
    
    hit_res_b = lfu.request("B")  # hit! B becomes freq 2
    assert hit_res_b["hit"] is True
    assert lfu.page_to_freq["B"] == 2
    # Since all resident pages are now at freq 2, min_freq must advance to 2
    assert lfu.min_freq == 2


def test_lfu_eviction_lowest_frequency():
    """Verify that the page with lowest frequency is evicted upon fault in full cache."""
    lfu = LFUCache(3)
    lfu.request("A")
    lfu.request("B")
    lfu.request("C")
    
    # Increase frequency of A and B
    lfu.request("A")  # A freq = 2
    lfu.request("A")  # A freq = 3
    lfu.request("B")  # B freq = 2
    # C remains at freq = 1
    
    assert lfu.min_freq == 1
    assert lfu.page_to_freq["C"] == 1
    
    # Request D: cache full, C has lowest frequency (1) -> C must be evicted
    res = lfu.request("D")
    assert res["hit"] is False
    assert res["evicted_page"] == "C"
    assert lfu.replacements == 1
    
    st = lfu.get_state()
    assert "C" not in st["pages"]
    assert "D" in st["pages"]
    assert st["frequencies"]["D"] == 1
    assert st["min_frequency"] == 1


def test_lfu_deterministic_lru_tie_breaking():
    """Verify deterministic LRU tie-breaking when multiple pages share minimum frequency."""
    lfu = LFUCache(3)
    # Insert A, B, C (all freq 1, arrival order: A earliest, then B, then C)
    lfu.request("A")
    lfu.request("B")
    lfu.request("C")
    
    # Cache full. All have freq 1.
    # A is LRU among freq=1 bucket -> A must be evicted first when D is requested.
    res_d = lfu.request("D")
    assert res_d["evicted_page"] == "A"
    assert lfu.get_state()["pages"] == ["B", "C", "D"]
    
    # Now B, C, D all have freq 1. B is LRU among them.
    # Access C to promote it: C becomes freq 2.
    lfu.request("C")
    
    # Resident set: B (freq 1), D (freq 1), C (freq 2).
    # Minimum freq is 1 (pages: B, D; B was inserted before D, so B is LRU).
    res_e = lfu.request("E")
    assert res_e["evicted_page"] == "B"
    assert "B" not in lfu.get_state()["pages"]
    assert "E" in lfu.get_state()["pages"]


def test_simulation_engine_integration():
    """Verify LFUCache runs seamlessly inside SimulationEngine."""
    lfu = LFUCache(3)
    workload = [1, 2, 3, 1, 2, 4, 1, 2, 5]
    engine = SimulationEngine(lfu, workload)
    result = engine.run()
    
    assert result["metrics"]["total_requests"] == len(workload)
    assert result["metrics"]["total_hits"] + result["metrics"]["total_misses"] == len(workload)
    assert len(result["steps"]) == len(workload)
    assert result["final_state"]["size"] <= 3


def test_decoupled_interface_contract():
    """Verify prepare_request, select_victim, evict_victim, and insert_page."""
    lfu = LFUCache(2)
    
    # Prepare cold miss
    prep = lfu.prepare_request("X")
    assert prep["status"] == "COLD_MISS"
    assert prep["requires_eviction"] is False
    
    lfu.insert_page("X")
    assert lfu.contains("X")
    assert lfu.page_to_freq["X"] == 1
    
    # Hit classification
    prep_hit = lfu.prepare_request("X")
    assert prep_hit["status"] == "HIT"
    
    lfu.insert_page("Y")
    assert lfu.is_full()
    
    # Select victim: X and Y both freq 1, X was inserted first
    victim = lfu.select_victim()
    assert victim == "X"
    
    # Evict victim
    lfu.evict_victim("X")
    assert not lfu.contains("X")
    assert lfu.replacements == 1
    
    # Insert new page
    lfu.insert_page("Z")
    assert lfu.contains("Z")
    assert lfu.contains("Y")


def test_reset_functionality():
    """Verify reset clears all structures and counters to pristine initial state."""
    lfu = LFUCache(3)
    for p in [1, 2, 3, 1, 4]:
        lfu.request(p)
        
    assert lfu.requests > 0
    assert lfu.hits > 0
    assert lfu.replacements > 0
    
    lfu.reset()
    st = lfu.get_state()
    assert st["size"] == 0
    assert st["requests"] == 0
    assert st["hits"] == 0
    assert st["misses"] == 0
    assert st["replacements"] == 0
    assert st["min_frequency"] is None
    assert st["frequencies"] == {}


def test_stress_invariant_random_workload():
    """Stress test with randomized requests verifying size and frequency invariants."""
    random.seed(42)
    capacity = 5
    lfu = LFUCache(capacity)
    
    for _ in range(500):
        page = random.randint(1, 15)
        lfu.request(page)
        
        # Verify strict invariants
        assert len(lfu.page_to_freq) <= capacity
        assert lfu.requests == lfu.hits + lfu.misses
        if lfu.page_to_freq:
            assert lfu.min_freq == min(lfu.page_to_freq.values())
            assert lfu.min_freq in lfu.freq_to_pages
            assert len(lfu.freq_to_pages[lfu.min_freq]) > 0

