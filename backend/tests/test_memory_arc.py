"""
Comprehensive integration tests for Stage 12.2 Phase 3A:
ARC (Adaptive Replacement Cache) integration with MemoryManager.

Verifies:
  1. Cold page faults and initial T1 placement.
  2. Resident page hits and T2 promotion.
  3. B1 ghost hits and learning rule (p expansion favoring recency).
  4. B2 ghost hits and learning rule (p contraction favoring frequency).
  5. Correct T1/T2 placement after ghost hits.
  6. Correct victim selection under adapted target_p.
  7. ARC-mandated eviction when a free physical frame still exists.
  8. Physical memory exhaustion and frame reuse.
  9. Cold-miss Case 4A and Case 4B directory trimming (B1/B2 ghost purges and direct T1 drops).
  10. Dirty victim write-back to backing store.
  11. Backing-store write failure preserves victim residency, dirty status, ARC lists, and p.
  12. Backing-store read failure preserves victim residency, ARC lists, and p.
  13. Successful write-back followed by failed read leaves victim clean and resident.
  14. Parity between MemoryManager-coordinated ARC and standalone ARCCache.
  15. Backward compatibility of standalone ARCCache and other policies (LRU, FIFO, LFU, MRU).
"""
from unittest.mock import patch
import pytest
from backend.algorithms.arc import ARCCache, ArcPlan
from backend.memory.backing_store import BackingStore
from backend.memory.exceptions import (
    BindingError,
    CorruptedManagerStateError,
    DiskReadError,
    DiskWriteError,
    NoFreeFrameError,
    StaleTokenError,
    UnsupportedOperationError,
)
from backend.memory.manager import MemoryManager
from backend.memory.page import VirtualPage


def test_arc_cold_faults_and_warmup():
    """Verify cold misses fill T1 in MRU order and allocate free physical frames."""
    mm = MemoryManager(capacity=3, algorithm="ARC")
    arc = mm.policy.policy

    # Access pages 1, 2, 3 (Process 1)
    r1 = mm.access_page(pid=1, vpn=10)
    r2 = mm.access_page(pid=1, vpn=20)
    r3 = mm.access_page(pid=1, vpn=30)

    assert not r1.hit and not r2.hit and not r3.hit
    assert r1.frame_id == 0 and r2.frame_id == 1 and r3.frame_id == 2
    assert r1.evicted_page is None and r2.evicted_page is None and r3.evicted_page is None

    # Verify ARC internal state: Warmup fills T1 (MRU to LRU)
    vp1 = VirtualPage(1, 10)
    vp2 = VirtualPage(1, 20)
    vp3 = VirtualPage(1, 30)

    state = arc.get_state()
    assert state["T1"] == [vp3, vp2, vp1]
    assert state["T2"] == []
    assert state["B1"] == []
    assert state["B2"] == []
    assert arc.p == 0.0

    # Page tables and physical frames must match
    pt = mm.get_page_table(1)
    assert pt.get_frame_id(10) == 0
    assert pt.get_frame_id(20) == 1
    assert pt.get_frame_id(30) == 2


def test_arc_resident_hits_and_t2_promotion():
    """Verify hits on T1 promote pages to T2 MRU, and hits on T2 move them to T2 MRU."""
    mm = MemoryManager(capacity=3, algorithm="ARC")
    arc = mm.policy.policy

    mm.access_page(1, 10)
    mm.access_page(1, 20)
    mm.access_page(1, 30)

    vp1 = VirtualPage(1, 10)
    vp2 = VirtualPage(1, 20)
    vp3 = VirtualPage(1, 30)

    # 1. Hit on (1, 10): Promoted from T1 to T2
    h1 = mm.access_page(1, 10)
    assert h1.hit is True
    assert h1.frame_id == 0

    state1 = arc.get_state()
    assert state1["T1"] == [vp3, vp2]
    assert state1["T2"] == [vp1]

    # 2. Hit on (1, 20): Promoted from T1 to T2 (MRU)
    h2 = mm.access_page(1, 20)
    assert h2.hit is True
    assert h2.frame_id == 1

    state2 = arc.get_state()
    assert state2["T1"] == [vp3]
    assert state2["T2"] == [vp2, vp1]

    # 3. Repeat hit on (1, 10): Moved to MRU of T2
    h3 = mm.access_page(1, 10)
    assert h3.hit is True

    state3 = arc.get_state()
    assert state3["T1"] == [vp3]
    assert state3["T2"] == [vp1, vp2]
    assert arc.p == 0.0


