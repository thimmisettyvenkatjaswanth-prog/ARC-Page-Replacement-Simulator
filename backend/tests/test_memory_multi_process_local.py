"""
Unit and integration tests for Stage 12.2 Phase 4C Substage 4C.5:
Local Replacement Engine, Independent Policy Adapters, Local Decision Matrix (S-1 to S-13),
Local Capacity Sizing, Local OPT Sequence Filtering, and Zero Cross-Process Evictions.
"""
from typing import List
from unittest.mock import MagicMock
import pytest

from backend.algorithms.arc import ARCCache
from backend.algorithms.clock import CLOCKCache
from backend.algorithms.fifo import FIFOCache
from backend.algorithms.lfu import LFUCache
from backend.algorithms.lru import LRUCache
from backend.algorithms.mru import MRUCache
from backend.algorithms.opt import OptimalCache
from backend.memory.backing_store import BackingStore
from backend.memory.exceptions import (
    CorruptedManagerStateError,
    DiskReadError,
    DiskWriteError,
    InvalidProcessStateError,
    NoFreeFrameError,
    UnsupportedOperationError,
)
from backend.memory.multi_process_manager import (
    MultiProcessMemoryManager,
    ReplacementScope,
)
from backend.memory.page import VirtualPage
from backend.memory.process import (
    ProcessQuota,
    ProcessState,
)


# =============================================================================
# 1. Independent Local Policy State & Capacity Sizing
# =============================================================================

def test_local_independent_policy_state_and_sizing():
    """
    Verify Approach A:
    1. In LOCAL replacement mode, each process owns an independent PolicyAdapter.
    2. Local capacity is sized according to:
       min(pcb.quota.max_frames, physical_capacity) if finite, else physical_capacity.
    """
    mm = MultiProcessMemoryManager(capacity=4, algorithm="LRU", scope=ReplacementScope.LOCAL)

    # Process 1 with finite max_frames = 2 -> local capacity = min(2, 4) = 2
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1, max_frames=2))
    assert p1.local_policy is not None
    assert p1.local_policy.capacity == 2

    # Process 2 with unlimited max_frames (None) -> local capacity = physical capacity = 4
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1))
    assert p2.local_policy is not None
    assert p2.local_policy.capacity == 4

    # Process 3 with max_frames > physical capacity -> local capacity clamped to physical capacity = 4
    p3 = mm.create_process(pid=3, quota=ProcessQuota(min_frames=1, max_frames=10))
    assert p3.local_policy is not None
    assert p3.local_policy.capacity == 4

    # The policies are completely independent objects
    assert p1.local_policy is not p2.local_policy
    assert p2.local_policy is not p3.local_policy
    assert p1.local_policy is not mm.policy


# =============================================================================
# 2. Local Replacement for All Seven Algorithms
# =============================================================================

def test_local_lru_replacement():
    """Verify Local LRU replacement: victim is strictly requester's own LRU page."""
    mm = MultiProcessMemoryManager(capacity=4, algorithm="LRU", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1, max_frames=2))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1, max_frames=2))

    # P1 fills its 2 frames: (1, 0) then (1, 1)
    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(1, 1))

    # P2 fills its 2 frames: (2, 0) then (2, 1)
    mm.access_page(VirtualPage(2, 0))
    mm.access_page(VirtualPage(2, 1))

    # All 4 physical frames are allocated (2 to P1, 2 to P2)
    assert mm.allocator.free_count == 0

    # P1 hits on (1, 0): promotes (1, 0) to MRU. LRU of P1 is now (1, 1)
    r_hit = mm.access_page(VirtualPage(1, 0))
    assert r_hit.hit is True

    # P1 accesses (1, 2): P1 must self-evict its own LRU page (1, 1)
    r_evict = mm.access_page(VirtualPage(1, 2))
    assert r_evict.hit is False
    assert r_evict.evicted_page == VirtualPage(1, 1)
    assert r_evict.victim_process_id == 1

    # P2 resident pages and frames are completely untouched!
    assert p2.resident_count == 2
    assert mm.get_page_table(2).is_valid(0) is True
    assert mm.get_page_table(2).is_valid(1) is True
    assert mm.metrics.cross_process_evictions == 0
    assert mm.metrics.self_evictions == 1


