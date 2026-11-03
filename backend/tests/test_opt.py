"""
Unit and invariant test suite for Belady's Optimal (OPT / MIN) Cache.
Verifies offline clairvoyant future reference precomputation, furthest-future
victim selection, infinity distance eviction, FIFO tie-breaking, and SimulationEngine integration.
"""
import pytest
import random
from collections import deque
from backend.algorithms.opt import OPTCache, OptimalCache
from backend.simulation.engine import SimulationEngine


def test_capacity_validation():
    """Verify capacity validation enforces positive integers."""
    with pytest.raises(ValueError):
        OptimalCache(0)
    with pytest.raises(ValueError):
        OptimalCache(-1)
    with pytest.raises(ValueError):
        OptimalCache("3")  # type: ignore
    with pytest.raises(ValueError):
        OptimalCache(3.5)  # type: ignore

    opt = OptimalCache(4)
    assert opt.capacity == 4


def test_initial_empty_cache():
    """Verify initial state of an empty OPT cache."""
    opt = OptimalCache(3)
    st = opt.get_state()
    assert st["capacity"] == 3
    assert st["size"] == 0
    assert st["pages"] == []
    assert st["current_step"] == 0
    assert st["sequence_length"] == 0
    assert st["requests"] == 0
    assert st["hits"] == 0
    assert st["misses"] == 0
    assert st["replacements"] == 0
    assert st["hit_ratio"] == 0.0
    assert st["is_offline"] is True


def test_uninitialized_sequence_raises_value_error():
    """Verify calling request() without initialized sequence raises an informative ValueError."""
    opt = OptimalCache(3)
    with pytest.raises(ValueError) as excinfo:
        opt.request(1)
    assert "OPT is an offline algorithm requiring the full future reference sequence" in str(excinfo.value)


def test_canonical_alias():
    """Verify OPTCache is identical to OptimalCache."""
    assert OPTCache is OptimalCache


def test_initialize_sequence():
    """Verify initialize_sequence precomputes future occurrence index queues."""
    seq = [1, 2, 3, 1, 2, 1]
    opt = OptimalCache(3, seq)
    
    assert opt.sequence == seq
    assert list(opt.page_to_future_indices[1]) == [0, 3, 5]
    assert list(opt.page_to_future_indices[2]) == [1, 4]
    assert list(opt.page_to_future_indices[3]) == [2]


def test_warmup_until_full():
    """Verify cold misses fill cache without evictions during warmup."""
    seq = [1, 2, 3, 4]
    opt = OptimalCache(3, seq)
    
    r1 = opt.request(1)
    r2 = opt.request(2)
    r3 = opt.request(3)
    
    assert r1["hit"] is False
    assert r2["hit"] is False
    assert r3["hit"] is False
    assert opt.replacements == 0
    assert opt.get_state()["pages"] == [1, 2, 3]


def test_opt_hit():
    """Verify cache hit increments hits and does not evict."""
    seq = [1, 2, 1, 3]
    opt = OptimalCache(2, seq)
    
    opt.request(1)  # miss
    opt.request(2)  # miss
    r_hit = opt.request(1)  # hit
    
    assert r_hit["hit"] is True
    assert r_hit["hit_type"] == "HIT"
    assert opt.hits == 1
    assert opt.misses == 2


def test_opt_eviction_furthest_future_reference():
    """
    Verify OPT evicts the resident page that will be accessed furthest in the future.
    Sequence: [1, 2, 3, 4, 1, 2, 3]
    Capacity: 3
    Warmup: 1, 2, 3 resident.
    Next accesses:
      1 referenced at step 4 (distance 0 from step 4)
      2 referenced at step 5 (distance 1)
      3 referenced at step 6 (distance 2)
    When 4 is requested at step 3, 3 is referenced furthest -> 3 must be evicted!
    """
    seq = [1, 2, 3, 4, 1, 2, 3]
    opt = OptimalCache(3, seq)
    
    opt.request(1)  # step 0
    opt.request(2)  # step 1
    opt.request(3)  # step 2
    # Cache has [1, 2, 3]
    
    r4 = opt.request(4)  # step 3
    assert r4["hit"] is False
    assert r4["evicted_page"] == 3
    assert opt.get_state()["pages"] == [1, 2, 4]