def test_arc_cold_eviction_and_b1_movement():
    """Verify cold eviction moves LRU page of T1 to B1 (Case 4B) and reuses its frame."""
    mm = MemoryManager(capacity=3, algorithm="ARC")
    arc = mm.policy.policy

    # Load 10, 20, 30
    mm.access_page(1, 10)
    mm.access_page(1, 20)
    mm.access_page(1, 30)

    # Hit 10 -> moves to T2. Now T1=[30, 20], T2=[10].
    mm.access_page(1, 10)

    vp1 = VirtualPage(1, 10)
    vp2 = VirtualPage(1, 20)
    vp3 = VirtualPage(1, 30)
    vp4 = VirtualPage(1, 40)

    # Access 40: Cache full (3 resident). |L1| = 2 < 3. |L1| + |L2| = 3 == c.
    # REPLACE: len(T1) = 2 > p (0.0) -> evicts LRU of T1 (which is 20) to B1!
    r4 = mm.access_page(1, 40)
    assert r4.hit is False
    assert r4.evicted_page == vp2
    assert r4.frame_id == 1  # Frame 1 reused

    state = arc.get_state()
    assert state["T1"] == [vp4, vp3]
    assert state["T2"] == [vp1]
    assert state["B1"] == [vp2]
    assert state["B2"] == []
    assert arc.p == 0.0

    # Page table check
    pt = mm.get_page_table(1)
    assert not pt.is_valid(20)
    assert pt.get_frame_id(40) == 1
    assert pt.get_frame_id(10) == 0
    assert pt.get_frame_id(30) == 2


def test_arc_b1_ghost_hit_and_p_increase():
    """Verify B1 ghost hit increases p by delta, evicts victim via REPLACE(target_p), and places page in T2."""
    mm = MemoryManager(capacity=3, algorithm="ARC")
    arc = mm.policy.policy

    # Setup: T1=[40, 30], T2=[10], B1=[20], B2=[], p=0.0
    mm.access_page(1, 10)
    mm.access_page(1, 20)
    mm.access_page(1, 30)
    mm.access_page(1, 10)
    mm.access_page(1, 40)

    vp1 = VirtualPage(1, 10)
    vp2 = VirtualPage(1, 20)
    vp3 = VirtualPage(1, 30)
    vp4 = VirtualPage(1, 40)

    assert arc.p == 0.0
    assert vp2 in arc.B1

    # Now request 20: B1 ghost hit!
    # |B1| = 1, |B2| = 0 -> delta = 1.0. target_p = min(3.0, 0.0 + 1.0) = 1.0.
    # REPLACE(1.0, 20): len(T1)=2 > 1.0 -> evicts LRU of T1 (which is 30) to B1!
    # 20 moved from B1 to T2 MRU.
    r_ghost = mm.access_page(1, 20)
    assert r_ghost.hit is False
    assert r_ghost.evicted_page == vp3
    assert r_ghost.frame_id == 2  # Frame 2 reused

    assert arc.p == 1.0
    state = arc.get_state()
    assert state["T1"] == [vp4]
    assert state["T2"] == [vp2, vp1]
    assert state["B1"] == [vp3]
    assert state["B2"] == []

    # Page table verification
    pt = mm.get_page_table(1)
    assert not pt.is_valid(30)
    assert pt.is_valid(20)
    assert pt.get_frame_id(20) == 2
    assert pt.is_valid(10)
    assert pt.is_valid(40)


def test_arc_b2_ghost_hit_and_p_decrease():
    """Verify B2 ghost hit decreases p by delta, evicts victim via REPLACE(target_p), and places page in T2."""
    mm = MemoryManager(capacity=3, algorithm="ARC")
    arc = mm.policy.policy

    # Reach state: T1=[40], T2=[20, 10], B1=[30], B2=[], p=1.0
    mm.access_page(1, 10)
    mm.access_page(1, 20)
    mm.access_page(1, 30)
    mm.access_page(1, 10)
    mm.access_page(1, 40)
    mm.access_page(1, 20)
    assert arc.p == 1.0

    vp1 = VirtualPage(1, 10)
    vp2 = VirtualPage(1, 20)
    vp3 = VirtualPage(1, 30)
    vp4 = VirtualPage(1, 40)
    vp5 = VirtualPage(1, 50)

    # Access 50: Cold miss.
    # |L1| = |T1| + |B1| = 1 + 1 = 2 < 3. |L1| + |L2| = 2 + 2 = 4 > 3.
    # REPLACE(1.0, 50): len(T1) = 1 == p (1.0). Condition len(T1) > p is False!
    # So evicts LRU of T2 (which is 10) to B2!
    r50 = mm.access_page(1, 50)
    assert r50.evicted_page == vp1

    state50 = arc.get_state()
    assert vp1 in state50["B2"]
    assert state50["T1"] == [vp5, vp4]
    assert state50["T2"] == [vp2]
    assert arc.p == 1.0

    # Now access 10: B2 ghost hit!
    # |B1| = 1, |B2| = 1 -> delta = 1.0. target_p = max(0.0, 1.0 - 1.0) = 0.0.
    # REPLACE(0.0, 10): 10 is in B2. len(T1)=2 > 0.0 -> evicts LRU of T1 (which is 40) to B1!
    # 10 is moved from B2 to T2 MRU.
    r10 = mm.access_page(1, 10)
    assert r10.hit is False
    assert r10.evicted_page == vp4

    assert arc.p == 0.0
    state10 = arc.get_state()
    assert state10["T1"] == [vp5]
    assert state10["T2"] == [vp1, vp2]
    assert vp4 in state10["B1"]
    assert vp1 not in state10["B2"]


