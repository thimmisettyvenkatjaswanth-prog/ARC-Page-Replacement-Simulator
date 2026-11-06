"""
Comprehensive integration tests for Stage 12.2 Phase 3B:
CLOCK (Second Chance) page replacement policy integration with MemoryManager.

Covers all 20 required verification scenarios:
  1. Pristine standalone CLOCK binding success.
  2. Rebinding already-bound CLOCK instance raises BindingError.
  3. Binding non-pristine CLOCK instance raises BindingError.
  4. READ access sets PhysicalFrame.reference_bit to True.
  5. WRITE access sets PhysicalFrame.reference_bit to True and dirty to True.
  6. CLOCK queries authoritative PhysicalFrame reference bit, not duplicate internal array.
  7. Second-chance scanning clears PhysicalFrame.reference_bit on physical frames.
  8. CLOCK hand movement and victim selection correctness.
  9. Free frames are never selected as victims (allocated before evicting).
  10. ClockVictimToken is immutable (@dataclass(frozen=True)).
  11. Stale tokens rejected after external reference-bit mutation via manager.set_frame_reference_bit.
  12. Stale tokens rejected after hand/state mutation.
  13. Non-mutating victim planning in Phase 1 (frames and CLOCK state unchanged).
  14. Failed backing-store write preserves CLOCK state and physical frames.
  15. Failed backing-store read preserves CLOCK state and physical frames.
  16. Successful eviction and insertion updates CLOCK state and physical frames correctly.
  17. Coordinated MemoryManager.reset() resets CLOCK, monotonically increments version, keeps _is_integrated.
  18. Direct clock.reset(), clock.request(), clock.set_reference() rejected with UnsupportedOperationError.
  19. Unexpected commit exceptions invalidate tokens and latch is_consistent = False.
  20. Standalone CLOCK behavior and backward compatibility preserved.
"""
from dataclasses import FrozenInstanceError
from unittest.mock import patch
import pytest

from backend.algorithms.clock import (
    CLOCKCache,
    ClockHitToken,
    ClockVictimToken,
    ClockWarmupToken,
)
from backend.memory.backing_store import BackingStore
from backend.memory.exceptions import (
    BindingError,
    CorruptedManagerStateError,
    DiskReadError,
    DiskWriteError,
    StaleTokenError,
    UnsupportedOperationError,
)
from backend.memory.manager import MemoryManager
from backend.memory.page import VirtualPage


# =============================================================================
# 1. Binding Verification
# =============================================================================

def test_clock_binding_pristine_success():
    """1. Binding a pristine standalone CLOCK instance to MemoryManager succeeds and sets _is_integrated = True."""
    clock = CLOCKCache(3)
    assert not clock._is_integrated
    assert clock.state_version == 0

    mm = MemoryManager(capacity=3, algorithm=clock)
    assert clock._is_integrated is True
    assert mm.policy.name == "CLOCKCache"
    assert clock.state_version == 0


def test_clock_rebinding_rejected():
    """2. Rebinding an already-bound CLOCK instance raises BindingError."""
    clock = CLOCKCache(3)
    mm1 = MemoryManager(capacity=3, algorithm=clock)
    assert clock._is_integrated is True

    # Attempt to bind to another MemoryManager
    with pytest.raises(BindingError) as exc_info:
        MemoryManager(capacity=3, algorithm=clock)
    assert "already bound" in str(exc_info.value)

    # Attempt direct bind_to_manager call
    with pytest.raises(BindingError) as exc_direct:
        clock.bind_to_manager()
    assert "already bound" in str(exc_direct.value)