def test_local_fifo_replacement():
    """Verify Local FIFO replacement: victim is strictly requester's oldest page."""
    mm = MultiProcessMemoryManager(capacity=4, algorithm="FIFO", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1, max_frames=2))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1, max_frames=2))

    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(1, 1))
    mm.access_page(VirtualPage(2, 0))
    mm.access_page(VirtualPage(2, 1))

    # In FIFO, hitting on (1, 0) does NOT alter arrival order
    mm.access_page(VirtualPage(1, 0))

    # P1 accesses (1, 2) -> FIFO evicts oldest page (1, 0)
    res = mm.access_page(VirtualPage(1, 2))
    assert res.evicted_page == VirtualPage(1, 0)
    assert p2.resident_count == 2
    assert mm.metrics.cross_process_evictions == 0


def test_local_lfu_replacement():
    """Verify Local LFU replacement: victim is strictly requester's least frequent page."""
    mm = MultiProcessMemoryManager(capacity=4, algorithm="LFU", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1, max_frames=2))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1, max_frames=2))

    mm.access_page(VirtualPage(1, 0))  # freq=1
    mm.access_page(VirtualPage(1, 0))  # freq=2
    mm.access_page(VirtualPage(1, 1))  # freq=1
    mm.access_page(VirtualPage(2, 0))
    mm.access_page(VirtualPage(2, 1))

    # P1 accesses (1, 2) -> LFU evicts (1, 1) with frequency 1
    res = mm.access_page(VirtualPage(1, 2))
    assert res.evicted_page == VirtualPage(1, 1)
    assert p2.resident_count == 2
    assert mm.metrics.cross_process_evictions == 0


def test_local_mru_replacement():
    """Verify Local MRU replacement: victim is strictly requester's most recently used page."""
    mm = MultiProcessMemoryManager(capacity=4, algorithm="MRU", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1, max_frames=2))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1, max_frames=2))

    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(1, 1))  # MRU for P1
    mm.access_page(VirtualPage(2, 0))
    mm.access_page(VirtualPage(2, 1))

    # P1 accesses (1, 2) -> MRU evicts (1, 1)
    res = mm.access_page(VirtualPage(1, 2))
    assert res.evicted_page == VirtualPage(1, 1)
    assert p2.resident_count == 2
    assert mm.metrics.cross_process_evictions == 0


def test_local_clock_replacement():
    """Verify Local CLOCK replacement: sweeps only requester's frames, preserving foreign frames."""
    mm = MultiProcessMemoryManager(capacity=4, algorithm="CLOCK", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1, max_frames=2))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1, max_frames=2))

    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(1, 1))
    mm.access_page(VirtualPage(2, 0))
    mm.access_page(VirtualPage(2, 1))

    # Frames 0, 1 belong to P1; Frames 2, 3 belong to P2
    p2_frame0 = mm.get_page_table(2).get_frame_id(0)
    p2_frame1 = mm.get_page_table(2).get_frame_id(1)

    # P1 accesses (1, 2) -> must evict one of P1's frames
    res = mm.access_page(VirtualPage(1, 2))
    assert res.evicted_page.pid == 1
    assert p2.resident_count == 2
    assert mm.frames[p2_frame0].owner_pid == 2
    assert mm.frames[p2_frame1].owner_pid == 2
    assert mm.metrics.cross_process_evictions == 0