def test_arc_mandated_eviction_when_allocator_has_free_frame():
    """Verify that an ARC-mandated eviction occurs even when allocator has a free frame."""
    # Initialize MemoryManager with capacity 4, but set ARC algorithm with capacity 3
    # This simulates a free physical frame existing while ARC resident cache is full
    arc_custom = ARCCache(capacity=3)
    mm = MemoryManager(capacity=4, algorithm=arc_custom)

    # Fill ARC cache (3 resident pages)
    mm.access_page(1, 10)
    mm.access_page(1, 20)
    mm.access_page(1, 30)
    # Hit 10 -> T1=[30, 20], T2=[10]
    mm.access_page(1, 10)

    # Verify allocator still has 1 free frame (frame 3)
    assert mm.allocator.has_free_frame() is True
    assert mm.allocator.peek_free_frame() == 3

    vp2 = VirtualPage(1, 20)
    # Access 40: ARC mandates eviction because |T1| + |T2| == 3 (capacity of ARC)!
    r40 = mm.access_page(1, 40)
    assert r40.hit is False
    assert r40.evicted_page == vp2  # Evicted LRU of T1 to B1
    assert r40.frame_id == 1        # Reused frame 1 of victim 20!

    # Crucially: frame 3 in allocator is STILL free!
    assert mm.allocator.has_free_frame() is True
    assert mm.allocator.peek_free_frame() == 3
    assert mm.frames[3].vpage is None  # Frame 3 remains empty


def test_arc_cold_miss_case_4a_direct_t1_drop():
    """Verify Case 4A subcase 1b: when |T1| == c, LRU of T1 is dropped directly without moving to B1."""
    mm = MemoryManager(capacity=3, algorithm="ARC")
    arc = mm.policy.policy

    # Fill T1 with 3 pages without hits: T1=[30, 20, 10], B1=[], T2=[], B2=[]
    mm.access_page(1, 10)
    mm.access_page(1, 20)
    mm.access_page(1, 30)

    vp1 = VirtualPage(1, 10)
    vp2 = VirtualPage(1, 20)
    vp3 = VirtualPage(1, 30)
    vp4 = VirtualPage(1, 40)

    # |L1| = |T1| = 3 == c. |T1| == c.
    # Case 4A subcase 1b requires deleting LRU page in T1 directly (not into B1)!
    r4 = mm.access_page(1, 40)
    assert r4.evicted_page == vp1
    assert r4.frame_id == 0

    state = arc.get_state()
    assert state["T1"] == [vp4, vp3, vp2]
    assert state["B1"] == []  # (1, 10) was dropped directly, NOT moved to B1!
    assert state["T2"] == []
    assert state["B2"] == []


def test_arc_cold_miss_case_4a_b1_directory_trimming():
    """Verify Case 4A when |L1| == c and |T1| < c: purges LRU of B1 and calls REPLACE."""
    mm = MemoryManager(capacity=3, algorithm="ARC")
    arc = mm.policy.policy

    # Setup: T1=[30, 20], T2=[10]
    mm.access_page(1, 10)
    mm.access_page(1, 20)
    mm.access_page(1, 30)
    mm.access_page(1, 10)

    # Access 40 -> evicts 20 to B1. State: T1=[40, 30], B1=[20], T2=[10].
    mm.access_page(1, 40)

    vp1 = VirtualPage(1, 10)
    vp2 = VirtualPage(1, 20)
    vp3 = VirtualPage(1, 30)
    vp4 = VirtualPage(1, 40)
    vp5 = VirtualPage(1, 50)

    # Now |L1| = |T1| + |B1| = 2 + 1 = 3 == c, and |T1| = 2 < c.
    # Access 50: Case 4A: purges LRU of B1 (which is 20) and evicts 30 from T1 to B1!
    r5 = mm.access_page(1, 50)
    assert r5.evicted_page == vp3

    state = arc.get_state()
    assert vp2 not in state["B1"]  # 20 was trimmed/purged from B1
    assert vp3 in state["B1"]      # 30 replaced it in B1
    assert state["T1"] == [vp5, vp4]