def test_clock_non_pristine_binding_rejected():
    """3. Binding a non-pristine CLOCK instance (after request or mutation) raises BindingError."""
    # Used via standalone request()
    c1 = CLOCKCache(3)
    c1.request("A")
    with pytest.raises(BindingError) as exc_req:
        MemoryManager(capacity=3, algorithm=c1)
    assert "non-pristine" in str(exc_req.value)

    # Hand mutated
    c2 = CLOCKCache(3)
    c2.hand = 1
    with pytest.raises(BindingError) as exc_hand:
        MemoryManager(capacity=3, algorithm=c2)
    assert "non-pristine" in str(exc_hand.value)

    # Reference bits mutated
    c3 = CLOCKCache(3)
    c3.reference_bits[0] = 1
    with pytest.raises(BindingError) as exc_ref:
        MemoryManager(capacity=3, algorithm=c3)
    assert "non-pristine" in str(exc_ref.value)

    # Slots mutated
    c4 = CLOCKCache(3)
    c4.slots[0] = "X"
    with pytest.raises(BindingError) as exc_slot:
        MemoryManager(capacity=3, algorithm=c4)
    assert "non-pristine" in str(exc_slot.value)


# =============================================================================
# 2. Reference and Dirty Bit Hardware Semantics
# =============================================================================

def test_clock_read_sets_frame_reference_bit():
    """4. READ access to resident page sets PhysicalFrame.reference_bit to True."""
    mm = MemoryManager(capacity=3, algorithm="CLOCK")

    # Miss: Cold load page (1, 10)
    r1 = mm.access_page(pid=1, vpn=10, operation="READ")
    assert r1.hit is False
    assert r1.frame_id == 0
    frame0 = mm.get_frame(0)
    assert frame0.reference_bit is True
    assert frame0.dirty is False

    # Clear reference bit externally to simulate hardware aging
    mm.set_frame_reference_bit(0, False)
    assert frame0.reference_bit is False

    # Hit: Access page (1, 10) again with READ
    r2 = mm.access_page(pid=1, vpn=10, operation="READ")
    assert r2.hit is True
    assert frame0.reference_bit is True
    assert frame0.dirty is False


def test_clock_write_sets_reference_and_dirty_bits():
    """5. WRITE access to resident page sets PhysicalFrame.reference_bit to True and dirty to True."""
    mm = MemoryManager(capacity=3, algorithm="CLOCK")

    # Cold WRITE: Page (1, 20)
    r1 = mm.access_page(pid=1, vpn=20, operation="WRITE", write_data={"val": 42})
    assert r1.hit is False
    frame0 = mm.get_frame(0)
    assert frame0.reference_bit is True
    assert frame0.dirty is True
    assert frame0.data == {"val": 42}

    # Cold READ: Page (1, 30) -> Frame 1
    r2 = mm.access_page(pid=1, vpn=30, operation="READ")
    frame1 = mm.get_frame(1)
    assert frame1.reference_bit is True
    assert frame1.dirty is False

    # Hit WRITE: Page (1, 30)
    r3 = mm.access_page(pid=1, vpn=30, operation="WRITE", write_data="updated")
    assert r3.hit is True
    assert frame1.reference_bit is True
    assert frame1.dirty is True
    assert frame1.data == "updated"


# =============================================================================
# 3. Reference Bit Authority and Second-Chance Scanning
# =============================================================================

def test_clock_queries_authoritative_physical_frame_reference_bit():
    """6. CLOCK queries authoritative PhysicalFrame reference bit, not duplicate internal array."""
    mm = MemoryManager(capacity=3, algorithm="CLOCK")
    clock = mm.policy.policy

    # Load 3 pages to fill capacity
    mm.access_page(1, 1)  # Frame 0
    mm.access_page(1, 2)  # Frame 1
    mm.access_page(1, 3)  # Frame 2

    # Physical frames all have reference_bit == True
    for fid in range(3):
        assert mm.get_frame(fid).reference_bit is True
        assert clock.get_reference(fid) == 1

    # Mutate physical frame 1 via MemoryManager controlled mutator
    mm.set_frame_reference_bit(1, False)
    assert mm.get_frame(1).reference_bit is False
    # CLOCK must read the authoritative hardware state through callback
    assert clock.get_reference(1) == 0

    # Internal reference_bits array in clock cache is not used as truth
    assert clock.reference_bits[1] == 0


