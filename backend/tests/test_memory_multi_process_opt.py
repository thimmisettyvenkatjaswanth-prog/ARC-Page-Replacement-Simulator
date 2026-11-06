"""
Unit and integration tests for Stage 12.2 Phase 4C Substage 4C.6:
Multi-Process OPT Sequence Slicing & Termination Contract.

Covers:
- Global interleaved clairvoyance and victim selection across processes.
- Global OPT termination contract: strict rejection of termination when unconsumed
  references remain for that PID, preserving all manager state and OPT position.
- Global OPT clean termination when references are exhausted while other processes continue.
- Local OPT sequence projection and complete stream isolation.
- Local OPT termination dropping local stream without affecting other processes.
- Required upfront PID registration validation.
- I/O failure (write-back and page-in) atomicity and sequence step preservation.
- Sequence mismatch and exhaustion zero-mutation guarantees.
- Cache hit single-step sequence advancement.
- Local termination retry and policy retention on storage failure.
- Idempotent repeated termination.
- Direct OptimalCache pending-reference query unit tests.
"""
from unittest.mock import MagicMock
import pytest

from backend.algorithms.opt import OptimalCache
from backend.memory.backing_store import BackingStore
from backend.memory.exceptions import (
    DiskReadError,
    DiskWriteError,
    InvalidProcessStateError,
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
# 1. Global Interleaved Clairvoyance & Victim Selection
# =============================================================================

def test_global_opt_interleaved_clairvoyance():
    """
    1. Verify that Global OPT operates with full clairvoyance across an interleaved
    multi-process sequence, correctly calculating future distances and deterministic
    tie-breaking to select optimal victims across processes.
    """
    # Trace:
    # 0: (1, 0) - Miss -> frame 0
    # 1: (2, 0) - Miss -> frame 1
    # 2: (1, 1) - Miss -> frame 2 (Memory full: capacity=3)
    # 3: (2, 1) - Miss -> Must evict.
    #    Candidates:
    #    (1, 0): next referenced at step 4 (dist = 1)
    #    (2, 0): never referenced again (dist = inf, arrival step 1)
    #    (1, 1): never referenced again (dist = inf, arrival step 2)
    #    Tie-breaking: (2, 0) arrived before (1, 1) -> (2, 0) must be evicted!
    # 4: (1, 0) - Hit!
    seq = [
        VirtualPage(1, 0),
        VirtualPage(2, 0),
        VirtualPage(1, 1),
        VirtualPage(2, 1),
        VirtualPage(1, 0),
    ]
    mm = MultiProcessMemoryManager(
        capacity=3, algorithm="OPT", reference_sequence=seq, scope=ReplacementScope.GLOBAL
    )
    p1 = mm.create_process(pid=1)
    p2 = mm.create_process(pid=2)

    r0 = mm.access_page(VirtualPage(1, 0))
    assert r0.hit is False
    assert r0.frame_id == 0

    r1 = mm.access_page(VirtualPage(2, 0))
    assert r1.hit is False
    assert r1.frame_id == 1

    r2 = mm.access_page(VirtualPage(1, 1))
    assert r2.hit is False
    assert r2.frame_id == 2
    assert mm.allocator.free_count == 0

    # Step 3: Eviction of (2, 0) due to inf distance and earlier arrival
    r3 = mm.access_page(VirtualPage(2, 1))
    assert r3.hit is False
    assert r3.evicted_page == VirtualPage(2, 0)
    assert r3.victim_process_id == 2

    # Step 4: Hit on (1, 0)
    r4 = mm.access_page(VirtualPage(1, 0))
    assert r4.hit is True
    assert r4.frame_id == 0
    assert mm.metrics.hits == 1
    assert mm.metrics.misses == 4


# =============================================================================
# 2. Global OPT Termination Contracts (Pending vs Exhausted)
# =============================================================================

def test_global_opt_rejects_termination_with_pending_references():
    """
    2. In Global scope, attempt to terminate a process that has unconsumed future
    references in the global trace. Must raise InvalidProcessStateError and leave
    PCB state, resident counts, page tables, frames, allocator, metrics, and OPT
    position completely unmutated.
    """
    seq = [
        VirtualPage(1, 0),
        VirtualPage(2, 0),
        VirtualPage(1, 1),  # Unconsumed future reference for PID 1
        VirtualPage(2, 1),
    ]
    mm = MultiProcessMemoryManager(
        capacity=3, algorithm="OPT", reference_sequence=seq, scope=ReplacementScope.GLOBAL
    )
    p1 = mm.create_process(pid=1)
    p2 = mm.create_process(pid=2)

    # Step 0: P1 accesses (1, 0)
    mm.access_page(VirtualPage(1, 0))
    opt: OptimalCache = mm.policy.policy
    assert opt.current_step == 1

    # Snapshot before illegal termination attempt
    p1_state_before = p1.state
    p1_resident_before = p1.resident_count
    free_frames_before = mm.allocator.free_count
    frame0_owner_before = mm.frames[0].owner_pid
    opt_step_before = opt.current_step
    requests_before = mm.metrics.requests

    # P1 still has reference at index 2: (1, 1)
    with pytest.raises(InvalidProcessStateError) as exc_info:
        mm.terminate_process(1)

    assert "Cannot terminate process 1: 1 unconsumed future references exist in Global OPT trace." in str(exc_info.value)

    # Assert ZERO state mutation
    assert p1.state == p1_state_before
    assert p1.resident_count == p1_resident_before
    assert mm.allocator.free_count == free_frames_before
    assert mm.frames[0].owner_pid == frame0_owner_before
    assert mm.frames[0].is_allocated is True
    assert mm.get_page_table(1).is_valid(0) is True
    assert opt.current_step == opt_step_before
    assert mm.metrics.requests == requests_before
    assert mm.is_consistent is True


def test_global_opt_terminates_cleanly_when_references_exhausted():
    """
    3. In Global scope, when all references for one PID have been consumed, that
    process must be allowed to terminate cleanly. Physical frames are freed, its
    residency is cleared, while the remaining process continues its execution
    through the remainder of the OPT sequence without interruption.
    """
    seq = [
        VirtualPage(1, 0),
        VirtualPage(1, 1),
        VirtualPage(2, 0),
        VirtualPage(2, 1),
    ]
    mm = MultiProcessMemoryManager(
        capacity=3, algorithm="OPT", reference_sequence=seq, scope=ReplacementScope.GLOBAL
    )
    p1 = mm.create_process(pid=1)
    p2 = mm.create_process(pid=2)

    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(1, 1))

    opt: OptimalCache = mm.policy.policy
    assert opt.current_step == 2
    assert opt.has_pending_references_for_pid(1) is False
    assert opt.has_pending_references_for_pid(2) is True

    # P1 has no future references -> Clean termination succeeds!
    mm.terminate_process(1, flush_dirty=True)
    assert p1.state == ProcessState.TERMINATED
    assert p1.resident_count == 0
    assert mm.get_page_table(1).is_valid(0) is False
    assert mm.get_page_table(1).is_valid(1) is False

    # Process 2 continues its remaining references seamlessly
    res_p2_0 = mm.access_page(VirtualPage(2, 0))
    assert res_p2_0.hit is False
    assert opt.current_step == 3

    res_p2_1 = mm.access_page(VirtualPage(2, 1))
    assert res_p2_1.hit is False
    assert opt.current_step == 4

    assert opt.has_pending_references_for_pid(2) is False

    # P2 terminates cleanly
    mm.terminate_process(2)
    assert p2.state == ProcessState.TERMINATED
    assert p2.resident_count == 0