def test_opt_eviction_never_referenced_again():
    """
    Verify page never referenced again (distance infinity) is evicted before
    pages that are referenced in the future.
    """
    seq = [1, 2, 3, 4, 2, 3]
    opt = OptimalCache(3, seq)
    
    opt.request(1)
    opt.request(2)
    opt.request(3)
    
    # Next accesses are 2, then 3. Page 1 is NEVER referenced again.
    # Request 4 must evict page 1.
    r4 = opt.request(4)
    assert r4["evicted_page"] == 1
    assert opt.get_state()["pages"] == [2, 3, 4]


def test_opt_fifo_tie_breaking_for_equal_distances():
    """
    Verify deterministic FIFO tie-breaking when multiple resident pages are never referenced again.
    Sequence: [1, 2, 3, 4, 5]
    Capacity: 3
    At step 3 (page 4): none of 1, 2, 3 will ever be referenced again (all distance inf).
    Earliest inserted page was 1 -> 1 must be evicted.
    At step 4 (page 5): pages 2, 3, 4 resident; 2 and 3 never referenced again.
    Earliest inserted page was 2 -> 2 must be evicted.
    """
    seq = [1, 2, 3, 4, 5]
    opt = OptimalCache(3, seq)
    
    opt.request(1)
    opt.request(2)
    opt.request(3)
    
    r4 = opt.request(4)
    assert r4["evicted_page"] == 1
    
    r5 = opt.request(5)
    assert r5["evicted_page"] == 2


def test_opt_classic_belady_hand_worked_example():
    """
    Verify textbook Belady's Optimal execution trace:
    Silberschatz OS textbook example:
    String: [7, 0, 1, 2, 0, 3, 0, 4, 2, 3, 0, 3, 2, 1, 2, 0, 1, 7, 0, 1]
    Capacity: 3
    Result: Exactly 9 page faults (misses) and 11 hits.
    """
    seq = [7, 0, 1, 2, 0, 3, 0, 4, 2, 3, 0, 3, 2, 1, 2, 0, 1, 7, 0, 1]
    opt = OptimalCache(3, seq)
    
    for page in seq:
        opt.request(page)
        
    assert opt.misses == 9
    assert opt.hits == 11
    assert opt.replacements == 6
    assert opt.hit_ratio == 0.55


def test_simulation_engine_integration():
    """Verify OptimalCache runs seamlessly inside SimulationEngine."""
    seq = [7, 0, 1, 2, 0, 3, 0, 4, 2, 3, 0, 3, 2, 1, 2, 0, 1, 7, 0, 1]
    opt = OptimalCache(3, seq)
    engine = SimulationEngine(opt, seq)
    res = engine.run()
    
    metrics = res["metrics"]
    assert metrics["total_requests"] == 20
    assert metrics["total_hits"] == 11
    assert metrics["total_misses"] == 9
    assert metrics["total_page_faults"] == 9
    assert len(res["steps"]) == 20


def test_decoupled_interface_contract():
    """Verify prepare_request, select_victim, evict_victim, and insert_page."""
    seq = [1, 2, 3, 2]
    opt = OptimalCache(2, seq)
    
    # Step 0: request page 1
    prep1 = opt.prepare_request(1)
    assert prep1["status"] == "COLD_MISS"
    assert prep1["requires_eviction"] is False
    opt.insert_page(1)
    assert opt.contains(1)
    
    # Step 1: request page 2
    prep2 = opt.prepare_request(2)
    assert prep2["status"] == "COLD_MISS"
    opt.insert_page(2)
    assert opt.is_full()
    
    # Step 2: incoming request page 3 (cache is full)
    prep3 = opt.prepare_request(3)
    assert prep3["status"] == "COLD_MISS"
    # Page 2 is referenced in the future at step 3; page 1 is never referenced again
    # select_victim predicts 1
    victim = opt.select_victim(3)
    assert victim == 1
    
    # Evict 1
    opt.evict_victim(1)
    assert not opt.contains(1)
    assert opt.replacements == 1
    
    # Insert 3
    opt.insert_page(3)
    assert opt.contains(3)
    assert opt.contains(2)


def test_reset_functionality():
    """Verify reset clears cache and re-initializes index queues from saved sequence."""
    seq = [1, 2, 3, 4, 1]
    opt = OptimalCache(2, seq)
    
    for p in seq:
        opt.request(p)
        
    assert opt.requests > 0
    assert opt.replacements > 0
    
    opt.reset()
    st = opt.get_state()
    assert st["size"] == 0
    assert st["requests"] == 0
    assert st["hits"] == 0
    assert st["misses"] == 0
    assert st["replacements"] == 0
    assert st["current_step"] == 0
    assert opt.page_to_future_indices[1] == deque([0, 4])