def test_clock_second_chance_clears_physical_frame_reference_bits():
    """7. Second-chance scanning clears PhysicalFrame.reference_bit on physical frames."""
    mm = MemoryManager(capacity=3, algorithm="CLOCK")

    # Load pages 1, 2, 3 into frames 0, 1, 2 (all ref=True)
    mm.access_page(1, 1)  # Frame 0, ref=1
    mm.access_page(1, 2)  # Frame 1, ref=1
    mm.access_page(1, 3)  # Frame 2, ref=1
    clock = mm.policy.policy
    assert clock.hand == 0

    # Access page 4 (miss; memory full)
    # Hand at 0:
    # Frame 0 has ref=1 -> second chance: cleared to 0, hand -> 1
    # Frame 1 has ref=1 -> second chance: cleared to 0, hand -> 2
    # Frame 2 has ref=1 -> second chance: cleared to 0, hand -> 0
    # Frame 0 now has ref=0 -> selected as victim!
    r4 = mm.access_page(1, 4)
    assert r4.hit is False
    assert r4.evicted_page == VirtualPage(1, 1)
    assert r4.frame_id == 0

    # Verify physical frames after commit:
    # Frame 0 has page 4 with ref=True (newly loaded)
    assert mm.get_frame(0).vpage == VirtualPage(1, 4)
    assert mm.get_frame(0).reference_bit is True

    # Frames 1 and 2 received second chance: reference_bit must be False!
    assert mm.get_frame(1).vpage == VirtualPage(1, 2)
    assert mm.get_frame(1).reference_bit is False

    assert mm.get_frame(2).vpage == VirtualPage(1, 3)
    assert mm.get_frame(2).reference_bit is False


def test_clock_hand_movement_and_victim_selection():
    """8. CLOCK hand advances correctly during victim selection and points to frame after victim."""
    mm = MemoryManager(capacity=3, algorithm="CLOCK")
    clock = mm.policy.policy

    mm.access_page(1, 1)  # Frame 0
    mm.access_page(1, 2)  # Frame 1
    mm.access_page(1, 3)  # Frame 2
    # Hand at 0

    # Access 4: Sweeps 0, 1, 2 (clearing bits), evicts 0. Hand moves to 1.
    mm.access_page(1, 4)
    assert clock.hand == 1
    assert mm.get_frame(1).reference_bit is False
    assert mm.get_frame(2).reference_bit is False

    # Access 5 (miss):
    # Hand is at 1. Frame 1 has reference_bit == 0.
    # It must be selected immediately without clearing frame 2!
    r5 = mm.access_page(1, 5)
    assert r5.hit is False
    assert r5.evicted_page == VirtualPage(1, 2)
    assert r5.frame_id == 1
    # Hand advances to (1 + 1) % 3 = 2
    assert clock.hand == 2

    # Frame 2 reference_bit must STILL be False (not swept/cleared)
    assert mm.get_frame(2).reference_bit is False
    # Frame 1 now holds page 5 (ref=True)
    assert mm.get_frame(1).vpage == VirtualPage(1, 5)
    assert mm.get_frame(1).reference_bit is True


def test_clock_free_frames_never_selected_as_victims():
    """9. Free frames are never selected as victims; allocation uses free frames before evicting."""
    mm = MemoryManager(capacity=4, algorithm="CLOCK")
    clock = mm.policy.policy

    # Access 3 pages in 4-frame capacity
    r1 = mm.access_page(1, 10)
    r2 = mm.access_page(1, 20)
    r3 = mm.access_page(1, 30)

    assert mm.allocator.has_free_frame() is True
    assert mm.metrics.successful_evictions == 0
    assert r1.evicted_page is None
    assert r2.evicted_page is None
    assert r3.evicted_page is None

    # Access 4th page (still 1 free frame: frame 3)
    r4 = mm.access_page(1, 40)
    assert r4.hit is False
    assert r4.frame_id == 3
    assert r4.evicted_page is None
    assert mm.metrics.successful_evictions == 0

    # All 4 frames now allocated
    assert mm.allocator.has_free_frame() is False
    assert len(mm.get_resident_pages()) == 4