def test_local_arc_replacement_and_invariants():
    """
    Verify Local ARC replacement:
    1. Independent ARC instance with local capacity = min(max_frames, physical_capacity).
    2. Enforces paper invariants and ghost list management within local address space.
    3. Never evicts foreign process pages.
    """
    mm = MultiProcessMemoryManager(capacity=4, algorithm="ARC", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1, max_frames=2))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1, max_frames=2))

    arc1: ARCCache = p1.local_policy.policy
    arc2: ARCCache = p2.local_policy.policy
    assert arc1.capacity == 2
    assert arc2.capacity == 2

    # P1: access (1, 0) WRITE -> enters T1
    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="P1_D0")
    # P1: access (1, 0) READ -> hit, moves to T2
    mm.access_page(VirtualPage(1, 0), operation="READ")
    # P1: access (1, 1) READ -> enters T1
    mm.access_page(VirtualPage(1, 1), operation="READ")

    assert VirtualPage(1, 0) in arc1.T2
    assert VirtualPage(1, 1) in arc1.T1
    assert p1.resident_count == 2

    # P2 accesses its own pages
    mm.access_page(VirtualPage(2, 0), operation="READ")
    mm.access_page(VirtualPage(2, 1), operation="READ")

    # P1 accesses (1, 2) -> local ARC capacity is full (|T1| + |T2| == 2)
    # REPLACE evicts T1 LRU: (1, 1) into B1
    res = mm.access_page(VirtualPage(1, 2), operation="READ")
    assert res.evicted_page == VirtualPage(1, 1)
    assert res.victim_process_id == 1
    assert VirtualPage(1, 1) in arc1.B1

    # Invariants on both local ARC instances hold
    arc1._verify_invariants()
    arc2._verify_invariants()
    assert mm.metrics.cross_process_evictions == 0


def test_local_opt_sequence_filtering_and_validation():
    """
    Verify Local OPT:
    1. Global sequence is filtered per-process: S_p = [V in S_global if V.pid == p].
    2. Advances only upon successful commit.
    3. Rejects references out of sequence.
    """
    seq = [
        VirtualPage(1, 0),
        VirtualPage(2, 0),
        VirtualPage(1, 1),
        VirtualPage(2, 1),
        VirtualPage(1, 2),
    ]
    mm = MultiProcessMemoryManager(
        capacity=4, algorithm="OPT", reference_sequence=seq, scope=ReplacementScope.LOCAL
    )
    p1 = mm.create_process(pid=1, quota=ProcessQuota(max_frames=2))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(max_frames=2))

    opt1: OptimalCache = p1.local_policy.policy
    opt2: OptimalCache = p2.local_policy.policy

    # Filtered local sequences
    assert opt1.sequence == [VirtualPage(1, 0), VirtualPage(1, 1), VirtualPage(1, 2)]
    assert opt2.sequence == [VirtualPage(2, 0), VirtualPage(2, 1)]

    # P1 access (1, 0)
    mm.access_page(VirtualPage(1, 0))
    assert opt1.current_step == 1
    assert opt2.current_step == 0

    # P2 access (2, 0)
    mm.access_page(VirtualPage(2, 0))
    assert opt1.current_step == 1
    assert opt2.current_step == 1

    # P1 attempting out of order access raises ValueError (sequence mismatch)
    with pytest.raises(ValueError, match="Sequence mismatch"):
        mm.access_page(VirtualPage(1, 2))  # Expected (1, 1) next
    assert opt1.current_step == 1  # Unchanged

    # P1 valid access (1, 1)
    mm.access_page(VirtualPage(1, 1))
    assert opt1.current_step == 2


# =============================================================================
# 3. Decision Matrix & Quota Scenarios in Local Scope
# =============================================================================

def test_local_max_quota_eviction_when_free_frames_remain():
    """
    Verify Scenario S-3 in Local Mode:
    When requester reaches max_frames, it MUST evict one of its own pages,
    even when the physical allocator still has free frames.
    """
    mm = MultiProcessMemoryManager(capacity=4, algorithm="LRU", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1, max_frames=2))

    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(1, 1))
    assert p1.resident_count == 2
    assert mm.allocator.free_count == 2  # Free frames exist!

    res = mm.access_page(VirtualPage(1, 2))
    assert res.evicted_page == VirtualPage(1, 0)
    assert res.eviction_trigger == "PRIMARY_QUOTA_CEILING"
    assert mm.allocator.free_count == 2  # Free frames strictly preserved!
    assert mm.metrics.quota_ceiling_evictions == 1
    assert mm.metrics.self_evictions == 1