def test_arc_cold_miss_case_4b_b2_directory_trimming():
    """Verify Case 4B when total directory |L1| + |L2| == 2c: purges LRU of B2."""
    mm = MemoryManager(capacity=2, algorithm="ARC")
    arc = mm.policy.policy

    # Initialize state where |L1| < c (here |L1| = 1 < 2) and total directory == 2c = 4:
    # T1=[10] (|L1|=1), T2=[20] (|T2|=1), B2=[30, 40] (|B2|=2) -> |L2|=3, total=4.
    mm.access_page(1, 10)
    mm.access_page(1, 20)
    # Hit 20 -> T1=[10], T2=[20]
    mm.access_page(1, 20)

    # Populate B2 ghosts (30 as LRU, 40 as MRU)
    vp30 = VirtualPage(1, 30)
    vp40 = VirtualPage(1, 40)
    arc.B2[vp30] = None
    arc.B2[vp40] = None

    state_before = arc.get_state()
    assert state_before["size_total"] == 4
    assert len(state_before["T1"]) == 1
    assert len(state_before["B1"]) == 0
    assert state_before["B2"] == [vp40, vp30]  # MRU to LRU

    # Access page 99: Cold miss.
    # |L1| = 1 < 2. Total = 4 == 2c.
    # Case 4B purges LRU of B2 (which is vp30), evicts LRU of T1 (vp10) to B1, and fetches 99 to T1.
    r99 = mm.access_page(1, 99)
    assert r99.hit is False
    assert r99.evicted_page == VirtualPage(1, 10)

    state_after = arc.get_state()
    assert state_after["size_total"] <= 4
    assert vp30 not in state_after["B2"]  # 30 was purged
    assert vp40 in state_after["B2"]      # 40 remains in B2
    assert VirtualPage(1, 10) in state_after["B1"]
    assert state_after["T1"] == [VirtualPage(1, 99)]


def test_arc_dirty_victim_writeback():
    """Verify dirty victim is written back to backing store before frame reassignment."""
    mm = MemoryManager(capacity=2, algorithm="ARC")

    # Write to page 10 (dirty) and read page 20
    mm.access_page(pid=1, vpn=10, operation="WRITE", write_data=b"MODIFIED_PAGE_10")
    mm.access_page(pid=1, vpn=20, operation="READ")

    vp10 = VirtualPage(1, 10)
    assert mm.frames[0].dirty is True

    # Access page 30: Triggers eviction of page 10
    r30 = mm.access_page(pid=1, vpn=30, operation="READ")
    assert r30.evicted_page == vp10
    assert r30.write_back_occurred is True

    # Verify data was written to backing store
    assert mm.backing_store.read_page(vp10) == b"MODIFIED_PAGE_10"
    assert mm.metrics.successful_writebacks == 1


def test_arc_failed_writeback_leaves_state_and_p_unchanged():
    """Verify backing-store write failure cleanly aborts transaction, leaving ARC state and p untouched."""
    mm = MemoryManager(capacity=2, algorithm="ARC")
    arc = mm.policy.policy

    # Page 10 (dirty) in T1, Page 20 in T2
    mm.access_page(pid=1, vpn=10, operation="WRITE", write_data=b"DIRTY_DATA")
    mm.access_page(pid=1, vpn=20, operation="READ")
    mm.access_page(pid=1, vpn=20, operation="READ")  # 20 hit -> T2=[20], T1=[10]

    vp10 = VirtualPage(1, 10)
    vp20 = VirtualPage(1, 20)
    assert mm.frames[0].dirty is True

    # Inject write error on victim (1, 10)
    mm.backing_store.inject_write_error(vp10)

    state_before = arc.get_state()
    p_before = arc.p

    with pytest.raises(DiskWriteError):
        mm.access_page(pid=1, vpn=30, operation="READ")

    # Verify ARC state is 100% identical
    assert arc.get_state() == state_before
    assert arc.p == p_before
    assert arc.requests == state_before["requests"]

    # Verify victim remains resident, mapped, and dirty
    pt = mm.get_page_table(1)
    assert pt.is_valid(10) is True
    assert mm.frames[0].vpage == vp10
    assert mm.frames[0].dirty is True
    assert mm.frames[1].vpage == vp20
    assert mm.frames[1].dirty is False