# =============================================================================
# 4. Token Safety and Immutability
# =============================================================================

def test_clock_victim_token_immutability():
    """10. ClockVictimToken is immutable (@dataclass(frozen=True))."""
    mm = MemoryManager(capacity=2, algorithm="CLOCK")
    mm.access_page(1, 1)
    mm.access_page(1, 2)

    val_token = mm.policy.validate_request(VirtualPage(1, 3))
    victim_token = mm.policy.select_victim(token=val_token)
    assert isinstance(victim_token, ClockVictimToken)

    # Immutability verification
    with pytest.raises(FrozenInstanceError):
        victim_token.victim_page = VirtualPage(1, 99)

    with pytest.raises(FrozenInstanceError):
        victim_token.victim_frame_id = 99

    with pytest.raises(FrozenInstanceError):
        victim_token.hand_after = 99

    with pytest.raises(FrozenInstanceError):
        victim_token.state_version = 99

    # Clean up abort
    mm.policy.abort_request(val_token)


def test_clock_stale_token_rejected_on_external_ref_bit_mutation():
    """11. Stale tokens rejected after external reference-bit mutation via manager.set_frame_reference_bit."""
    mm = MemoryManager(capacity=2, algorithm="CLOCK")
    mm.access_page(1, 1)
    mm.access_page(1, 2)

    clock = mm.policy.policy
    val_token = mm.policy.validate_request(VirtualPage(1, 3))
    victim_token = mm.policy.select_victim(token=val_token)
    assert isinstance(victim_token, ClockVictimToken)

    # External hardware mutation outside active commit
    mm.set_frame_reference_bit(0, False)

    # Attempting to evict with the token must raise StaleTokenError
    with pytest.raises(StaleTokenError) as exc_info:
        mm.policy.evict_victim(victim_token.victim_page, victim_token)
    assert "Stale" in str(exc_info.value)

    clock.abort_request()


def test_clock_stale_token_rejected_on_state_mutation():
    """12. Stale tokens rejected after hand/state mutation."""
    mm = MemoryManager(capacity=2, algorithm="CLOCK")
    mm.access_page(1, 1)
    mm.access_page(1, 2)

    clock = mm.policy.policy
    val_token = mm.policy.validate_request(VirtualPage(1, 3))
    victim_token = mm.policy.select_victim(token=val_token)

    # Intervening completed transaction
    mm.access_page(1, 1)  # Hit on page 1

    # Token has old state_version
    with pytest.raises(StaleTokenError) as exc_info:
        mm.policy.evict_victim(victim_token.victim_page, victim_token)
    assert "Stale" in str(exc_info.value)

    clock.abort_request()


# =============================================================================
# 5. Non-Mutating Victim Planning
# =============================================================================

def test_clock_non_mutating_victim_planning():
    """13. Victim selection planning in Phase 1 causes zero state mutations."""
    mm = MemoryManager(capacity=3, algorithm="CLOCK")
    mm.access_page(1, 1)
    mm.access_page(1, 2)
    mm.access_page(1, 3)

    clock = mm.policy.policy
    hand_before = clock.hand
    version_before = clock.state_version
    ref_bits_before = [mm.get_frame(i).reference_bit for i in range(3)]
    slots_before = list(clock.slots)

    # Pre-validation & victim selection
    val_token = mm.policy.validate_request(VirtualPage(1, 4))
    victim_token = mm.policy.select_victim(token=val_token)

    # State must be 100% identical after planning
    assert clock.hand == hand_before
    assert clock.state_version == version_before
    assert [mm.get_frame(i).reference_bit for i in range(3)] == ref_bits_before
    assert list(clock.slots) == slots_before

    # Token itself planned clearing of frames, but physical frames were untouched
    assert victim_token.cleared_frames == (0, 1, 2)
    assert all(ref_bits_before)  # All were True

    mm.policy.abort_request(val_token)