# =============================================================================
# 3. Local OPT Sequence Projection, Slicing & Isolation
# =============================================================================

def test_local_opt_sequence_projection_and_isolation():
    """
    4. Verify that in Local replacement mode:
       - Each process receives its own projected sequence S_p = [v in S_global if v.pid == p].
       - Interleaving of requests from different processes does not distort the per-process sequence order.
       - Each process's current_step advances only on its own memory accesses.
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

    # Verify per-process projection
    assert opt1.sequence == [VirtualPage(1, 0), VirtualPage(1, 1), VirtualPage(1, 2)]
    assert opt2.sequence == [VirtualPage(2, 0), VirtualPage(2, 1)]

    # Interleaved executions
    mm.access_page(VirtualPage(1, 0))
    assert opt1.current_step == 1
    assert opt2.current_step == 0

    mm.access_page(VirtualPage(2, 0))
    assert opt1.current_step == 1
    assert opt2.current_step == 1

    mm.access_page(VirtualPage(1, 1))
    assert opt1.current_step == 2
    assert opt2.current_step == 1

    mm.access_page(VirtualPage(2, 1))
    assert opt1.current_step == 2
    assert opt2.current_step == 2

    mm.access_page(VirtualPage(1, 2))
    assert opt1.current_step == 3
    assert opt2.current_step == 2


def test_local_opt_termination_drops_local_stream_without_affecting_others():
    """
    5. In Local replacement mode:
       - The global pending-reference termination restriction does NOT apply.
       - Terminating a process with unconsumed local references discards its local policy.
       - Other processes' local OPT sequence steps, queues, and caches are completely unaffected.
    """
    seq = [
        VirtualPage(1, 0),
        VirtualPage(2, 0),
        VirtualPage(1, 1),  # Unconsumed by P1
        VirtualPage(2, 1),  # Future for P2
        VirtualPage(1, 2),  # Unconsumed by P1
    ]
    mm = MultiProcessMemoryManager(
        capacity=4, algorithm="OPT", reference_sequence=seq, scope=ReplacementScope.LOCAL
    )
    p1 = mm.create_process(pid=1)
    p2 = mm.create_process(pid=2)

    opt1: OptimalCache = p1.local_policy.policy
    opt2: OptimalCache = p2.local_policy.policy

    mm.access_page(VirtualPage(1, 0))
    mm.access_page(VirtualPage(2, 0))
    assert opt1.current_step == 1
    assert opt2.current_step == 1

    # Terminate P1 while it still has 2 unconsumed local references
    mm.terminate_process(1, flush_dirty=True)
    assert p1.state == ProcessState.TERMINATED
    assert p1.resident_count == 0

    # Verify P2's local OPT state is completely unaffected
    assert opt2.current_step == 1
    assert opt2.sequence == [VirtualPage(2, 0), VirtualPage(2, 1)]
    assert opt2.has_pending_references_for_pid(2) is True

    # P2 executes its remaining reference (2, 1) successfully
    res = mm.access_page(VirtualPage(2, 1))
    assert res.hit is False
    assert opt2.current_step == 2
    assert opt2.has_pending_references_for_pid(2) is False


# =============================================================================
# 4. Upfront Validation & Sequence Guarantees
# =============================================================================

def test_opt_unregistered_pid_rejected_without_mutation():
    """
    6. All PIDs appearing in the OPT reference sequence must be registered in the
    process table before the first request. Any unregistered PID triggers an
    UnsupportedOperationError before executing any memory access, with zero mutation.
    """
    seq = [VirtualPage(1, 0), VirtualPage(99, 0)]
    mm = MultiProcessMemoryManager(
        capacity=3, algorithm="OPT", reference_sequence=seq, scope=ReplacementScope.GLOBAL
    )
    mm.create_process(pid=1)
    # PID 99 is intentionally omitted

    with pytest.raises(UnsupportedOperationError) as exc_info:
        mm.access_page(VirtualPage(1, 0))

    assert "PID 99 appearing in OPT reference trace is not registered" in str(exc_info.value)
    assert mm.allocator.free_count == 3
    assert mm.policy.policy.current_step == 0


def test_opt_sequence_mismatch_without_mutation():
    """
    Verify sequence mismatch rejection:
    Accessing a page differing from the expected page at current_step raises ValueError
    without advancing the sequence step or mutating cache state.
    """
    seq = [VirtualPage(1, 0), VirtualPage(1, 1)]
    mm = MultiProcessMemoryManager(
        capacity=2, algorithm="OPT", reference_sequence=seq, scope=ReplacementScope.GLOBAL
    )
    mm.create_process(pid=1)

    opt: OptimalCache = mm.policy.policy

    # Request unexpected (1, 5) when (1, 0) was expected
    with pytest.raises(ValueError, match="Sequence mismatch at index 0"):
        mm.access_page(VirtualPage(1, 5))

    assert opt.current_step == 0
    assert len(opt.cache) == 0
    assert mm.allocator.free_count == 2
    assert mm.is_consistent is True

    # Subsequent access with the expected page succeeds
    res = mm.access_page(VirtualPage(1, 0))
    assert res.hit is False
    assert opt.current_step == 1


def test_opt_sequence_exhaustion_without_mutation():
    """
    Verify sequence exhaustion rejection:
    Accessing a page when current_step >= len(sequence) raises ValueError without mutation.
    """
    seq = [VirtualPage(1, 0)]
    mm = MultiProcessMemoryManager(
        capacity=2, algorithm="OPT", reference_sequence=seq, scope=ReplacementScope.GLOBAL
    )
    mm.create_process(pid=1)
    opt: OptimalCache = mm.policy.policy

    mm.access_page(VirtualPage(1, 0))
    assert opt.current_step == 1

    with pytest.raises(ValueError, match="Reference sequence exhausted"):
        mm.access_page(VirtualPage(1, 0))

    assert opt.current_step == 1
    assert mm.is_consistent is True


def test_opt_cache_hit_advances_sequence_exactly_once():
    """
    Verify commit behavior:
    A cache hit advances current_step exactly once.
    """
    seq = [VirtualPage(1, 0), VirtualPage(1, 0)]
    mm = MultiProcessMemoryManager(
        capacity=2, algorithm="OPT", reference_sequence=seq, scope=ReplacementScope.GLOBAL
    )
    mm.create_process(pid=1)
    opt: OptimalCache = mm.policy.policy

    # Cold miss: step 0 -> 1
    r1 = mm.access_page(VirtualPage(1, 0))
    assert r1.hit is False
    assert opt.current_step == 1

    # Resident hit: step 1 -> 2
    r2 = mm.access_page(VirtualPage(1, 0))
    assert r2.hit is True
    assert opt.current_step == 2
    assert mm.metrics.hits == 1


# =============================================================================
# 5. Fault Injection & Sequence Step Preservation
# =============================================================================

def test_opt_io_failure_preserves_sequence_step():
    """
    7. Inject write-back and page-in failures during OPT execution.
    Verify that sequence position remains unchanged, pending token is aborted,
    and subsequent retries work correctly.
    """
    real_store = BackingStore()
    seq = [
        VirtualPage(1, 0),
        VirtualPage(1, 1),
        VirtualPage(1, 2),  # Requires evicting (1, 0)
    ]
    mm = MultiProcessMemoryManager(
        capacity=2,
        algorithm="OPT",
        backing_store=real_store,
        reference_sequence=seq,
        scope=ReplacementScope.GLOBAL,
    )
    mm.create_process(pid=1)
    opt: OptimalCache = mm.policy.policy

    # Populate: (1, 0) WRITE (dirty), (1, 1) READ
    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="DIRTY_V0")
    mm.access_page(VirtualPage(1, 1), operation="READ")
    assert opt.current_step == 2

    # Step 2a Fault Injection: DiskWriteError during dirty write-back
    mock_store = MagicMock()
    mock_store.write_page.side_effect = DiskWriteError("Simulated disk write failure")
    mm.backing_store = mock_store

    with pytest.raises(DiskWriteError):
        mm.access_page(VirtualPage(1, 2))

    # Invariants preserved:
    assert opt.current_step == 2  # Did NOT advance
    assert mm.frames[0].dirty is True  # Remained dirty
    assert mm.get_page_table(1).is_valid(0) is True  # Remained resident
    assert mm.is_consistent is True

    # Step 2b Fault Injection: Write succeeds, read fails
    mock_store.write_page.side_effect = real_store.write_page
    mock_store.read_page.side_effect = DiskReadError("Simulated disk read failure")

    with pytest.raises(DiskReadError):
        mm.access_page(VirtualPage(1, 2))

    # Invariants preserved:
    assert opt.current_step == 2  # Did NOT advance
    assert mm.frames[0].dirty is False  # Dirty bit cleared after successful write
    assert mm.get_page_table(1).is_valid(0) is True  # Victim remained resident
    assert mm.is_consistent is True

    # Restore real store and retry: succeeds!
    mm.backing_store = real_store
    res = mm.access_page(VirtualPage(1, 2))
    assert res.hit is False
    assert res.evicted_page == VirtualPage(1, 0)
    assert opt.current_step == 3


def test_local_opt_failed_termination_retains_local_opt_instance():
    """
    Verify that in Local scope, when termination fails during Stage 3 dirty write-back,
    the local policy is NOT discarded or reset, remaining fully intact for retry.
    """
    real_store = BackingStore()
    seq = [VirtualPage(1, 0), VirtualPage(1, 1)]
    mm = MultiProcessMemoryManager(
        capacity=2,
        algorithm="OPT",
        backing_store=real_store,
        reference_sequence=seq,
        scope=ReplacementScope.LOCAL,
    )
    p1 = mm.create_process(pid=1)

    mm.access_page(VirtualPage(1, 0), operation="WRITE", data="D0")
    opt1: OptimalCache = p1.local_policy.policy
    assert opt1.current_step == 1
    assert VirtualPage(1, 0) in opt1.cache

    # Inject write error during termination
    mock_store = MagicMock()
    mock_store.write_page.side_effect = DiskWriteError("Disk failed")
    mm.backing_store = mock_store

    with pytest.raises(DiskWriteError):
        mm.terminate_process(1, flush_dirty=True)

    # Invariants preserved:
    assert p1.state == ProcessState.TERMINATION_FAILED
    assert p1.resident_count == 1
    assert VirtualPage(1, 0) in opt1.cache  # Local OPT was NOT discarded!
    assert opt1.current_step == 1

    # Retry with working store
    mm.backing_store = real_store
    mm.terminate_process(1, flush_dirty=True)
    assert p1.state == ProcessState.TERMINATED
    assert p1.resident_count == 0
    assert len(opt1.cache) == 0  # Cleanly reset after successful termination


def test_opt_repeated_termination_idempotent():
    """
    Verify that repeated termination attempts on an already TERMINATED process
    are idempotent no-ops that do not mutate OPT or manager state.
    """
    seq = [VirtualPage(1, 0)]
    mm = MultiProcessMemoryManager(
        capacity=2, algorithm="OPT", reference_sequence=seq, scope=ReplacementScope.GLOBAL
    )
    p1 = mm.create_process(pid=1)
    mm.access_page(VirtualPage(1, 0))

    mm.terminate_process(1)
    assert p1.state == ProcessState.TERMINATED

    # Second termination call is an idempotent no-op
    mm.terminate_process(1)
    assert p1.state == ProcessState.TERMINATED
    assert mm.is_consistent is True


# =============================================================================
# 6. OptimalCache Query Interface Unit Tests
# =============================================================================

def test_opt_pending_reference_queries():
    """
    Verify has_pending_references_for_pid() and get_pending_references_for_pid()
    work non-mutatingly across unconsumed, partially consumed, and exhausted states.
    """
    seq = [
        VirtualPage(1, 0),
        VirtualPage(2, 0),
        VirtualPage(1, 1),
    ]
    opt = OptimalCache(capacity=2, reference_sequence=seq)

    # At start (step 0): both have pending references
    assert opt.has_pending_references_for_pid(1) is True
    assert opt.has_pending_references_for_pid(2) is True
    assert opt.has_pending_references_for_pid(99) is False
    assert opt.get_pending_references_for_pid(1) == [VirtualPage(1, 0), VirtualPage(1, 1)]
    assert opt.get_pending_references_for_pid(2) == [VirtualPage(2, 0)]

    # Simulate advancement to step 2 (consumed 0 and 1)
    opt.current_step = 2
    assert opt.has_pending_references_for_pid(1) is True
    assert opt.has_pending_references_for_pid(2) is False  # (2, 0) was at step 1
    assert opt.get_pending_references_for_pid(1) == [VirtualPage(1, 1)]
    assert opt.get_pending_references_for_pid(2) == []

    # Sequence exhaustion at step 3
    opt.current_step = 3
    assert opt.has_pending_references_for_pid(1) is False
    assert opt.has_pending_references_for_pid(2) is False
    assert opt.get_pending_references_for_pid(1) == []
    assert opt.get_pending_references_for_pid(2) == []

