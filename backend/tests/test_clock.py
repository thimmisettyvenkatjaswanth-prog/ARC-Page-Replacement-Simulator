"""
Unit and invariant test suite for CLOCK (Second-Chance) Cache.
Verifies circular buffer pointer movement, second-chance bit clearing,
all-bits-set non-infinite loop safety, counters, and SimulationEngine integration.
"""
import pytest
import random
from backend.algorithms.clock import CLOCKCache
from backend.simulation.engine import SimulationEngine


def test_capacity_validation():
    """Verify capacity validation enforces positive integers."""
    with pytest.raises(ValueError):
        CLOCKCache(0)
    with pytest.raises(ValueError):
        CLOCKCache(-1)
    with pytest.raises(ValueError):
        CLOCKCache("3")  # type: ignore
    with pytest.raises(ValueError):
        CLOCKCache(3.5)  # type: ignore

    clock = CLOCKCache(4)
    assert clock.capacity == 4


def test_initial_empty_cache():
    """Verify initial state of an empty CLOCK cache."""
    clock = CLOCKCache(3)
    st = clock.get_state()
    assert st["capacity"] == 3
    assert st["size"] == 0
    assert st["pages"] == []
    assert st["slots"] == [None, None, None]
    assert st["reference_bits"] == [0, 0, 0]
    assert st["hand"] == 0
    assert st["requests"] == 0
    assert st["hits"] == 0
    assert st["misses"] == 0
    assert st["replacements"] == 0
    assert st["hit_ratio"] == 0.0


def test_warmup_until_full():
    """Verify cold misses fill slots sequentially, setting bit=1 and advancing hand."""
    clock = CLOCKCache(3)
    
    res1 = clock.request(10)
    assert res1["hit"] is False
    assert clock.slots == [10, None, None]
    assert clock.reference_bits == [1, 0, 0]
    assert clock.hand == 1
    
    res2 = clock.request(20)
    assert res2["hit"] is False
    assert clock.slots == [10, 20, None]
    assert clock.reference_bits == [1, 1, 0]
    assert clock.hand == 2
    
    res3 = clock.request(30)
    assert res3["hit"] is False
    assert clock.slots == [10, 20, 30]
    assert clock.reference_bits == [1, 1, 1]
    assert clock.hand == 0  # Wraps around to 0
    assert clock.replacements == 0


def test_hit_sets_bit_without_advancing_hand():
    """Verify cache hit sets reference bit to 1 and does NOT move clock hand."""
    clock = CLOCKCache(3)
    clock.request(1)
    clock.request(2)
    clock.request(3)
    # slots=[1, 2, 3], bits=[1, 1, 1], hand=0
    
    # Artificially clear bit of slot 1
    clock.set_reference(1, 0)
    assert clock.get_reference(1) == 0
    assert clock.hand == 0
    
    # Hit on page 2 (in slot 1)
    res = clock.request(2)
    assert res["hit"] is True
    assert clock.hits == 1
    assert clock.get_reference(1) == 1  # Bit set back to 1
    assert clock.hand == 0  # Hand did NOT advance on hit!


def test_second_chance_eviction_with_bit_zero():
    """Verify slot with reference bit 0 is evicted immediately without extra sweep."""
    clock = CLOCKCache(3)
    clock.request(1)
    clock.request(2)
    clock.request(3)
    # slots=[1, 2, 3], bits=[1, 1, 1], hand=0
    
    # Clear bit at hand position (slot 0)
    clock.set_reference(0, 0)
    assert clock.reference_bits == [0, 1, 1]
    assert clock.hand == 0
    
    # Request 4: hand is at slot 0 which has bit 0 -> slot 0 evicted immediately!
    res = clock.request(4)
    assert res["hit"] is False
    assert res["evicted_page"] == 1
    assert clock.slots == [4, 2, 3]
    assert clock.reference_bits == [1, 1, 1]
    assert clock.hand == 1  # Hand advances past replaced slot


def test_second_chance_sweep_clearing_bits():
    """Verify hand clears bit 1 to 0 and advances until reaching a slot with bit 0."""
    clock = CLOCKCache(3)
    clock.request(1)
    clock.request(2)
    clock.request(3)
    # slots=[1, 2, 3], bits=[1, 1, 1], hand=0
    
    # Set bits: slot 0 has bit 1, slot 1 has bit 1, slot 2 has bit 0
    clock.set_reference(2, 0)
    assert clock.reference_bits == [1, 1, 0]
    assert clock.hand == 0
    
    # Request 4:
    # Slot 0 has bit 1 -> cleared to 0, hand moves to 1
    # Slot 1 has bit 1 -> cleared to 0, hand moves to 2
    # Slot 2 has bit 0 -> evicted! Replaced with 4 (bit=1)
    # Hand moves to (2 + 1) % 3 = 0
    res = clock.request(4)
    assert res["hit"] is False
    assert res["evicted_page"] == 3
    assert clock.slots == [1, 2, 4]
    assert clock.reference_bits == [0, 0, 1]
    assert clock.hand == 0


def test_all_bits_set_no_infinite_loop():
    """
    Verify worst-case scenario: all slots have bit 1.
    First revolution clears all bits to 0; second revolution evicts slot at initial hand.
    """
    clock = CLOCKCache(3)
    clock.request(1)
    clock.request(2)
    clock.request(3)
    # slots=[1, 2, 3], bits=[1, 1, 1], hand=0
    
    # All bits are 1. Request 4:
    # Lap 1: slot 0 (1->0), slot 1 (1->0), slot 2 (1->0). Hand returns to 0.
    # Lap 2: slot 0 now has bit 0 -> evicted! Replaced with 4 (bit=1).
    # Hand advances to 1.
    res = clock.request(4)
    assert res["hit"] is False
    assert res["evicted_page"] == 1
    assert clock.slots == [4, 2, 3]
    assert clock.reference_bits == [1, 0, 0]
    assert clock.hand == 1