# =============================================================================
# 6. Fallible I/O and Transaction Failure Isolation
# =============================================================================

def test_clock_failed_backing_store_write_preserves_state():
    """14. Failed backing-store write preserves CLOCK state and physical frames."""
    mm = MemoryManager(capacity=2, algorithm="CLOCK")
    clock = mm.policy.policy

    # Populate 2 dirty frames
    mm.access_page(1, 1, operation="WRITE", write_data="p1_dirty")
    mm.access_page(1, 2, operation="WRITE", write_data="p2_dirty")

    hand_before = clock.hand
    version_before = clock.state_version
    slots_before = list(clock.slots)

    # Fail write-back in Phase 2
    with patch.object(mm.backing_store, "write_page", side_effect=IOError("Disk write fault")):
        with pytest.raises(DiskWriteError) as exc_info:
            mm.access_page(1, 3)
        assert "Failed write-back" in str(exc_info.value)

    # State preservation checks:
    # 1. Victim (page 1 in frame 0) remains resident, dirty, and referenced
    frame0 = mm.get_frame(0)
    assert frame0.vpage == VirtualPage(1, 1)
    assert frame0.dirty is True
    assert frame0.reference_bit is True
    assert frame0.data == "p1_dirty"

    # 2. CLOCK cache state unchanged
    assert clock.hand == hand_before
    assert clock.state_version == version_before
    assert list(clock.slots) == slots_before
    assert clock._active_commit_token is None

    # 3. Manager is healthy and can process subsequent operations
    assert mm.is_consistent is True
    assert mm.metrics.failed_pagefaults == 1


def test_clock_failed_backing_store_read_preserves_state():
    """15. Failed backing-store read preserves CLOCK state and physical frames."""
    mm = MemoryManager(capacity=2, algorithm="CLOCK")
    clock = mm.policy.policy

    # Clean frames
    mm.access_page(1, 1, operation="READ")
    mm.access_page(1, 2, operation="READ")

    hand_before = clock.hand
    version_before = clock.state_version
    slots_before = list(clock.slots)

    # Fail page-in read in Phase 2
    with patch.object(mm.backing_store, "read_page", side_effect=IOError("Disk read fault")):
        with pytest.raises(DiskReadError) as exc_info:
            mm.access_page(1, 3)
        assert "Failed to read page" in str(exc_info.value)

    # Victim remains resident and referenced
    frame0 = mm.get_frame(0)
    assert frame0.vpage == VirtualPage(1, 1)
    assert frame0.reference_bit is True

    # CLOCK cache state unchanged
    assert clock.hand == hand_before
    assert clock.state_version == version_before
    assert list(clock.slots) == slots_before
    assert clock._active_commit_token is None
    assert mm.is_consistent is True


# =============================================================================
# 7. Lifecycle Mutation and Coordinated Reset
# =============================================================================

def test_clock_successful_eviction_and_insertion_lifecycle():
    """16. Successful eviction and insertion updates CLOCK state and physical frames correctly."""
    mm = MemoryManager(capacity=3, algorithm="CLOCK")
    clock = mm.policy.policy

    # Cold fills
    mm.access_page(1, 10, operation="WRITE", write_data="data10")
    mm.access_page(1, 20, operation="READ")
    mm.access_page(1, 30, operation="READ")

    # Hit on 20 sets reference
    mm.access_page(1, 20, operation="READ")

    # Access 40 (miss): Eviction required
    r = mm.access_page(1, 40, operation="READ")
    assert r.hit is False
    assert r.evicted_page == VirtualPage(1, 10)
    assert r.write_back_occurred is True
    assert r.frame_id == 0

    # Backing store must have preserved dirty write-back
    assert mm.backing_store.read_page(VirtualPage(1, 10)) == "data10"

    # Frame 0 holds page 40
    frame0 = mm.get_frame(0)
    assert frame0.vpage == VirtualPage(1, 40)
    assert frame0.reference_bit is True
    assert frame0.dirty is False

    # Page table mapping updated
    assert mm.get_page_table(1).is_valid(40) is True
    assert mm.get_page_table(1).is_valid(10) is False

    # CLOCK internal tracking matches
    assert clock.slots[0] == VirtualPage(1, 40)
    assert clock.page_to_slot[VirtualPage(1, 40)] == 0
    assert VirtualPage(1, 10) not in clock.page_to_slot