def test_arc_failed_pagein_leaves_state_and_p_unchanged():
    """Verify backing-store read failure cleanly aborts transaction, leaving ARC state and p untouched."""
    mm = MemoryManager(capacity=2, algorithm="ARC")
    arc = mm.policy.policy

    mm.access_page(1, 10)
    mm.access_page(1, 20)

    vp30 = VirtualPage(1, 30)
    mm.backing_store.inject_read_error(vp30)

    state_before = arc.get_state()
    p_before = arc.p

    with pytest.raises(DiskReadError):
        mm.access_page(1, 30)

    # ARC state and p must remain untouched
    assert arc.get_state() == state_before
    assert arc.p == p_before
    assert arc.requests == state_before["requests"]

    # Memory state unchanged
    pt = mm.get_page_table(1)
    assert pt.is_valid(10) is True
    assert pt.is_valid(20) is True
    assert not pt.is_valid(30)


def test_arc_successful_writeback_followed_by_failed_pagein_consistency():
    """Verify successful write-back followed by failed read leaves victim resident and clean."""
    mm = MemoryManager(capacity=2, algorithm="ARC")
    arc = mm.policy.policy

    # Fill cache, make page 10 dirty
    mm.access_page(1, 10, operation="WRITE", write_data=b"VICTIM_DATA")
    mm.access_page(1, 20)
    mm.access_page(1, 20)  # T1=[10], T2=[20]

    vp10 = VirtualPage(1, 10)
    vp30 = VirtualPage(1, 30)

    # Inject read error on 30. Write-back for 10 will succeed, but page-in for 30 will fail.
    mm.backing_store.inject_read_error(vp30)

    state_before = arc.get_state()

    with pytest.raises(DiskReadError):
        mm.access_page(1, 30)

    # Victim (1, 10) remains resident and mapped, but is now CLEAN because write-back succeeded
    pt = mm.get_page_table(1)
    assert pt.is_valid(10) is True
    assert mm.frames[0].vpage == vp10
    assert mm.frames[0].dirty is False
    assert mm.backing_store.read_page(vp10) == b"VICTIM_DATA"

    # ARC state and p remain unchanged
    assert arc.get_state() == state_before


def test_arc_standalone_parity():
    """Verify that MemoryManager ARC operations produce identical internal state to standalone ARCCache."""
    capacity = 3
    standalone = ARCCache(capacity=capacity)
    mm = MemoryManager(capacity=capacity, algorithm="ARC")

    reference_stream = [1, 2, 3, 1, 4, 1, 2, 5, 1, 2, 3, 4, 5, 2, 1]

    for p in reference_stream:
        # Standalone
        standalone.request(p)
        # MemoryManager (using pid=1, vpn=p)
        mm.access_page(pid=1, vpn=p)

        std_state = standalone.get_state()
        mm_arc_state = mm.policy.policy.get_state()

        # Compare T1, T2, B1, B2 (mapped back to raw vpn values)
        assert [v.vpn for v in mm_arc_state["T1"]] == std_state["T1"]
        assert [v.vpn for v in mm_arc_state["T2"]] == std_state["T2"]
        assert [v.vpn for v in mm_arc_state["B1"]] == std_state["B1"]
        assert [v.vpn for v in mm_arc_state["B2"]] == std_state["B2"]
        assert mm_arc_state["p"] == std_state["p"]
        assert mm_arc_state["hits"] == std_state["hits"]
        assert mm_arc_state["misses"] == std_state["misses"]
        assert mm_arc_state["replacements"] == std_state["replacements"]


def test_standalone_arc_backward_compatibility():
    """Verify standalone ARCCache continues to support existing request() API and telemetry."""
    cache = ARCCache(capacity=2)
    t1 = cache.request("A")
    assert t1["case"] == "CASE_4B_L1_LESS"
    assert t1["hit"] is False

    t2 = cache.request("A")
    assert t2["case"] == "CASE_1_T1_HIT"
    assert t2["hit"] is True

    assert cache.hit_ratio == 0.5
    cache.reset()
    assert cache.requests == 0
    assert cache.p == 0.0


def test_existing_policies_remain_functional():
    """Verify LRU, FIFO, LFU, and MRU continue to function properly with MemoryManager."""
    for algo in ["LRU", "FIFO", "LFU", "MRU"]:
        mm = MemoryManager(capacity=2, algorithm=algo)
        r1 = mm.access_page(1, 100)
        r2 = mm.access_page(1, 200)
        r3 = mm.access_page(1, 100)  # Hit for LRU, MRU, LFU
        assert not r1.hit and not r2.hit
        assert r3.hit is True