def test_local_out_of_memory_s12_raises_no_free_frame():
    """
    Verify Scenario S-12 (Local Out of Memory):
    Physical memory is exhausted, requester has 0 resident pages (R_P == 0),
    and cannot evict foreign frames in Local Mode -> raises NoFreeFrameError with zero mutation.
    """
    mm = MultiProcessMemoryManager(capacity=2, algorithm="LRU", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1)
    p2 = mm.create_process(pid=2)

    # P1 fills all physical memory
    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(1, 1))
    assert mm.allocator.has_free_frame() is False

    # P2 faults on (2, 0). Memory is full, and P2 has 0 pages to evict.
    with pytest.raises(NoFreeFrameError) as exc_info:
        mm.access_page(VirtualPage(2, 0))
    assert "No eligible victim page available" in str(exc_info.value)

    # Invariants preserved: zero mutation
    assert p2.resident_count == 0
    assert p1.resident_count == 2
    assert mm.get_page_table(2).is_valid(0) is False
    assert mm.is_consistent is True


def test_local_mode_never_evicts_foreign_page():
    """
    Verify absolute isolation:
    Even under high memory contention across multiple processes,
    cross_process_evictions is strictly ZERO in Local mode.
    """
    mm = MultiProcessMemoryManager(capacity=3, algorithm="LRU", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1, quota=ProcessQuota(max_frames=1))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(max_frames=1))
    p3 = mm.create_process(pid=3, quota=ProcessQuota(max_frames=1))

    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(2, 0))
    mm.access_page(VirtualPage(3, 0))

    # All 3 held by distinct processes
    assert mm.allocator.free_count == 0

    # P1 accesses 5 more pages (forces 5 self-evictions)
    for vpn in range(1, 6):
        res = mm.access_page(VirtualPage(1, vpn))
        assert res.victim_process_id == 1

    # P2 and P3 are completely untouched!
    assert p2.resident_count == 1
    assert p3.resident_count == 1
    assert mm.get_page_table(2).is_valid(0) is True
    assert mm.get_page_table(3).is_valid(0) is True
    assert mm.metrics.cross_process_evictions == 0
    assert mm.metrics.self_evictions == 5


# =============================================================================
# 4. Process Teardown, Reset, and Error Handling
# =============================================================================

def test_local_process_termination_and_policy_cleanup():
    """Verify process termination in local mode flushes dirty frames and resets local policy."""
    mm = MultiProcessMemoryManager(capacity=4, algorithm="LRU", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1)
    p2 = mm.create_process(pid=2)

    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="DIRTY_P1")
    mm.access_page(VirtualPage(1, 1), operation="READ")
    mm.access_page(VirtualPage(2, 0), operation="READ")

    p1_policy = p1.local_policy
    assert p1_policy.contains(VirtualPage(1, 0)) is True

    mm.terminate_process(1, flush_dirty=True)

    # 1. State and resident count
    assert p1.state == ProcessState.TERMINATED
    assert p1.resident_count == 0

    # 2. Backing store has dirty data
    assert mm.backing_store.read_page(VirtualPage(1, 0)) == "DIRTY_P1"

    # 3. Frames freed
    assert mm.allocator.free_count == 3

    # 4. Local policy emptied
    assert p1_policy.contains(VirtualPage(1, 0)) is False
    assert p1_policy.contains(VirtualPage(1, 1)) is False

    # 5. P2 untouched
    assert p2.resident_count == 1
    assert mm.get_page_table(2).is_valid(0) is True


def test_local_termination_failed_write_back_retry():
    """Verify termination write-back failure transitions to TERMINATION_FAILED, retryable."""
    mm = MultiProcessMemoryManager(capacity=3, algorithm="LRU", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1)

    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="D1")

    faulty_store = MagicMock()
    faulty_store.write_page.side_effect = DiskWriteError("I/O write crash")
    mm.backing_store = faulty_store

    with pytest.raises(DiskWriteError):
        mm.terminate_process(1, flush_dirty=True)
    assert p1.state == ProcessState.TERMINATION_FAILED
    assert p1.resident_count == 1  # Intact

    # Retry with good store
    mm.backing_store = BackingStore()
    mm.terminate_process(1, flush_dirty=True)
    assert p1.state == ProcessState.TERMINATED
    assert p1.resident_count == 0