def test_clock_coordinated_reset():
    """17. Coordinated MemoryManager.reset() resets CLOCK, monotonically increments version, keeps _is_integrated."""
    mm = MemoryManager(capacity=3, algorithm="CLOCK")
    clock = mm.policy.policy

    mm.access_page(1, 1)
    mm.access_page(1, 2)
    mm.access_page(1, 3)
    mm.access_page(1, 4)

    version_before = clock.state_version
    assert clock._is_integrated is True

    mm.reset()

    # Invariants preserved
    assert clock._is_integrated is True
    assert clock.state_version > version_before
    assert clock.hand == 0
    assert len(clock.page_to_slot) == 0
    assert all(s is None for s in clock.slots)
    assert mm.is_consistent is True
    assert len(mm.get_resident_pages()) == 0
    assert mm.metrics.requests == 0


def test_clock_direct_operations_rejected_in_integrated_mode():
    """18. Direct clock.reset(), clock.request(), clock.set_reference() rejected with UnsupportedOperationError."""
    mm = MemoryManager(capacity=3, algorithm="CLOCK")
    clock = mm.policy.policy

    with pytest.raises(UnsupportedOperationError) as exc_req:
        clock.request("A")
    assert "Direct call to CLOCKCache.request() is unsupported" in str(exc_req.value)

    with pytest.raises(UnsupportedOperationError) as exc_res:
        clock.reset()
    assert "Direct call to CLOCKCache.reset() is unsupported" in str(exc_res.value)

    with pytest.raises(UnsupportedOperationError) as exc_set:
        clock.set_reference(0, 1)
    assert "Direct reference-bit modification is unsupported" in str(exc_set.value)


# =============================================================================
# 8. Panic Guard and Standalone Compatibility
# =============================================================================

def test_clock_commit_unexpected_exception_marks_inconsistent():
    """19. Unexpected commit exceptions invalidate tokens and latch is_consistent = False."""
    mm = MemoryManager(capacity=2, algorithm="CLOCK")
    mm.access_page(1, 1)

    # Patch policy insert_page to raise unexpected error during Phase 3
    with patch.object(mm.policy, "insert_page", side_effect=RuntimeError("Hardware fatal error")):
        with pytest.raises(CorruptedManagerStateError) as exc_info:
            mm.access_page(1, 2)
        assert "Fatal commit failure" in str(exc_info.value)

    # Manager must be latched as inconsistent
    assert mm.is_consistent is False

    # Subsequent access must immediately raise CorruptedManagerStateError
    with pytest.raises(CorruptedManagerStateError) as exc_sub:
        mm.access_page(1, 3)
    assert "inconsistent panic state" in str(exc_sub.value)


def test_standalone_clock_backward_compatibility():
    """20. Standalone CLOCK behavior and all existing standalone tests continue passing unchanged."""
    clock = CLOCKCache(capacity=3)
    assert not clock._is_integrated

    r1 = clock.request("A")
    assert r1["hit"] is False
    assert r1["evicted_page"] is None

    r2 = clock.request("B")
    r3 = clock.request("C")
    assert clock.size == 3

    # Hit on A
    r4 = clock.request("A")
    assert r4["hit"] is True

    # Miss: All slots have bit 1; full sweep clears bits, evicting slot 0 ("A")
    r5 = clock.request("D")
    assert r5["hit"] is False
    assert r5["evicted_page"] == "A"

    # Reset
    clock.reset()
    assert clock.hand == 0
    assert clock.requests == 0
    assert clock.size == 0
    assert not clock._is_integrated