# =============================================================================
# ARC Safety & Monotonic State-Version Regression Tests
# =============================================================================

def test_arc_prepare_multiple_plans_no_overwrite():
    """Test 1: Prepare plan A, prepare plan B before committing A: A is not overwritten and commits cleanly."""
    arc = ARCCache(capacity=3)
    plan_a = arc.prepare_request("PAGE_A")
    plan_b = arc.prepare_request("PAGE_B")

    assert plan_a.page == "PAGE_A"
    assert plan_b.page == "PAGE_B"
    assert plan_a.state_version == 0
    assert plan_b.state_version == 0

    # Commit plan A using explicit plan parameter
    arc.insert_page("PAGE_A", frame_id=0, plan=plan_a)
    arc.commit_request(token=plan_a)

    assert arc.contains("PAGE_A") is True
    assert arc.contains("PAGE_B") is False
    assert arc.state_version == 1


def test_arc_stale_plan_rejected_after_standalone_mutation():
    """Test 2: Prepare plan, mutate ARC via standalone request: commit old plan raises StaleTokenError."""
    arc = ARCCache(capacity=3)
    plan_a = arc.prepare_request("PAGE_A")
    assert plan_a.state_version == 0

    # Mutate cache via standalone request
    arc.request("PAGE_C")
    assert arc.state_version == 1

    # Attempting to commit plan A must be rejected due to version mismatch
    with pytest.raises(StaleTokenError) as exc_info:
        arc.insert_page("PAGE_A", frame_id=0, plan=plan_a)
    assert "version" in str(exc_info.value).lower()


def test_arc_stale_plan_rejected_after_reset():
    """Test 3: Prepare plan, reset ARC: commit old plan raises StaleTokenError."""
    # Standalone mode
    arc = ARCCache(capacity=3)
    plan_a = arc.prepare_request("PAGE_A")
    arc.reset()
    assert arc.state_version == 1  # Reset advanced version monotonically

    with pytest.raises(StaleTokenError):
        arc.insert_page("PAGE_A", frame_id=0, plan=plan_a)

    # Integrated mode
    mm = MemoryManager(capacity=3, algorithm="ARC")
    integrated_arc = mm.policy.policy
    vp = VirtualPage(1, 100)
    plan_int = integrated_arc.prepare_request(vp)
    v_before = plan_int.state_version

    mm.reset()
    assert integrated_arc.state_version > v_before

    with pytest.raises(StaleTokenError):
        integrated_arc.insert_page(vp, frame_id=0, plan=plan_int)


def test_arc_integrated_direct_request_rejected():
    """Test 4: Direct arc.request() on integrated ARC: raises UnsupportedOperationError."""
    mm = MemoryManager(capacity=3, algorithm="ARC")
    arc = mm.policy.policy
    assert arc._is_integrated is True

    with pytest.raises(UnsupportedOperationError) as exc_info:
        arc.request(VirtualPage(1, 10))
    assert "integrated mode" in str(exc_info.value).lower()


def test_arc_integrated_direct_reset_rejected():
    """Test 5: Direct arc.reset() on integrated ARC: raises UnsupportedOperationError."""
    mm = MemoryManager(capacity=3, algorithm="ARC")
    arc = mm.policy.policy
    assert arc._is_integrated is True

    with pytest.raises(UnsupportedOperationError) as exc_info:
        arc.reset()
    assert "coordinated through memorymanager.reset()" in str(exc_info.value).lower()


def test_arc_coordinated_reset_preserves_mode_and_advances_version():
    """Test 6: Coordinated mm.reset(): succeeds, resets ARC & memory structures consistently, advances state_version."""
    mm = MemoryManager(capacity=3, algorithm="ARC")
    arc = mm.policy.policy

    mm.access_page(1, 10)
    mm.access_page(1, 20)
    v_active = arc.state_version

    # Execute coordinated reset
    mm.reset()

    # Integrated mode preserved
    assert arc._is_integrated is True
    # Monotonically advanced state_version
    assert arc.state_version > v_active
    # Subsystems empty and consistent
    assert len(arc.T1) == 0 and len(arc.T2) == 0 and len(arc.B1) == 0 and len(arc.B2) == 0
    assert arc.p == 0.0
    assert len(mm.get_resident_pages()) == 0
    assert mm.allocator.has_free_frame() is True
    assert mm.allocator.free_count == 3
    assert mm.metrics.requests == 0
    assert mm.is_consistent is True