def test_reference_bit_helpers_and_bounds():
    """Verify get_reference and set_reference bounds checking."""
    clock = CLOCKCache(2)
    with pytest.raises(IndexError):
        clock.get_reference(-1)
    with pytest.raises(IndexError):
        clock.get_reference(2)
    with pytest.raises(IndexError):
        clock.set_reference(5, 1)


def test_deterministic_clock_trace():
    """Verify complete multi-step execution trace against hand-calculated values."""
    clock = CLOCKCache(3)
    trace = [1, 2, 3, 1, 4, 2, 5]
    
    # 1: miss, slots=[1, None, None], bits=[1, 0, 0], hand=1
    clock.request(1)
    # 2: miss, slots=[1, 2, None], bits=[1, 1, 0], hand=2
    clock.request(2)
    # 3: miss, slots=[1, 2, 3], bits=[1, 1, 1], hand=0
    clock.request(3)
    # 1: hit, slots=[1, 2, 3], bits=[1, 1, 1], hand=0
    clock.request(1)
    # 4: miss, all bits 1 -> clears all, evicts slot 0 (1) -> slots=[4, 2, 3], bits=[1, 0, 0], hand=1
    r4 = clock.request(4)
    assert r4["evicted_page"] == 1
    assert clock.slots == [4, 2, 3]
    assert clock.reference_bits == [1, 0, 0]
    assert clock.hand == 1
    # 2: hit, slot 1 bit becomes 1, hand stays at 1 -> bits=[1, 1, 0], hand=1
    r2 = clock.request(2)
    assert r2["hit"] is True
    assert clock.reference_bits == [1, 1, 0]
    assert clock.hand == 1
    # 5: miss, hand at 1 (bit 1 -> 0, hand->2); slot 2 (bit 0 -> evicts 3).
    # slots=[4, 2, 5], bits=[1, 0, 1], hand=0
    r5 = clock.request(5)
    assert r5["evicted_page"] == 3
    assert clock.slots == [4, 2, 5]
    assert clock.reference_bits == [1, 0, 1]
    assert clock.hand == 0


def test_simulation_engine_integration():
    """Verify CLOCKCache runs seamlessly inside SimulationEngine."""
    clock = CLOCKCache(3)
    workload = [1, 2, 3, 1, 4, 1, 5, 2, 3]
    engine = SimulationEngine(clock, workload)
    result = engine.run()
    
    assert result["metrics"]["total_requests"] == len(workload)
    assert result["metrics"]["total_hits"] + result["metrics"]["total_misses"] == len(workload)
    assert len(result["steps"]) == len(workload)
    assert result["final_state"]["size"] <= 3


def test_decoupled_interface_contract():
    """Verify prepare_request, select_victim, evict_victim, and insert_page."""
    clock = CLOCKCache(2)
    
    prep = clock.prepare_request("A")
    assert prep["status"] == "COLD_MISS"
    assert prep["requires_eviction"] is False
    
    clock.insert_page("A")
    assert clock.contains("A")
    assert clock.slots[0] == "A"
    
    clock.insert_page("B")
    assert clock.is_full()
    assert clock.slots == ["A", "B"]
    
    # Both A (slot 0) and B (slot 1) have bit 1. Hand is at 0.
    # Non-mutating select_victim simulates sweep and predicts slot 0 ("A") as victim
    victim = clock.select_victim()
    assert victim == "A"
    # Verify select_victim did NOT mutate actual hand or reference bits
    assert clock.hand == 0
    assert clock.reference_bits == [1, 1]
    
    # Evict victim
    clock.evict_victim("A")
    assert not clock.contains("A")
    assert clock.slots[0] is None
    assert clock.replacements == 1
    
    # Insert new page C
    clock.insert_page("C")
    assert clock.contains("C")
    assert clock.slots[0] == "C"


def test_reset_functionality():
    """Verify reset clears all structures and counters to pristine initial state."""
    clock = CLOCKCache(3)
    for p in [1, 2, 3, 4, 1]:
        clock.request(p)
        
    assert clock.requests > 0
    assert clock.replacements > 0
    
    clock.reset()
    st = clock.get_state()
    assert st["size"] == 0
    assert st["slots"] == [None, None, None]
    assert st["reference_bits"] == [0, 0, 0]
    assert st["hand"] == 0
    assert st["requests"] == 0
    assert st["hits"] == 0
    assert st["misses"] == 0
    assert st["replacements"] == 0


def test_stress_invariant_random_workload():
    """Stress test with randomized requests verifying size and bounds invariants."""
    random.seed(42)
    capacity = 5
    clock = CLOCKCache(capacity)
    
    for _ in range(500):
        page = random.randint(1, 15)
        clock.request(page)
        
        # Verify strict invariants
        assert len(clock.page_to_slot) <= capacity
        assert 0 <= clock.hand < capacity
        assert len(clock.slots) == capacity
        assert len(clock.reference_bits) == capacity
        assert clock.requests == clock.hits + clock.misses
        for slot_idx, resident_page in enumerate(clock.slots):
            if resident_page is not None:
                assert clock.page_to_slot[resident_page] == slot_idx
                assert clock.reference_bits[slot_idx] in (0, 1)