def test_local_page_in_and_write_back_atomicity():
    """Verify failure atomicity: failed page-in in local mode clears dirty bit on victim and leaves it resident."""
    real_store = BackingStore()
    mm = MultiProcessMemoryManager(
        capacity=2, algorithm="LRU", backing_store=real_store, scope=ReplacementScope.LOCAL
    )
    p1 = mm.create_process(pid=1, quota=ProcessQuota(max_frames=2))

    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="DIRTY_DATA")
    mm.access_page(VirtualPage(1, 1), operation="READ")

    mock_store = MagicMock()
    mock_store.write_page.side_effect = real_store.write_page
    mock_store.read_page.side_effect = DiskReadError("Simulated head crash")
    mm.backing_store = mock_store

    with pytest.raises(DiskReadError):
        mm.access_page(VirtualPage(1, 2))

    # Victim (1, 0) remains resident and its dirty bit was cleared
    assert mm.get_page_table(1).is_valid(0) is True
    assert mm.frames[0].dirty is False
    assert real_store.read_page(VirtualPage(1, 0)) == "DIRTY_DATA"


def test_local_manager_reset_and_policy_reset():
    """Verify manager reset in local mode resets frames, allocator, and all local policies."""
    mm = MultiProcessMemoryManager(capacity=3, algorithm="LRU", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1)
    p2 = mm.create_process(pid=2)

    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(2, 0))

    mm.reset()

    assert mm.allocator.free_count == 3
    assert mm.process_table.count == 0
    assert len(mm.page_tables) == 0
    assert mm.is_consistent is True


def test_local_metrics_reconciliation_zero_cross_process():
    """Verify metrics reconciliation identities hold in local mode with cross_process_evictions == 0."""
    mm = MultiProcessMemoryManager(capacity=3, algorithm="LRU", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1, quota=ProcessQuota(max_frames=1))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(max_frames=1))

    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="W1")
    mm.access_page(VirtualPage(2, 0), operation="READ")

    # P1 ceiling eviction (S-3)
    mm.access_page(VirtualPage(1, 1), operation="READ")

    m = mm.metrics
    assert m.successful_evictions == m.quota_ceiling_evictions + m.directory_mandated_evictions + m.physical_exhaustion_evictions
    assert m.successful_evictions == m.clean_evictions + m.dirty_evictions
    assert m.successful_evictions == m.self_evictions
    assert m.cross_process_evictions == 0


def test_scope_switching_prohibited_without_reset():
    """Verify live mode switching without reset is strictly prohibited."""
    mm = MultiProcessMemoryManager(capacity=3, algorithm="LRU", scope=ReplacementScope.LOCAL)
    p1 = mm.create_process(pid=1)
    mm.access_page(VirtualPage(1, 0))

    # Live switch rejected
    with pytest.raises(UnsupportedOperationError) as exc_info:
        mm.set_scope(ReplacementScope.GLOBAL)
    assert "Coordinated reset() is required" in str(exc_info.value)

    # After reset, switch succeeds
    mm.reset()
    mm.set_scope(ReplacementScope.GLOBAL)
    assert mm.scope == ReplacementScope.GLOBAL


def test_opt_unregistered_pid_rejected_before_execution():
    """
    Verify OPT upfront registration contract:
    All PIDs appearing in the OPT trace must be registered in ProcessTable before the first request.
    """
    seq = [VirtualPage(1, 0), VirtualPage(2, 0)]
    mm = MultiProcessMemoryManager(
        capacity=3, algorithm="OPT", reference_sequence=seq, scope=ReplacementScope.LOCAL
    )
    mm.create_process(pid=1)
    # PID 2 is NOT yet registered

    with pytest.raises(UnsupportedOperationError) as exc_info:
        mm.access_page(VirtualPage(1, 0))
    assert "is not registered in ProcessTable" in str(exc_info.value)

    # Register PID 2 and now access succeeds
    mm.create_process(pid=2)
    res = mm.access_page(VirtualPage(1, 0))
    assert res.hit is False

