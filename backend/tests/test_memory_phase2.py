"""
Unit and Integration tests for Stage 12.2 Phase 2:
BackingStore and MemoryManager Page-Fault Lifecycle.
"""
from unittest.mock import patch
import pytest

from backend.memory.backing_store import BackingStore
from backend.memory.exceptions import (
    BackingStoreError,
    ConcurrentTransactionError,
    CorruptedManagerStateError,
    DiskReadError,
    DiskWriteError,
    NoFreeFrameError,
    PageNotFoundError,
)
from backend.memory.manager import AccessResult, MemoryManager
from backend.memory.page import VirtualPage


# =============================================================================
# 1. Backing Store Tests
# =============================================================================

def test_backing_store_initialization_and_reads():
    """Verify backing store auto-population, custom factories, and reads."""
    bs = BackingStore(auto_populate=True)
    vp = VirtualPage(pid=1, vpn=0)

    # First read auto-generates deterministic data
    data = bs.read_page(vp)
    assert data == "data_1_0"
    assert bs.successful_reads == 1
    assert bs.failed_reads == 0

    # Custom factory
    bs_custom = BackingStore(
        auto_populate=True,
        default_factory=lambda p: f"custom_payload_{p.page_id}"
    )
    assert bs_custom.read_page(vp) == "custom_payload_(1, 0)"

    # Auto-populate=False raises PageNotFoundError on uninitialized page
    bs_strict = BackingStore(auto_populate=False)
    with pytest.raises(PageNotFoundError):
        bs_strict.read_page(vp)
    assert bs_strict.failed_reads == 1


def test_backing_store_page_writes_and_persistence():
    """Verify page writes, persistence across reads, and write error injection."""
    bs = BackingStore()
    vp = VirtualPage(pid=2, vpn=15)

    # Explicit write
    bs.write_page(vp, "persisted_value_42")
    assert bs.successful_writes == 1
    assert bs.contains_page(vp) is True
    assert bs.read_page(vp) == "persisted_value_42"

    # Inject write failure
    bs.fail_next_write()
    with pytest.raises(DiskWriteError):
        bs.write_page(vp, "should_fail")
    assert bs.failed_writes == 1

    # Injected read failure
    bs.fail_next_read()
    with pytest.raises(DiskReadError):
        bs.read_page(vp)
    assert bs.failed_reads == 1


# =============================================================================
# 2. MemoryManager Hit and Page Fault Lifecycle
# =============================================================================

def test_memory_manager_page_hits():
    """Verify resident page hit detection, frame consistency, and telemetry."""
    mm = MemoryManager(capacity=3, algorithm="LRU")

    # Initial access is a cold miss (page fault)
    res1 = mm.access_page(pid=1, vpn=0, operation="READ")
    assert res1.hit is False
    assert res1.frame_id == 0
    assert mm.metrics.misses == 1
    assert mm.metrics.hits == 0

    # Second access to the same page is a resident hit
    res2 = mm.access_page(pid=1, vpn=0, operation="READ")
    assert res2.hit is True
    assert res2.frame_id == 0
    assert mm.metrics.hits == 1
    assert mm.metrics.requests == 2
    assert mm.metrics.hit_ratio == 0.5


def test_memory_manager_page_faults_and_free_frame_allocation():
    """Verify sequential page faults allocate free frames in FIFO order."""
    mm = MemoryManager(capacity=3, algorithm="LRU")

    # Access 3 distinct pages
    res0 = mm.access_page(pid=1, vpn=10)
    res1 = mm.access_page(pid=1, vpn=20)
    res2 = mm.access_page(pid=1, vpn=30)

    assert res0.frame_id == 0
    assert res1.frame_id == 1
    assert res2.frame_id == 2

    assert mm.allocator.free_count == 0
    assert mm.allocator.allocated_count == 3
    assert len(mm.get_resident_pages()) == 3


def test_memory_manager_physical_memory_exhaustion_and_eviction():
    """Verify victim selection, frame reuse, and unmapping upon memory full."""
    mm = MemoryManager(capacity=2, algorithm="LRU")

    # Fill memory: Frame 0 -> (1, 10), Frame 1 -> (1, 20)
    mm.access_page(pid=1, vpn=10)
    mm.access_page(pid=1, vpn=20)

    # Access page (1, 30): Memory full; LRU victim is (1, 10) in Frame 0
    res3 = mm.access_page(pid=1, vpn=30)
    assert res3.hit is False
    assert res3.frame_id == 0
    assert res3.evicted_page == VirtualPage(1, 10)
    assert mm.metrics.successful_evictions == 1

    # Verify PageTable mappings
    pt = mm.get_page_table(1)
    assert pt.is_valid(10) is False
    assert pt.get_frame_id(10) is None
    assert pt.is_valid(30) is True
    assert pt.get_frame_id(30) == 0