def test_arc_multi_step_commit_session():
    """Test 7: Multi-step ARC commit using one plan: succeeds without false version mismatch."""
    arc = ARCCache(capacity=2)
    # Fill cache: A hit promotes A to T2, B in T1
    arc.request("A")
    arc.request("A")  # Promoted to T2
    arc.request("B")  # Placed in T1
    v_before = arc.state_version

    # Now T1=[B], T2=[A]. len(T1) = 1 < 2.
    # Request for C: |L1| < 2. len(T1) = 1 > p (0.0). Evicts B to B1!
    plan_c = arc.prepare_request("C")
    assert plan_c.requires_eviction is True
    assert plan_c.victim == "B"
    assert plan_c.eviction_type == "T1->B1"
    assert plan_c.state_version == v_before

    # Multi-step commit: evict_victim binds the active commit session, insert_page continues it
    arc.evict_victim(plan_c.victim, plan=plan_c)
    assert arc._active_commit_plan is plan_c

    arc.insert_page("C", frame_id=0, plan=plan_c)
    assert arc._active_commit_plan is plan_c

    # Finalize commit
    arc.commit_request(token=plan_c)
    assert arc._active_commit_plan is None
    assert arc.state_version == v_before + 1
    assert arc.contains("C") is True
    assert arc.contains("A") is True
    assert "B" in arc.B1


def test_arc_failed_writeback_preserves_version_and_state():
    """Test 8: Failed write-back: version and ARC state unchanged."""
    mm = MemoryManager(capacity=2, algorithm="ARC")
    arc = mm.policy.policy

    mm.access_page(1, 10, operation="WRITE", write_data=b"DATA")
    mm.access_page(1, 20)

    v_before = arc.state_version
    state_before = arc.get_state()

    vp10 = VirtualPage(1, 10)
    mm.backing_store.inject_write_error(vp10)

    with pytest.raises(DiskWriteError):
        mm.access_page(1, 30)

    assert arc.state_version == v_before
    assert arc.get_state() == state_before


def test_arc_failed_pagein_preserves_version_and_state():
    """Test 9: Failed page-in: version and ARC state unchanged."""
    mm = MemoryManager(capacity=2, algorithm="ARC")
    arc = mm.policy.policy

    mm.access_page(1, 10)
    mm.access_page(1, 20)

    v_before = arc.state_version
    state_before = arc.get_state()

    vp30 = VirtualPage(1, 30)
    mm.backing_store.inject_read_error(vp30)

    with pytest.raises(DiskReadError):
        mm.access_page(1, 30)

    assert arc.state_version == v_before
    assert arc.get_state() == state_before


def test_arc_unexpected_commit_exception_marks_corrupted_state():
    """Test 10: Unexpected commit exception: manager marked inconsistent (CorruptedManagerStateError)."""
    mm = MemoryManager(capacity=2, algorithm="ARC")
    mm.access_page(1, 10)

    with patch.object(mm.policy, "insert_page", side_effect=RuntimeError("Hardware fault during commit")):
        with pytest.raises(CorruptedManagerStateError):
            mm.access_page(1, 20)

    # Manager entered panic mode
    assert mm.is_consistent is False

    # All future accesses must be blocked
    with pytest.raises(CorruptedManagerStateError):
        mm.access_page(1, 10)


def test_arc_standalone_monotonic_state_version():
    """Test 11: Standalone ARC requests and reset remain backward-compatible and monotonic."""
    arc = ARCCache(capacity=3)
    assert arc.state_version == 0

    arc.request(1)
    assert arc.state_version == 1

    arc.request(2)
    assert arc.state_version == 2

    # Resident hit
    arc.request(1)
    assert arc.state_version == 3

    # Reset
    arc.reset()
    assert arc.state_version == 4
    assert arc.requests == 0
    assert arc.hits == 0

    # New request after reset
    arc.request(5)
    assert arc.state_version == 5


def test_arc_binding_rules():
    """Test 12: Binding is a one-way transition from pristine standalone to integrated mode."""
    # 1. Pristine standalone binds successfully
    arc1 = ARCCache(capacity=2)
    assert arc1._is_integrated is False
    arc1.bind_to_manager()
    assert arc1._is_integrated is True

    # 2. Cannot rebind already bound instance
    with pytest.raises(BindingError):
        arc1.bind_to_manager()

    # 3. Cannot bind non-pristine instance
    arc2 = ARCCache(capacity=2)
    arc2.request(10)
    with pytest.raises(BindingError):
        arc2.bind_to_manager()

    # 4. Initializing MemoryManager with non-pristine instance raises BindingError
    with pytest.raises(BindingError):
        MemoryManager(capacity=2, algorithm=arc2)