def test_opt_request_matches_expected_sequence():
    """Verify that requests matching the initialized sequence execute normally."""
    seq = [10, 20, 30, 10]
    opt = OptimalCache(2, seq)
    for p in seq:
        res = opt.request(p)
        assert res["page"] == p
    assert opt.requests == 4
    assert opt.hits == 1
    assert opt.misses == 3


def test_opt_request_differs_from_expected_page():
    """Verify that a request differing from the expected sequence raises ValueError with details."""
    seq = ["A", "B", "C"]
    opt = OptimalCache(2, seq)
    opt.request("A")  # Step 0 ok
    
    # Step 1 expects "B", but we send "Z"
    with pytest.raises(ValueError) as excinfo:
        opt.request("Z")
        
    err_msg = str(excinfo.value)
    assert "Sequence mismatch at index 1" in err_msg
    assert "expected page 'B'" in err_msg
    assert "received page 'Z'" in err_msg


def test_opt_mismatch_does_not_modify_state():
    """Verify that a failed mismatched request does not mutate cache, queues, step, or counters."""
    seq = [1, 2, 3, 1]
    opt = OptimalCache(2, seq)
    opt.request(1)  # Step 0
    
    # Snapshot state prior to error
    cached_before = dict(opt.cache)
    step_before = opt.current_step
    requests_before = opt.requests
    hits_before = opt.hits
    misses_before = opt.misses
    replacements_before = opt.replacements
    queues_before = {k: list(v) for k, v in opt.page_to_future_indices.items()}
    
    # Attempt invalid request (expected 2, received 99)
    with pytest.raises(ValueError):
        opt.request(99)
        
    # Verify no state mutation occurred
    assert opt.cache == cached_before
    assert opt.current_step == step_before
    assert opt.requests == requests_before
    assert opt.hits == hits_before
    assert opt.misses == misses_before
    assert opt.replacements == replacements_before
    queues_after = {k: list(v) for k, v in opt.page_to_future_indices.items()}
    assert queues_after == queues_before
    
    # Ensure simulation can resume with the correct page
    res = opt.request(2)
    assert res["hit"] is False
    assert opt.current_step == 2


def test_opt_request_after_sequence_exhaustion():
    """Verify that requesting pages after the sequence is exhausted raises ValueError."""
    seq = [1, 2]
    opt = OptimalCache(2, seq)
    opt.request(1)
    opt.request(2)
    assert opt.current_step == 2
    
    with pytest.raises(ValueError) as excinfo:
        opt.request(1)
        
    err_msg = str(excinfo.value)
    assert "Reference sequence exhausted" in err_msg
    assert "step index 2" in err_msg


def test_opt_exhaustion_error_does_not_modify_state():
    """Verify that sequence exhaustion error does not mutate cache, step, or counters."""
    seq = [1, 2]
    opt = OptimalCache(2, seq)
    opt.request(1)
    opt.request(2)
    
    cached_before = dict(opt.cache)
    step_before = opt.current_step
    requests_before = opt.requests
    hits_before = opt.hits
    misses_before = opt.misses
    replacements_before = opt.replacements
    
    with pytest.raises(ValueError):
        opt.request(3)
        
    assert opt.cache == cached_before
    assert opt.current_step == step_before
    assert opt.requests == requests_before
    assert opt.hits == hits_before
    assert opt.misses == misses_before
    assert opt.replacements == replacements_before


def test_opt_reset_restores_sequence_position():
    """Verify that reset allows the exact same sequence to be simulated from start."""
    seq = [1, 2, 3, 1, 2]
    opt = OptimalCache(2, seq)
    
    for p in seq:
        opt.request(p)
    assert opt.current_step == len(seq)
    first_hits = opt.hits
    first_misses = opt.misses
    
    # Reset
    opt.reset()
    assert opt.current_step == 0
    assert opt.requests == 0
    assert len(opt.cache) == 0
    
    # Run identical sequence again
    for p in seq:
        opt.request(p)
    assert opt.current_step == len(seq)
    assert opt.hits == first_hits
    assert opt.misses == first_misses