def test_memory_manager_dirty_and_clean_eviction():
    """Verify clean evictions skip write-back, and dirty evictions persist to backing store."""
    bs = BackingStore()
    mm = MemoryManager(capacity=2, algorithm="LRU", backing_store=bs)

    # 1. Load clean page into Frame 0
    mm.access_page(pid=1, vpn=1, operation="READ")
    assert mm.get_frame(0).dirty is False

    # 2. Load and write to page in Frame 1
    mm.access_page(pid=1, vpn=2, operation="WRITE", write_data="dirty_data_v2")
    assert mm.get_frame(1).dirty is True

    # 3. Touch page 2 again (READ hit) -> LRU victim is now page 1
    mm.access_page(pid=1, vpn=2, operation="READ")

    # 4. Access page 3 (evicts clean page 1) -> Clean eviction (no write-back)
    res_clean = mm.access_page(pid=1, vpn=3, operation="READ")
    assert res_clean.evicted_page == VirtualPage(1, 1)
    assert res_clean.write_back_occurred is False
    assert mm.metrics.successful_writebacks == 0

    # 5. Access page 4 (evicts dirty page 2) -> Dirty eviction (write-back triggered)
    res_dirty = mm.access_page(pid=1, vpn=4, operation="READ")
    assert res_dirty.evicted_page == VirtualPage(1, 2)
    assert res_dirty.write_back_occurred is True
    assert mm.metrics.successful_writebacks == 1

    # Verify backing store persisted the dirty data
    assert bs.get_stored_data(VirtualPage(1, 2)) == "dirty_data_v2"


# =============================================================================
# 3. Failure Handling and Recovery Tests
# =============================================================================

def test_memory_manager_failed_writeback_preserves_victim_state():
    """Verify failed write-back leaves victim resident, dirty, and aborts request cleanly."""
    bs = BackingStore()
    mm = MemoryManager(capacity=1, algorithm="LRU", backing_store=bs)

    # Populate single frame with dirty page
    mm.access_page(pid=1, vpn=100, operation="WRITE", write_data="vital_data")
    victim_frame = mm.get_frame(0)
    assert victim_frame.dirty is True
    assert victim_frame.vpage == VirtualPage(1, 100)

    # Inject simulated disk write failure
    bs.fail_next_write()

    # Request new page 200 (requires evicting dirty page 100)
    with pytest.raises(DiskWriteError):
        mm.access_page(pid=1, vpn=200, operation="READ")

    # Invariants after failed write-back:
    # 1. Victim page remains resident in Frame 0
    assert victim_frame.vpage == VirtualPage(1, 100)
    # 2. Dirty bit remains True
    assert victim_frame.dirty is True
    # 3. PageTableEntry for victim remains valid
    assert mm.get_page_table(1).is_valid(100) is True
    # 4. Incoming page 200 was NOT mapped
    assert mm.get_page_table(1).is_valid(200) is False
    # 5. Telemetry records failure
    assert mm.metrics.failed_pagefaults == 1
    assert mm.metrics.successful_writebacks == 0
    assert mm.metrics.successful_evictions == 0


def test_memory_manager_writeback_success_pagein_failure_consistency():
    """Verify that if write-back succeeds but page-in fails, victim remains resident and clean."""
    bs = BackingStore()
    mm = MemoryManager(capacity=1, algorithm="LRU", backing_store=bs)

    # Populate single frame with dirty page
    mm.access_page(pid=1, vpn=100, operation="WRITE", write_data="persisted_payload")
    victim_frame = mm.get_frame(0)
    assert victim_frame.dirty is True

    # Inject page-in failure (write-back will succeed, but read will fail)
    bs.fail_next_read()

    with pytest.raises(DiskReadError):
        mm.access_page(pid=1, vpn=200, operation="READ")

    # Invariants:
    # 1. Write-back succeeded: dirty bit is cleared to False
    assert victim_frame.dirty is False
    assert mm.metrics.successful_writebacks == 1
    assert bs.get_stored_data(VirtualPage(1, 100)) == "persisted_payload"

    # 2. Because page-in failed, incoming page 200 is discarded
    assert mm.get_page_table(1).is_valid(200) is False

    # 3. Victim page 100 remains resident and valid
    assert victim_frame.vpage == VirtualPage(1, 100)
    assert mm.get_page_table(1).is_valid(100) is True
    assert mm.metrics.failed_pagefaults == 1
    assert mm.metrics.successful_evictions == 0