def test_arc_stale_plan_rejected_on_t1_middle_reorder():
    """Verify middle element reordering in T1 (preserving length and LRU) invalidates outstanding plans."""
    arc = ARCCache(capacity=4)
    arc.request(10)
    arc.request(20)
    arc.request(30)
    # LRU is 10, middle is 20, MRU is 30.
    plan = arc.prepare_request(40)
    assert plan.state_version == 3

    # Reorder middle element 20 to the MRU end: order becomes [10, 30, 20]
    # Notice: len(T1) remains 3, LRU is still 10!
    arc.T1.move_to_end(20, last=True)

    # state_version is NOT advanced by direct collection modification
    assert arc.state_version == 3

    # Attempting to commit plan must be rejected via complete directory signature check
    with pytest.raises(StaleTokenError) as exc_info:
        arc.insert_page(40, frame_id=3, plan=plan)
    assert "directory was modified" in str(exc_info.value).lower()


def test_arc_stale_plan_rejected_on_t2_middle_reorder():
    """Verify middle element reordering in T2 (preserving length and LRU) invalidates outstanding plans."""
    arc = ARCCache(capacity=4)
    arc.request(10); arc.request(10)
    arc.request(20); arc.request(20)
    arc.request(30); arc.request(30)
    # T2 has [10, 20, 30]. LRU is 10, middle is 20, MRU is 30.
    plan = arc.prepare_request(40)

    # Reorder middle element 20 to the MRU end: order becomes [10, 30, 20]
    arc.T2.move_to_end(20, last=True)

    with pytest.raises(StaleTokenError) as exc_info:
        arc.insert_page(40, frame_id=3, plan=plan)
    assert "directory was modified" in str(exc_info.value).lower()


def test_arc_stale_plan_rejected_on_b1_middle_reorder():
    """Verify middle element reordering in B1 (preserving length and LRU) invalidates outstanding plans."""
    arc = ARCCache(capacity=4)
    arc.B1[10] = None
    arc.B1[20] = None
    arc.B1[30] = None
    plan = arc.prepare_request(40)

    # Reorder middle element 20
    arc.B1.move_to_end(20, last=True)

    with pytest.raises(StaleTokenError) as exc_info:
        arc.insert_page(40, frame_id=0, plan=plan)
    assert "directory was modified" in str(exc_info.value).lower()


def test_arc_stale_plan_rejected_on_b2_middle_reorder():
    """Verify middle element reordering in B2 (preserving length and LRU) invalidates outstanding plans."""
    arc = ARCCache(capacity=4)
    arc.B2[10] = None
    arc.B2[20] = None
    arc.B2[30] = None
    plan = arc.prepare_request(40)

    # Reorder middle element 20
    arc.B2.move_to_end(20, last=True)

    with pytest.raises(StaleTokenError) as exc_info:
        arc.insert_page(40, frame_id=0, plan=plan)
    assert "directory was modified" in str(exc_info.value).lower()


def test_base_exception_in_commit_marks_inconsistent_and_cleans_up():
    """Verify BaseException in Phase 3 latches inconsistent state, releases lock, and cleans up active session."""
    class MockProcessInterrupt(BaseException):
        pass

    mm = MemoryManager(capacity=2, algorithm="ARC")
    mm.access_page(1, 10)
    arc = mm.policy.policy

    # Inject MockProcessInterrupt inside Phase 3 commit
    with patch.object(mm.policy, "insert_page", side_effect=MockProcessInterrupt("Forced interrupt")):
        with pytest.raises(MockProcessInterrupt):
            mm.access_page(1, 20)

    # 1. Manager marked inconsistent
    assert mm.is_consistent is False

    # 2. Transaction guard released
    assert mm._in_transaction is False

    # 3. Active ARC commit session invalidated
    assert arc._active_commit_plan is None
    assert arc._pending_plan is None

    # 4. Subsequent access is rejected with CorruptedManagerStateError
    with pytest.raises(CorruptedManagerStateError):
        mm.access_page(1, 10)


def test_keyboard_interrupt_preserves_exception_and_releases_lock():
    """Verify KeyboardInterrupt preserves original exception type and invalidates commit session."""
    mm = MemoryManager(capacity=2, algorithm="ARC")
    mm.access_page(1, 10)
    arc = mm.policy.policy

    with patch.object(mm.policy, "insert_page", side_effect=KeyboardInterrupt("User aborted")):
        with pytest.raises(KeyboardInterrupt):
            mm.access_page(1, 20)

    assert mm.is_consistent is False
    assert mm._in_transaction is False
    assert arc._active_commit_plan is None
    with pytest.raises(CorruptedManagerStateError):
        mm.access_page(1, 10)


