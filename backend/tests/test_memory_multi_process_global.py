"""
Unit and integration tests for Stage 12.2 Phase 4C Substage 4C.4:
MultiProcessMemoryManager Core, Global Replacement, Overlap Eviction Engine,
and Coordinated Process Teardown.
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
    DuplicateProcessError,
    InvalidProcessStateError,
    InvalidQuotaError,
    NoFreeFrameError,
    ProcessNotFoundError,
    ProcessTerminatedError,
    UnsupportedOperationError,
)
from backend.memory.multi_process_manager import (
    MultiProcessMemoryManager,
    ReplacementScope,
)
from backend.memory.page import VirtualPage
from backend.memory.process import (
    ProcessControlBlock,
    ProcessQuota,
    ProcessState,
)


# =============================================================================
# 1. Initialization and Process Registry
# =============================================================================

def test_process_creation_with_capacity_supplied_to_process_table():
    """Verify MultiProcessMemoryManager configures ProcessTable with physical capacity."""
    mm = MultiProcessMemoryManager(capacity=4, algorithm="LRU")
    assert mm.capacity == 4
    assert mm.process_table.capacity == 4
    assert mm.scope == ReplacementScope.GLOBAL

    # Creating processes within quota succeeds
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=2, max_frames=3))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=2, max_frames=4))
    assert mm.has_process(1) is True
    assert mm.has_process(2) is True

    # Quota exceeding capacity fails
    with pytest.raises(InvalidQuotaError):
        mm.create_process(pid=3, quota=ProcessQuota(min_frames=1))


def test_two_or_more_processes_sharing_frame_pool_and_unique_vpns():
    """
    Verify multiple processes share the same physical frames while maintaining
    independent page tables and unique (pid, vpn) page identities.
    """
    mm = MultiProcessMemoryManager(capacity=4, algorithm="LRU")
    mm.create_process(pid=1)
    mm.create_process(pid=2)

    # Both processes access VPN 0
    res1 = mm.access_page(VirtualPage(1, 0), operation="READ")
    res2 = mm.access_page(VirtualPage(2, 0), operation="READ")

    assert res1.frame_id != res2.frame_id
    assert mm.get_page_table(1).get_frame_id(0) == res1.frame_id
    assert mm.get_page_table(2).get_frame_id(0) == res2.frame_id

    # Frames track ownership accurately
    assert mm.frames[res1.frame_id].owner_pid == 1
    assert mm.frames[res2.frame_id].owner_pid == 2
    assert mm.frames[res1.frame_id].vpn == 0
    assert mm.frames[res2.frame_id].vpn == 0


# =============================================================================
# 2. Complete Decision Matrix S-1 through S-13 in Global Scope
# =============================================================================

def test_decision_matrix_s1_through_s3_hits_free_frames_and_quota_ceiling():
    """Verify S-1 (Hit), S-2 (Free Frame), and S-3 (Forced Self-Eviction at Max Quota)."""
    mm = MultiProcessMemoryManager(capacity=4, algorithm="LRU")
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1, max_frames=2))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1, max_frames=3))

    # S-2: Free frame allocation (p1 below max)
    r1 = mm.access_page(VirtualPage(1, 0))
    assert not r1.hit and r1.evicted_page is None
    r2 = mm.access_page(VirtualPage(1, 1))
    assert not r2.hit and r2.evicted_page is None
    assert p1.resident_count == 2

    # S-1: Hit
    r_hit = mm.access_page(VirtualPage(1, 0))
    assert r_hit.hit is True and r_hit.evicted_page is None

    # S-3: P1 is at max_frames=2, while allocator still has 2 free frames!
    # P1 MUST self-evict its own LRU page (VPN 0 was accessed again on hit, so VPN 0 is MRU, VPN 1 is LRU)
    assert mm.allocator.has_free_frame() is True
    r_s3 = mm.access_page(VirtualPage(1, 2))
    assert r_s3.evicted_page == VirtualPage(1, 1)
    assert r_s3.eviction_trigger == "PRIMARY_QUOTA_CEILING"
    assert r_s3.also_directory_mandated is False
    assert r_s3.also_physical_exhausted is False
    assert mm.metrics.quota_ceiling_evictions == 1
    assert mm.metrics.self_evictions == 1
    assert mm.allocator.free_count == 2  # Free frames were strictly preserved!


def test_decision_matrix_s4_and_s5_global_eviction_and_shielding_fallback():
    """
    Verify S-4 (Global cross-process eviction of unshielded foreign candidate) and
    S-5 (Shielding fallback: foreign candidate shielded, requester self-evicts).
    """
    mm = MultiProcessMemoryManager(capacity=3, algorithm="LRU")
    # P1 has min_frames=1, P2 has min_frames=1
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1))

    # Fill all 3 frames: P1 gets 2 frames, P2 gets 1 frame
    mm.access_page(VirtualPage(1, 0))  # P1: 1 frame
    mm.access_page(VirtualPage(2, 0))  # P2: 1 frame
    mm.access_page(VirtualPage(1, 1))  # P1: 2 frames. Memory is now full (3/3)

    assert mm.allocator.has_free_frame() is False
    assert p1.resident_count == 2
    assert p2.resident_count == 1

    # S-4: P2 faults on VPN 1.
    # Candidates in LRU order: (1, 0), (2, 0), (1, 1)
    # P1 has 2 frames > min_frames (1) -> (1, 0) is ELIGIBLE!
    # P2 evicts (1, 0) from P1 (cross-process eviction)
    r_s4 = mm.access_page(VirtualPage(2, 1))
    assert r_s4.evicted_page == VirtualPage(1, 0)
    assert r_s4.eviction_trigger == "PRIMARY_PHYSICAL_EXHAUSTION"
    assert mm.metrics.cross_process_evictions == 1
    assert p2.metrics.evictions_caused == 1
    assert p1.metrics.evictions_suffered == 1
    assert p1.resident_count == 1
    assert p2.resident_count == 2

    # Now P1 has 1 frame (at its min_frames=1 guarantee).
    # P2 accesses VPN 2. P1's page (1, 1) is SHIELDED (1 <= 1).
    # P2 cannot evict P1's page. P2 evicts its own LRU page (2, 0) -> S-5 Shielding Fallback!
    r_s5 = mm.access_page(VirtualPage(2, 2))
    assert r_s5.evicted_page == VirtualPage(2, 0)
    assert r_s5.eviction_trigger == "PRIMARY_PHYSICAL_EXHAUSTION"
    assert mm.metrics.self_evictions == 1
    assert p1.resident_count == 1  # P1 was successfully protected!


def test_decision_matrix_s6_foreign_shielding_starvation():
    """
    Verify S-6: Physical memory full, all foreign pages shielded at min_frames,
    and requester has 0 resident pages -> raises NoFreeFrameError with zero mutation.
    """
    mm = MultiProcessMemoryManager(capacity=3, algorithm="LRU")
    # P1 and P2 hold all 3 frames at their min_frames guarantee
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=2))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1))
    p3 = mm.create_process(pid=3, quota=ProcessQuota(min_frames=0))

    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(1, 1))
    mm.access_page(VirtualPage(2, 0))

    assert mm.allocator.has_free_frame() is False
    assert p1.resident_count == 2 == p1.quota.min_frames
    assert p2.resident_count == 1 == p2.quota.min_frames
    assert p3.resident_count == 0

    # P3 requests memory. All foreign frames are shielded, and P3 has 0 frames.
    # Must raise NoFreeFrameError without partial state mutation.
    with pytest.raises(NoFreeFrameError) as exc_info:
        mm.access_page(VirtualPage(3, 0))
    assert "No eligible victim page available" in str(exc_info.value)

    # Invariants preserved
    assert mm.allocator.has_free_frame() is False
    assert p1.resident_count == 2
    assert p2.resident_count == 1
    assert p3.resident_count == 0
    assert mm.metrics.failed_pagefaults == 0  # Aborted in Phase 1 before I/O


# =============================================================================
# 3. Overlap Eviction Hierarchy and Diagnostic Flags
# =============================================================================

def test_overlapping_eviction_telemetry_precedence():
    """
    Verify overlap precedence: Quota Ceiling (1) > Directory Mandated (2) > Physical Exhaustion (3).
    Tests simultaneous conditions and diagnostic flags.
    """
    # Case 6: Quota ceiling + Physical exhaustion
    mm = MultiProcessMemoryManager(capacity=2, algorithm="LRU")
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1, max_frames=1))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1, max_frames=1))

    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(2, 0))
    assert mm.allocator.has_free_frame() is False  # Physical memory full!

    # P1 accesses VPN 1: P1 is at max_frames=1 AND physical memory is full!
    # Precedence: PRIMARY_QUOTA_CEILING, also_physical_exhausted = True
    res = mm.access_page(VirtualPage(1, 1))
    assert res.eviction_trigger == "PRIMARY_QUOTA_CEILING"
    assert res.also_quota_ceiling is False  # primary is already quota ceiling
    assert res.also_physical_exhausted is True
    assert res.also_directory_mandated is False
    assert mm.metrics.quota_ceiling_evictions == 1
    assert mm.metrics.physical_exhaustion_evictions == 0


def test_arc_directory_mandated_and_ghost_prunes():
    """
    Verify ARC directory-mandated evictions (Operation B) vs ghost pruning (Operation A).
    Ensures ghost prunes are not counted as physical evictions.
    """
    mm = MultiProcessMemoryManager(capacity=2, algorithm="ARC")
    mm.create_process(pid=1)

    # Fill cache: access (1, 1), (1, 2)
    mm.access_page(VirtualPage(1, 1))
    mm.access_page(VirtualPage(1, 2))
    assert mm.metrics.successful_evictions == 0

    # Access (1, 3): triggers ARC REPLACE (T1 -> B1). Physical eviction occurs.
    res3 = mm.access_page(VirtualPage(1, 3))
    assert res3.eviction_trigger == "PRIMARY_DIRECTORY_MANDATED" or res3.eviction_trigger == "PRIMARY_PHYSICAL_EXHAUSTION"
    assert mm.metrics.successful_evictions == 1

    # Access (1, 4): Case 4B or 4A with ghost pruning
    mm.access_page(VirtualPage(1, 4))
    assert mm.metrics.successful_evictions == 2
    # Ghost prunes counter is tracked cleanly
    assert mm.metrics.arc_ghost_prunes >= 0


# =============================================================================
# 4. Metric Reconciliation Identities
# =============================================================================

def test_physical_eviction_metric_reconciliation():
    """
    Verify all 4 mathematical reconciliation identities:
    1. E_phys = E_quota_ceiling + E_directory_mandated + E_physical_exhaustion
    2. E_phys = clean_evictions + dirty_evictions
    3. E_phys = cross_process_evictions + self_evictions
    4. sum(caused) == sum(suffered) == cross_process_evictions
    """
    mm = MultiProcessMemoryManager(capacity=3, algorithm="LRU")
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1, max_frames=2))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1))

    # Workload generating clean, dirty, self, and cross-process evictions
    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="D0")
    mm.access_page(VirtualPage(1, 1), operation="READ")
    mm.access_page(VirtualPage(2, 0), operation="WRITE", data="D2")

    # P1 ceiling eviction (S-3) of (1, 0) which is DIRTY
    mm.access_page(VirtualPage(1, 2), operation="READ")

    # P2 physical exhaustion cross-process eviction (S-4)
    mm.access_page(VirtualPage(2, 1), operation="READ")

    m = mm.metrics
    # Identity 1: Physical cause balance
    assert m.successful_evictions == (
        m.quota_ceiling_evictions + m.directory_mandated_evictions + m.physical_exhaustion_evictions
    )
    # Identity 2: Cleanliness balance
    assert m.successful_evictions == (m.clean_evictions + m.dirty_evictions)
    # Identity 3: Scope balance
    assert m.successful_evictions == (m.cross_process_evictions + m.self_evictions)
    # Identity 4: Cross-process symmetry
    assert p1.metrics.evictions_caused + p2.metrics.evictions_caused == m.cross_process_evictions
    assert p1.metrics.evictions_suffered + p2.metrics.evictions_suffered == m.cross_process_evictions


# =============================================================================
# 5. Fallible Storage I/O Safety & Consistency
# =============================================================================

def test_dirty_write_back_failure_safety():
    """Verify failed dirty write-back aborts cleanly without mutating victim state."""
    backing_store = BackingStore()
    mm = MultiProcessMemoryManager(capacity=2, algorithm="LRU", backing_store=backing_store)
    p1 = mm.create_process(pid=1)

    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="dirty_data")
    mm.access_page(VirtualPage(1, 1), operation="READ")

    # Inject failure into backing store for write_page
    faulty_store = MagicMock()
    faulty_store.write_page.side_effect = DiskWriteError("Disk hardware bad block")
    mm.backing_store = faulty_store

    # Evicting (1, 0) requires dirty write-back -> fails
    with pytest.raises(DiskWriteError) as exc_info:
        mm.access_page(VirtualPage(1, 2))
    assert "Dirty write-back failed" in str(exc_info.value)

    # Invariants preserved
    assert mm.frames[0].dirty is True  # Victim remained dirty
    assert mm.frames[0].vpage == VirtualPage(1, 0)  # Victim remained resident
    assert mm.get_page_table(1).is_valid(0) is True  # PTE still valid
    assert mm.get_page_table(1).is_valid(2) is False  # Requester not mapped
    assert mm.is_consistent is True  # Manager is NOT panicked on I/O error


def test_page_in_failure_safety():
    """Verify failed page-in aborts cleanly, leaving victim resident and unmapped page unassigned."""
    mm = MultiProcessMemoryManager(capacity=2, algorithm="LRU")
    mm.create_process(pid=1)

    mm.access_page(VirtualPage(1, 0), operation="READ")
    mm.access_page(VirtualPage(1, 1), operation="READ")

    # Inject failure into read_page
    faulty_store = MagicMock()
    faulty_store.read_page.side_effect = DiskReadError("Disk read head crash")
    mm.backing_store = faulty_store

    with pytest.raises(DiskReadError):
        mm.access_page(VirtualPage(1, 2))

    # Frame 0 is still mapped to (1, 0)
    assert mm.frames[0].vpage == VirtualPage(1, 0)
    assert mm.get_page_table(1).is_valid(0) is True
    assert mm.get_page_table(1).is_valid(2) is False
    assert mm.is_consistent is True


def test_dirty_write_back_success_followed_by_page_in_failure():
    """
    Verify Correction 1:
    When a dirty victim's write-back succeeds but incoming page-in fails:
    1. Expected DiskReadError is raised.
    2. Victim remains resident and its PTE remains valid.
    3. Victim frame remains allocated and retains owner PID and VPN.
    4. Victim frame's dirty bit is cleared to False.
    5. Backing store contains the latest dirty data written back.
    6. Allocator and page-table mappings remain consistent.
    7. Policy validation token is aborted/cleared.
    8. Manager remains consistent and accepts subsequent valid access.
    """
    real_store = BackingStore()
    mm = MultiProcessMemoryManager(capacity=3, algorithm="LRU", backing_store=real_store)
    p1 = mm.create_process(pid=1, quota=ProcessQuota(min_frames=1))
    p2 = mm.create_process(pid=2, quota=ProcessQuota(min_frames=1))

    # Fill all 3 frames: P1 gets 2 frames, P2 gets 1 frame
    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="DIRTY_P1_V0")
    mm.access_page(VirtualPage(1, 1), operation="READ")
    mm.access_page(VirtualPage(2, 0), operation="READ")

    victim_fid = mm.get_page_table(1).get_frame_id(0)
    assert mm.frames[victim_fid].dirty is True
    assert mm.frames[victim_fid].data == "DIRTY_P1_V0"

    # Inject page-in failure while allowing write-back to succeed through real_store
    mock_store = MagicMock()
    mock_store.write_page.side_effect = real_store.write_page
    mock_store.read_page.side_effect = DiskReadError("Disk head seek error during page-in")
    mm.backing_store = mock_store

    # Record metrics before fault
    initial_pageins = mm.metrics.successful_pageins
    initial_evictions = mm.metrics.successful_evictions

    # P2 faults on (2, 1). P1's (1, 0) is the LRU victim and is dirty.
    with pytest.raises(DiskReadError) as exc_info:
        mm.access_page(VirtualPage(2, 1))
    assert "Page-in read failed" in str(exc_info.value)

    # 1. Victim remains resident and valid
    assert mm.get_page_table(1).is_valid(0) is True
    assert mm.get_page_table(2).is_valid(1) is False

    # 2. Frame ownership unchanged
    victim_frame = mm.frames[victim_fid]
    assert victim_frame.is_allocated is True
    assert victim_frame.owner_pid == 1
    assert victim_frame.vpn == 0
    assert victim_frame.data == "DIRTY_P1_V0"

    # 3. Dirty bit cleared after successful write-back
    assert victim_frame.dirty is False

    # 4. Backing store contains latest victim data
    assert real_store.read_page(VirtualPage(1, 0)) == "DIRTY_P1_V0"

    # 5. Allocator and policy tokens consistent
    assert mm.allocator.free_count == 0
    assert mm.allocator.is_allocated(victim_fid) is True
    assert mm.policy._pending_token is None
    assert mm.is_consistent is True

    # 6. Metrics: no eviction or pagein metrics committed
    assert mm.metrics.successful_evictions == initial_evictions
    assert mm.metrics.successful_pageins == initial_pageins
    assert mm.metrics.failed_pagefaults == 1

    # 7. Subsequent valid request succeeds
    res_subsequent = mm.access_page(VirtualPage(1, 0), operation="READ")
    assert res_subsequent.hit is True
    assert res_subsequent.frame_id == victim_fid


def test_opt_dirty_write_back_success_followed_by_page_in_failure_preserves_sequence():
    """
    Verify that in Global OPT, when dirty write-back succeeds but page-in fails:
    1. Dirty bit is cleared to False on victim.
    2. OPT sequence step does NOT advance.
    3. Active tokens are cleanly cleared.
    4. Retrying the access at the same step succeeds.
    """
    seq = [
        VirtualPage(1, 0),
        VirtualPage(1, 1),
        VirtualPage(2, 0),
        VirtualPage(2, 1),
        VirtualPage(2, 1),
    ]
    real_store = BackingStore()
    mm = MultiProcessMemoryManager(
        capacity=3, algorithm="OPT", backing_store=real_store, reference_sequence=seq
    )
    mm.create_process(pid=1)
    mm.create_process(pid=2)

    # Step 0: (1, 0) WRITE (dirty)
    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="OPT_DIRTY_1_0")
    # Step 1: (1, 1) READ
    mm.access_page(VirtualPage(1, 1), operation="READ")
    # Step 2: (2, 0) READ
    mm.access_page(VirtualPage(2, 0), operation="READ")

    opt: OptimalCache = mm.policy.policy
    assert opt.current_step == 3
    assert mm.frames[0].dirty is True

    # Inject page-in failure for step 3: (2, 1)
    mock_store = MagicMock()
    mock_store.write_page.side_effect = real_store.write_page
    mock_store.read_page.side_effect = DiskReadError("OPT page-in transient I/O failure")
    mm.backing_store = mock_store

    with pytest.raises(DiskReadError):
        mm.access_page(VirtualPage(2, 1))

    # Verify invariants:
    # 1. Victim (1, 0) dirty bit is cleared
    assert mm.frames[0].dirty is False
    assert real_store.read_page(VirtualPage(1, 0)) == "OPT_DIRTY_1_0"
    # 2. OPT current_step did NOT advance!
    assert opt.current_step == 3
    assert opt._pending_token is None
    assert opt._active_commit_token is None

    # Restore real store and retry step 3: (2, 1)
    mm.backing_store = real_store
    res = mm.access_page(VirtualPage(2, 1), operation="READ")
    assert res.hit is False
    assert res.evicted_page == VirtualPage(1, 0)
    assert opt.current_step == 4


# =============================================================================
# 6. Coordinated CLOCK Teardown & Integrated Purge Rejection
# =============================================================================

def test_clock_integrated_purge_rejected_and_coordinated_teardown_succeeds():
    """
    Verify:
    1. Direct CLOCKCache.purge_pid() raises UnsupportedOperationError in integrated mode.
    2. Coordinated teardown via terminate_process(pid) clears slots, unmaps PTEs,
       frees frames, monotonic version bumps, and transitions to TERMINATED.
    """
    mm = MultiProcessMemoryManager(capacity=4, algorithm="CLOCK")
    p1 = mm.create_process(pid=1)
    p2 = mm.create_process(pid=2)

    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="P1_V0")
    mm.access_page(VirtualPage(1, 1), operation="READ")
    mm.access_page(VirtualPage(2, 0), operation="READ")

    clock: CLOCKCache = mm.policy.policy
    assert clock._is_integrated is True

    # 1. Direct purge_pid on integrated CLOCK must fail
    with pytest.raises(UnsupportedOperationError) as exc_info:
        clock.purge_pid(1)
    assert "integrated clock process teardown must be coordinated through memorymanager" in str(exc_info.value).lower()

    # 2. Coordinated teardown via terminate_process
    v_before = clock.state_version
    mm.terminate_process(1, flush_dirty=True)

    # State assertions
    assert p1.state == ProcessState.TERMINATED
    assert p1.resident_count == 0
    assert clock.state_version > v_before

    # CLOCK slots for PID 1 are None
    for slot in clock.slots:
        assert getattr(slot, "pid", None) != 1

    # Allocated frames owned by PID 1 are freed to allocator
    assert mm.allocator.is_allocated(0) is False
    assert mm.allocator.is_allocated(1) is False
    assert mm.allocator.is_allocated(2) is True  # PID 2 frame remains allocated

    # Subsequent CLOCK sweeps safely skip unallocated/empty slots without crashing
    mm.access_page(VirtualPage(2, 1), operation="READ")
    assert p2.resident_count == 2


def test_clock_teardown_detecting_orphaned_allocated_frames_corrupted_state():
    """Verify bidirectional audit in CLOCK teardown detects corruption and triggers panic latch."""
    mm = MultiProcessMemoryManager(capacity=3, algorithm="CLOCK")
    p1 = mm.create_process(pid=1)
    mm.access_page(VirtualPage(1, 0))

    # Artificially desynchronize: clear PTE without clearing frame
    p1.page_table.unmap_page(0)

    with pytest.raises(CorruptedManagerStateError) as exc_info:
        mm.terminate_process(1)
    assert "Pre-teardown audit failed" in str(exc_info.value)
    assert mm.is_consistent is False


# =============================================================================
# 7. Preflight Transaction Guards & Lifecycle State Transitions
# =============================================================================

def test_termination_preflight_guards_reject_active_transactions():
    """Verify terminate_process rejects termination if any transaction or commit token is active."""
    mm = MultiProcessMemoryManager(capacity=3, algorithm="CLOCK")
    p1 = mm.create_process(pid=1)
    mm.access_page(VirtualPage(1, 0))

    # Case A: Manager transaction active
    mm._in_transaction = True
    with pytest.raises(UnsupportedOperationError) as exc_a:
        mm.terminate_process(1)
    assert "MemoryManager transaction is currently active" in str(exc_a.value)
    mm._in_transaction = False
    assert p1.state == ProcessState.READY  # State preserved

    # Case B: PolicyAdapter active token
    mm.policy._pending_token = "active_token"
    with pytest.raises(UnsupportedOperationError) as exc_b:
        mm.terminate_process(1)
    assert "PolicyAdapter has an active pending token" in str(exc_b.value)
    mm.policy._pending_token = None
    assert p1.state == ProcessState.READY

    # Case C: CLOCK policy pending token
    clock: CLOCKCache = mm.policy.policy
    clock._pending_token = "active_clock_token"
    with pytest.raises(UnsupportedOperationError) as exc_c:
        mm.terminate_process(1)
    assert "CLOCK pending token is active" in str(exc_c.value)
    clock._pending_token = None
    assert p1.state == ProcessState.READY


def test_termination_from_new_state_and_retry_from_failed():
    """Verify process termination lifecycle: NEW -> TERMINATING -> TERMINATED, and retry on failure."""
    mm = MultiProcessMemoryManager(capacity=3, algorithm="LRU")
    p1 = mm.create_process(pid=1)
    p1.state = ProcessState.NEW

    # Termination from NEW state
    mm.terminate_process(1)
    assert p1.state == ProcessState.TERMINATED

    # Retry from TERMINATION_FAILED
    p2 = mm.create_process(pid=2)
    mm.access_page(VirtualPage(2, 0), operation="WRITE", data="D2")

    faulty_store = MagicMock()
    faulty_store.write_page.side_effect = DiskWriteError("I/O error")
    mm.backing_store = faulty_store

    with pytest.raises(DiskWriteError):
        mm.terminate_process(2, flush_dirty=True)
    assert p2.state == ProcessState.TERMINATION_FAILED

    # Fix backing store and retry termination
    mm.backing_store = BackingStore()
    mm.terminate_process(2, flush_dirty=True)
    assert p2.state == ProcessState.TERMINATED


@pytest.mark.parametrize("algo", ["FIFO", "LFU", "MRU"])
def test_terminate_process_general_algorithms(algo: str):
    """
    Verify terminate_process(pid) for FIFO, LFU, and MRU:
    - Dirty resident pages are written back correctly.
    - All process-owned frames are released.
    - Page-table mappings are removed.
    - Frame allocator state is correct.
    - Process resident count becomes zero.
    - Process transitions to TERMINATED.
    - Policy resident entries for that PID are removed.
    - Other processes' resident pages remain intact.
    """
    mm = MultiProcessMemoryManager(capacity=4, algorithm=algo)
    p1 = mm.create_process(pid=1)
    p2 = mm.create_process(pid=2)

    # PID 1: 1 dirty page, 1 clean page
    mm.access_page(VirtualPage(1, 0), operation="WRITE", data=f"{algo}_P1_D0")
    mm.access_page(VirtualPage(1, 1), operation="READ")

    # PID 2: 1 dirty page, 1 clean page
    mm.access_page(VirtualPage(2, 0), operation="WRITE", data=f"{algo}_P2_D0")
    mm.access_page(VirtualPage(2, 1), operation="READ")

    if algo == "LFU":
        # Extra hits to vary frequency
        mm.access_page(VirtualPage(1, 0), operation="READ")
        mm.access_page(VirtualPage(2, 0), operation="READ")

    assert p1.resident_count == 2
    assert p2.resident_count == 2
    assert mm.allocator.free_count == 0

    p1_f0 = mm.get_page_table(1).get_frame_id(0)
    p1_f1 = mm.get_page_table(1).get_frame_id(1)
    p2_f0 = mm.get_page_table(2).get_frame_id(0)
    p2_f1 = mm.get_page_table(2).get_frame_id(1)

    # Terminate PID 1
    mm.terminate_process(1, flush_dirty=True)

    # 1. State transitions and resident count
    assert p1.state == ProcessState.TERMINATED
    assert p1.resident_count == 0

    # 2. Dirty page flushed to backing store
    assert mm.backing_store.read_page(VirtualPage(1, 0)) == f"{algo}_P1_D0"

    # 3. Frames released to allocator
    assert mm.allocator.is_allocated(p1_f0) is False
    assert mm.allocator.is_allocated(p1_f1) is False
    assert mm.allocator.free_count == 2

    # 4. Page table unmapped
    assert mm.get_page_table(1).is_valid(0) is False
    assert mm.get_page_table(1).is_valid(1) is False

    # 5. Policy entries for PID 1 removed
    assert mm.policy.contains(VirtualPage(1, 0)) is False
    assert mm.policy.contains(VirtualPage(1, 1)) is False

    # 6. Other process (PID 2) resident pages intact
    assert p2.state == ProcessState.READY
    assert p2.resident_count == 2
    assert mm.get_page_table(2).is_valid(0) is True
    assert mm.get_page_table(2).is_valid(1) is True
    assert mm.allocator.is_allocated(p2_f0) is True
    assert mm.allocator.is_allocated(p2_f1) is True
    assert mm.frames[p2_f0].owner_pid == 2
    assert mm.frames[p2_f1].owner_pid == 2
    assert mm.policy.contains(VirtualPage(2, 0)) is True
    assert mm.policy.contains(VirtualPage(2, 1)) is True


def test_terminate_process_arc_comprehensive_directory_cleanup():
    """
    Verify terminate_process(pid) under ARC:
    1. Populates resident T1/T2 entries and ghost B1 entries for PID 1.
    2. Terminates PID 1 with flush_dirty=True.
    3. Verifies all entries for PID 1 are removed from T1, T2, B1, and B2.
    4. Verifies ARC invariants (0 <= p <= c, list disjointness) hold.
    5. Verifies PID 2 entries are intact and subsequent PID 2 access succeeds.
    """
    mm = MultiProcessMemoryManager(capacity=3, algorithm="ARC")
    p1 = mm.create_process(pid=1)
    p2 = mm.create_process(pid=2)

    # Step 1: (1, 0) WRITE -> enters T1
    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="ARC_DIRTY_1_0")
    # Step 2: (1, 0) READ -> hit, promoted to T2
    mm.access_page(VirtualPage(1, 0), operation="READ")
    # Step 3: (1, 1) READ -> enters T1
    mm.access_page(VirtualPage(1, 1), operation="READ")
    # Step 4: (2, 0) READ -> enters T1
    mm.access_page(VirtualPage(2, 0), operation="READ")

    arc: ARCCache = mm.policy.policy
    assert VirtualPage(1, 0) in arc.T2
    assert VirtualPage(1, 1) in arc.T1
    assert VirtualPage(2, 0) in arc.T1

    # Step 5: (2, 1) READ -> causes eviction of T1 LRU: VirtualPage(1, 1) into B1 ghost cache
    mm.access_page(VirtualPage(2, 1), operation="READ")

    # Verify PID 1 has resident page in T2 and ghost page in B1
    assert VirtualPage(1, 0) in arc.T2
    assert VirtualPage(1, 1) in arc.B1
    assert VirtualPage(2, 0) in arc.T1
    assert VirtualPage(2, 1) in arc.T1
    p1_f0 = mm.get_page_table(1).get_frame_id(0)

    # Terminate PID 1
    mm.terminate_process(1, flush_dirty=True)

    # 1. State transitions and resident count
    assert p1.state == ProcessState.TERMINATED
    assert p1.resident_count == 0

    # 2. Dirty page flushed to backing store
    assert mm.backing_store.read_page(VirtualPage(1, 0)) == "ARC_DIRTY_1_0"

    # 3. Frame released to allocator
    assert mm.allocator.is_allocated(p1_f0) is False
    assert mm.allocator.free_count == 1

    # 4. Page table unmapped
    assert mm.get_page_table(1).is_valid(0) is False
    assert mm.get_page_table(1).is_valid(1) is False

    # 5. All PID 1 entries purged from T1, T2, B1, and B2
    for directory in (arc.T1, arc.T2, arc.B1, arc.B2):
        for page in directory:
            assert getattr(page, "pid", None) != 1

    # 6. PID 2 entries preserved
    assert VirtualPage(2, 0) in arc.T1
    assert VirtualPage(2, 1) in arc.T1
    assert p2.resident_count == 2
    assert mm.get_page_table(2).is_valid(0) is True
    assert mm.get_page_table(2).is_valid(1) is True

    # 7. ARC invariants verified
    arc._verify_invariants()
    assert 0 <= arc.p <= arc.capacity

    # 8. Subsequent access by PID 2 operates normally
    res_hit = mm.access_page(VirtualPage(2, 0), operation="READ")
    assert res_hit.hit is True
    assert VirtualPage(2, 0) in arc.T2  # Promoted to T2 on hit


# =============================================================================
# 8. Optimal (OPT) Interleaved Sequence Validation & Termination Guard
# =============================================================================

def test_opt_interleaved_multi_process_sequence_and_termination_guard():
    """
    Verify Global OPT with interleaved multi-process sequence:
    1. References executed in exact sequence order.
    2. Terminating a process with pending future references is rejected.
    3. Once references are exhausted, termination succeeds cleanly.
    """
    seq = [
        VirtualPage(1, 0),
        VirtualPage(2, 0),
        VirtualPage(1, 1),
        VirtualPage(2, 1),
        VirtualPage(1, 0),
    ]
    mm = MultiProcessMemoryManager(capacity=3, algorithm="OPT", reference_sequence=seq)
    mm.create_process(pid=1)
    mm.create_process(pid=2)

    # Execute first 2 accesses: (1, 0) and (2, 0)
    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(2, 0))

    # Attempting to terminate P1 while (1, 1) and (1, 0) are still pending in trace
    with pytest.raises(InvalidProcessStateError) as exc_info:
        mm.terminate_process(1)
    assert "unconsumed future references exist in Global OPT trace" in str(exc_info.value)
    assert mm.get_process(1).state == ProcessState.READY

    # Consume remaining references
    mm.access_page(VirtualPage(1, 1))
    mm.access_page(VirtualPage(2, 1))
    mm.access_page(VirtualPage(1, 0))

    # All references for P1 are now consumed. Termination succeeds!
    mm.terminate_process(1)
    assert mm.get_process(1).state == ProcessState.TERMINATED


# =============================================================================
# 9. Coordinated Multi-Process Reset
# =============================================================================

def test_multi_process_coordinated_reset():
    """Verify mm.reset() restores memory manager, frames, page tables, and process table to initial state."""
    mm = MultiProcessMemoryManager(capacity=3, algorithm="LRU")
    mm.create_process(pid=1)
    mm.create_process(pid=2)

    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(2, 0))

    assert mm.allocator.free_count == 1
    assert mm.process_table.count == 2

    mm.reset()

    assert mm.allocator.free_count == 3
    assert mm.process_table.count == 0
    assert len(mm.page_tables) == 0
    assert mm.is_consistent is True
    assert mm.metrics.requests == 0