def test_memory_manager_reference_and_dirty_bit_updates():
    """Verify READ sets reference bit, WRITE sets reference and dirty bits."""
    mm = MemoryManager(capacity=2, algorithm="LRU")

    # READ access: reference=True, dirty=False
    mm.access_page(pid=1, vpn=1, operation="READ")
    f0 = mm.get_frame(0)
    assert f0.reference_bit is True
    assert f0.dirty is False

    # Clear reference bit
    mm.set_frame_reference_bit(0, False)
    assert f0.reference_bit is False

    # WRITE hit: reference=True, dirty=True
    mm.access_page(pid=1, vpn=1, operation="WRITE")
    assert f0.reference_bit is True
    assert f0.dirty is True


def test_memory_manager_transaction_guard_and_panic_recovery():
    """Verify transaction re-entrancy rejection and panic recovery via coordinated reset."""
    mm = MemoryManager(capacity=2, algorithm="LRU")

    # Test re-entrancy / concurrent mutation rejection
    mm._in_transaction = True
    try:
        with pytest.raises(ConcurrentTransactionError):
            mm.set_frame_reference_bit(0, True)
        with pytest.raises(ConcurrentTransactionError):
            mm.set_frame_dirty(0, True)
        with pytest.raises(ConcurrentTransactionError):
            mm.access_page(pid=1, vpn=1)
    finally:
        mm._in_transaction = False

    # Simulate unexpected corruption in Phase 3
    mm.is_consistent = False
    with pytest.raises(CorruptedManagerStateError):
        mm.access_page(pid=1, vpn=1)

    # Coordinated reset restores consistency
    mm.reset()
    assert mm.is_consistent is True
    res = mm.access_page(pid=1, vpn=1)
    assert res.hit is False


def test_memory_manager_pagetable_and_allocator_consistency():
    """Verify continuous invariant parity between page tables, frames, and allocator."""
    mm = MemoryManager(capacity=3, algorithm="LRU")

    # Mixed access workload
    refs = [(1, 0), (1, 1), (1, 2), (1, 0), (1, 3), (1, 1), (1, 4)]
    for pid, vpn in refs:
        mm.access_page(pid=pid, vpn=vpn)

    # Check structural consistency
    resident_in_frames = [f.vpage for f in mm.frames.values() if f.is_allocated]
    assert len(resident_in_frames) == 3

    resident_in_pt = [
        VirtualPage(1, vpn) for vpn in mm.get_page_table(1).get_resident_pages()
    ]
    assert sorted(resident_in_frames) == sorted(resident_in_pt)
    assert mm.allocator.allocated_count == len(resident_in_frames)
    assert mm.allocator.free_count == 0


def test_memory_manager_reset_failure_latches_inconsistent():
    """Verify that a failure midway through reset marks manager inconsistent and blocks operations."""
    mm = MemoryManager(capacity=3, algorithm="LRU")
    mm.access_page(pid=1, vpn=10)
    mm.access_page(pid=1, vpn=20)
    assert mm.is_consistent is True

    # Inject failure midway through reset in policy subsystem
    with patch.object(mm.policy, "reset", side_effect=RuntimeError("Subsystem reset error")):
        with pytest.raises(RuntimeError) as exc_info:
            mm.reset()
        assert "Subsystem reset error" in str(exc_info.value)

    # Manager must be marked inconsistent
    assert mm.is_consistent is False

    # Subsequent memory accesses must be blocked by CorruptedManagerStateError
    with pytest.raises(CorruptedManagerStateError) as exc_blocked:
        mm.access_page(pid=1, vpn=30)
    assert "inconsistent panic state" in str(exc_blocked.value)

    # Subsequent successful reset restores consistency and normal operation
    mm.reset()
    assert mm.is_consistent is True
    res = mm.access_page(pid=1, vpn=30)
    assert res.hit is False
    assert res.frame_id == 0


def test_memory_manager_reset_failure_with_clock_policy():
    """Verify reset failure latching and recovery when integrated with CLOCKCache."""
    mm = MemoryManager(capacity=3, algorithm="CLOCK")
    mm.access_page(pid=1, vpn=1)
    mm.access_page(pid=1, vpn=2)
    assert mm.is_consistent is True

    # Inject failure during frame allocator reset
    with patch.object(mm.allocator, "reset", side_effect=ValueError("Allocator reset error")):
        with pytest.raises(ValueError) as exc_info:
            mm.reset()
        assert "Allocator reset error" in str(exc_info.value)

    # Inconsistent state must be latched
    assert mm.is_consistent is False

    # Accesses blocked
    with pytest.raises(CorruptedManagerStateError):
        mm.access_page(pid=1, vpn=3)

    # Recovery via clean reset
    mm.reset()
    assert mm.is_consistent is True
    assert mm.policy.policy._is_integrated is True
    res = mm.access_page(pid=1, vpn=3)
    assert res.hit is False


