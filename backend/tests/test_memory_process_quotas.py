"""
Unit and integration tests for Stage 12.2 Phase 4C Substage 4C.3:
ProcessTable Aggregate Quota Validation and ProcessControlBlock Extensions.
"""
from unittest.mock import MagicMock
import pytest

from backend.memory.exceptions import (
    DuplicateProcessError,
    InvalidAddressError,
    InvalidProcessStateError,
    InvalidQuotaError,
    ProcessNotFoundError,
)
from backend.memory.process import (
    ProcessControlBlock,
    ProcessQuota,
    ProcessState,
    ProcessTable,
)
from backend.memory.policy_adapter import PolicyAdapter
from backend.algorithms.arc import ARCCache
from backend.algorithms.clock import CLOCKCache
from backend.algorithms.fifo import FIFOCache
from backend.algorithms.lfu import LFUCache
from backend.algorithms.lru import LRUCache
from backend.algorithms.mru import MRUCache
from backend.algorithms.opt import OptimalCache
from backend.memory.page import VirtualPage


# =============================================================================
# 1. ProcessTable Capacity Initialization and Validation
# =============================================================================

def test_process_table_capacity_validation():
    """Verify ProcessTable capacity validates positive integers and rejects invalid types."""
    # Valid positive integers
    pt1 = ProcessTable(1)
    assert pt1.capacity == 1

    pt2 = ProcessTable(capacity=16)
    assert pt2.capacity == 16

    # Backward compatibility: unconstrained table when omitted
    pt_unconstrained = ProcessTable()
    assert pt_unconstrained.capacity is None

    # Reject zero and negative values
    with pytest.raises(InvalidQuotaError) as exc_zero:
        ProcessTable(0)
    assert "positive integer" in str(exc_zero.value)

    with pytest.raises(InvalidQuotaError) as exc_neg:
        ProcessTable(-4)
    assert "positive integer" in str(exc_neg.value)

    # Reject booleans
    with pytest.raises(InvalidQuotaError):
        ProcessTable(True)
    with pytest.raises(InvalidQuotaError):
        ProcessTable(False)

    # Reject non-integer types
    with pytest.raises(InvalidQuotaError):
        ProcessTable("8")
    with pytest.raises(InvalidQuotaError):
        ProcessTable(4.5)
    with pytest.raises(InvalidQuotaError):
        ProcessTable(capacity=None)


# =============================================================================
# 2. Aggregate Minimum Quota Enforcements (Sum min_frames <= C)
# =============================================================================

def test_aggregate_quota_below_capacity():
    """Verify registration succeeds when aggregate min_frames is strictly below capacity."""
    pt = ProcessTable(capacity=10)
    p1 = pt.create_process(pid=1, quota=ProcessQuota(min_frames=3))
    p2 = pt.create_process(pid=2, quota=ProcessQuota(min_frames=4))

    assert pt.count == 2
    assert pt.active_count == 2
    assert p1.quota.min_frames == 3
    assert p2.quota.min_frames == 4


def test_aggregate_quota_exactly_equal_to_capacity():
    """
    Verify registration succeeds when aggregate min_frames exactly equals physical capacity.
    Per Phase 4C architecture, weak inequality (sum min_frames <= C) is strictly valid.
    """
    pt = ProcessTable(capacity=6)
    pt.create_process(pid=1, quota=ProcessQuota(min_frames=2))
    pt.create_process(pid=2, quota=ProcessQuota(min_frames=4))

    assert pt.count == 2
    assert pt.active_count == 2
    active = pt.get_active_processes()
    assert sum(p.quota.min_frames for p in active) == 6


def test_aggregate_quota_exceeding_capacity_rejected():
    """Verify registration raises InvalidQuotaError when aggregate min_frames exceeds capacity."""
    pt = ProcessTable(capacity=5)
    pt.create_process(pid=1, quota=ProcessQuota(min_frames=3))

    with pytest.raises(InvalidQuotaError) as exc_info:
        pt.create_process(pid=2, quota=ProcessQuota(min_frames=3))
    assert "Aggregate minimum quota sum (6) exceeds physical capacity (5)" in str(exc_info.value)


def test_rejection_during_process_creation():
    """Verify create_process checks aggregate quota and leaves table unchanged upon failure."""
    pt = ProcessTable(capacity=4)
    pt.create_process(pid=1, quota=ProcessQuota(min_frames=3))

    with pytest.raises(InvalidQuotaError):
        pt.create_process(pid=2, quota=ProcessQuota(min_frames=2))

    assert pt.has_process(2) is False
    assert 2 not in pt
    assert pt.count == 1
    assert pt.active_count == 1


def test_rejection_during_direct_registration():
    """Verify register_process checks aggregate quota and leaves table unchanged upon failure."""
    pt = ProcessTable(capacity=4)
    pt.create_process(pid=1, quota=ProcessQuota(min_frames=3))

    candidate = ProcessControlBlock(pid=2, quota=ProcessQuota(min_frames=2), state=ProcessState.READY)
    with pytest.raises(InvalidQuotaError):
        pt.register_process(candidate)

    assert pt.has_process(2) is False
    assert 2 not in pt
    assert pt.count == 1
    assert pt.active_count == 1


def test_no_partial_registration_or_mutation_after_rejection():
    """Verify that failed creation/registration causes zero mutations across all state."""
    pt = ProcessTable(capacity=4)
    p1 = pt.create_process(pid=1, quota=ProcessQuota(min_frames=3, max_frames=4))

    # Failed creation
    with pytest.raises(InvalidQuotaError):
        pt.create_process(pid=2, quota=ProcessQuota(min_frames=2))

    # Failed registration
    p3 = ProcessControlBlock(pid=3, quota=ProcessQuota(min_frames=5), state=ProcessState.READY)
    with pytest.raises(InvalidQuotaError):
        pt.register_process(p3)

    assert pt.get_all_processes() == [p1]
    assert pt.get_active_processes() == [p1]
    assert p1.quota.min_frames == 3
    assert p1.quota.max_frames == 4
    assert p1.state == ProcessState.READY


# =============================================================================
# 3. Lifecycle States & Terminated Process Quota Handling
# =============================================================================

def test_terminated_process_quota_reservation_handling():
    """
    Verify that terminated processes do not hold active quota reservations.
    Once a process transitions to TERMINATED, its reservation is freed for new processes.
    """
    pt = ProcessTable(capacity=4)
    p1 = pt.create_process(pid=1, quota=ProcessQuota(min_frames=2))
    p2 = pt.create_process(pid=2, quota=ProcessQuota(min_frames=2))

    # Table is at full quota (2 + 2 = 4)
    with pytest.raises(InvalidQuotaError):
        pt.create_process(pid=3, quota=ProcessQuota(min_frames=1))

    # Terminate process 2 via valid lifecycle: READY -> TERMINATING -> TERMINATED
    p2.transition_to(ProcessState.TERMINATING)
    p2.transition_to(ProcessState.TERMINATED)

    # Process 2 is now TERMINATED; active quota reservation dropped to 2
    # Registering process 3 with min_frames=2 now succeeds (2 + 2 = 4)
    p3 = pt.create_process(pid=3, quota=ProcessQuota(min_frames=2))

    assert pt.count == 3
    assert pt.active_count == 2
    assert pt.get_process(1).state == ProcessState.READY
    assert pt.get_process(2).state == ProcessState.TERMINATED
    assert pt.get_process(3).state == ProcessState.READY


def test_all_lifecycle_states_and_treatment_under_active_definition():
    """
    Verify quota reservation handling across all 7 ProcessState lifecycle states:
    - Non-terminated states (NEW, READY, RUNNING, BLOCKED, TERMINATING, TERMINATION_FAILED):
      MUST retain active quota reservations because their physical frames have not yet been reclaimed.
    - Only TERMINATED: releases its quota reservation, as destructive teardown has completed.
    """
    pt = ProcessTable(capacity=6)

    # 1. Register a TERMINATED PCB: does NOT consume quota headroom
    p_term = ProcessControlBlock(pid=10, state=ProcessState.TERMINATED, quota=ProcessQuota(min_frames=4))
    pt.register_process(p_term)
    assert pt.count == 1
    assert pt.active_count == 0

    # 2. Register processes in each non-terminated state (each min_frames=1)
    p_new = ProcessControlBlock(pid=1, state=ProcessState.NEW, quota=ProcessQuota(min_frames=1))
    pt.register_process(p_new)  # sum = 1

    p_ready = ProcessControlBlock(pid=2, state=ProcessState.READY, quota=ProcessQuota(min_frames=1))
    pt.register_process(p_ready)  # sum = 2

    p_run = ProcessControlBlock(pid=3, state=ProcessState.RUNNING, quota=ProcessQuota(min_frames=1))
    pt.register_process(p_run)  # sum = 3

    p_blk = ProcessControlBlock(pid=4, state=ProcessState.BLOCKED, quota=ProcessQuota(min_frames=1))
    pt.register_process(p_blk)  # sum = 4

    p_ing = ProcessControlBlock(pid=5, state=ProcessState.TERMINATING, quota=ProcessQuota(min_frames=1))
    pt.register_process(p_ing)  # sum = 5 (retains reservation during teardown)

    p_fail = ProcessControlBlock(pid=6, state=ProcessState.TERMINATION_FAILED, quota=ProcessQuota(min_frames=1))
    pt.register_process(p_fail)  # sum = 6 == capacity (retains reservation after teardown failure)

    assert pt.count == 7

    # Any additional reservation fails because all 6 frames are reserved
    p_extra = ProcessControlBlock(pid=7, state=ProcessState.READY, quota=ProcessQuota(min_frames=1))
    with pytest.raises(InvalidQuotaError):
        pt.register_process(p_extra)


def test_failed_termination_preserves_quota_reservation_consistency():
    """
    Verify that if process termination fails (TERMINATING -> TERMINATION_FAILED),
    its quota reservation is preserved and does not allow premature overcommit.
    Only full transition to TERMINATED releases the reservation.
    """
    pt = ProcessTable(capacity=4)
    p1 = pt.create_process(pid=1, quota=ProcessQuota(min_frames=3))
    p2 = pt.create_process(pid=2, quota=ProcessQuota(min_frames=1))

    # Table is at capacity (3 + 1 = 4)
    # P1 begins termination: READY -> TERMINATING
    p1.transition_to(ProcessState.TERMINATING)

    # While in TERMINATING, P1 still holds frames; new reservations must be rejected
    with pytest.raises(InvalidQuotaError):
        pt.create_process(pid=3, quota=ProcessQuota(min_frames=1))

    # Termination write-back fails: TERMINATING -> TERMINATION_FAILED
    p1.transition_to(ProcessState.TERMINATION_FAILED)

    # In TERMINATION_FAILED, P1 still holds frames; quota reservation remains protected
    with pytest.raises(InvalidQuotaError):
        pt.create_process(pid=3, quota=ProcessQuota(min_frames=1))

    # P1 retries termination and succeeds: TERMINATION_FAILED -> TERMINATING -> TERMINATED
    p1.transition_to(ProcessState.TERMINATING)
    p1.transition_to(ProcessState.TERMINATED)

    # Now P1 is TERMINATED; its 3 reserved frames are freed
    # Registering P3 with min_frames=3 now succeeds (1 + 3 = 4 <= 4)
    p3 = pt.create_process(pid=3, quota=ProcessQuota(min_frames=3))
    assert pt.count == 3
    assert pt.get_process(1).state == ProcessState.TERMINATED
    assert pt.get_process(3).state == ProcessState.READY


def test_zero_minimum_quota():
    """Verify processes with min_frames=0 do not consume reservation headroom."""
    pt = ProcessTable(capacity=3)

    # Many processes with min_frames=0 can be registered
    for i in range(10):
        pt.create_process(pid=i, quota=ProcessQuota(min_frames=0))
    assert pt.count == 10
    assert pt.active_count == 10

    # Can still register a process with min_frames = capacity
    pt.create_process(pid=100, quota=ProcessQuota(min_frames=3))
    assert pt.count == 11

    # Another min_frames=0 still succeeds
    pt.create_process(pid=101, quota=ProcessQuota(min_frames=0))
    assert pt.count == 12

    # min_frames > 0 fails because aggregate reservation is 3
    with pytest.raises(InvalidQuotaError):
        pt.create_process(pid=102, quota=ProcessQuota(min_frames=1))


def test_multiple_processes_with_mixed_min_and_max_quotas():
    """Verify combinations of mixed min_frames and max_frames are properly enforced."""
    pt = ProcessTable(capacity=10)

    p1 = pt.create_process(pid=1, quota=ProcessQuota(min_frames=2, max_frames=5))
    p2 = pt.create_process(pid=2, quota=ProcessQuota(min_frames=3, max_frames=8))
    p3 = pt.create_process(pid=3, quota=ProcessQuota(min_frames=0, max_frames=4))
    p4 = pt.create_process(pid=4, quota=ProcessQuota(min_frames=5, max_frames=10))

    # Total min_frames = 2 + 3 + 0 + 5 = 10 == capacity
    assert pt.count == 4
    assert sum(p.quota.min_frames for p in pt.get_active_processes()) == 10

    # Adding even 1 more min_frame fails
    with pytest.raises(InvalidQuotaError):
        pt.create_process(pid=5, quota=ProcessQuota(min_frames=1, max_frames=2))


# =============================================================================
# 4. Duplicate PID and Pre-Validation Integrity
# =============================================================================

def test_duplicate_pid_behavior_takes_precedence_over_quota():
    """
    Verify duplicate PID detection takes precedence over quota checking.
    Even if the duplicate process has an out-of-bounds quota, DuplicateProcessError is raised.
    """
    pt = ProcessTable(capacity=4)
    pt.create_process(pid=1, quota=ProcessQuota(min_frames=2))

    # Duplicate with small quota
    with pytest.raises(DuplicateProcessError):
        pt.create_process(pid=1, quota=ProcessQuota(min_frames=1))

    # Duplicate with quota exceeding capacity
    with pytest.raises(DuplicateProcessError):
        pt.create_process(pid=1, quota=ProcessQuota(min_frames=100))

    # Duplicate via register_process
    duplicate_pcb = ProcessControlBlock(pid=1, quota=ProcessQuota(min_frames=100))
    with pytest.raises(DuplicateProcessError):
        pt.register_process(duplicate_pcb)


def test_set_process_quota_and_mutation_safety():
    """Verify updating a process quota validates aggregate bounds without mutating on failure."""
    pt = ProcessTable(capacity=5)
    p1 = pt.create_process(pid=1, quota=ProcessQuota(min_frames=2))
    p2 = pt.create_process(pid=2, quota=ProcessQuota(min_frames=2))

    # Increase p2 from 2 to 3 (2 + 3 = 5 == capacity): valid
    pt.set_process_quota(2, ProcessQuota(min_frames=3))
    assert pt.get_process(2).quota.min_frames == 3

    # Attempt to increase p2 to 4 (2 + 4 = 6 > 5): invalid
    with pytest.raises(InvalidQuotaError) as exc_info:
        pt.set_process_quota(2, ProcessQuota(min_frames=4))
    assert "Aggregate minimum quota sum (6) exceeds physical capacity (5)" in str(exc_info.value)

    # Verification: p2 quota remains 3 (zero mutation on failure)
    assert pt.get_process(2).quota.min_frames == 3

    # Non-existent process raises ProcessNotFoundError
    with pytest.raises(ProcessNotFoundError):
        pt.set_process_quota(999, ProcessQuota(min_frames=1))

    # Invalid type raises TypeError
    with pytest.raises(TypeError):
        pt.set_process_quota(1, "invalid_quota")  # type: ignore


# =============================================================================
# 5. ProcessControlBlock local_policy Extension and PCB Reset
# =============================================================================

def test_pcb_local_policy_default_and_explicit_assignment():
    """Verify local_policy defaults to None and can be explicitly provided."""
    # Default is None
    pcb1 = ProcessControlBlock(pid=1)
    assert pcb1.local_policy is None

    # Explicit mock policy in constructor
    mock_policy = MagicMock()
    pcb2 = ProcessControlBlock(pid=2, local_policy=mock_policy)
    assert pcb2.local_policy is mock_policy

    # Direct attribute assignment
    pcb1.local_policy = mock_policy
    assert pcb1.local_policy is mock_policy

    # Pass through ProcessTable.create_process
    pt = ProcessTable(capacity=4)
    pcb3 = pt.create_process(pid=3, local_policy=mock_policy)
    assert pcb3.local_policy is mock_policy


def test_pcb_reset_behavior_with_local_policy():
    """
    Verify PCB.reset() calls local_policy.reset() when no resident pages exist,
    and raises InvalidProcessStateError without resetting local_policy if pages are resident.
    """
    mock_policy = MagicMock()
    pcb = ProcessControlBlock(pid=5, local_policy=mock_policy)

    # Case A: resident_count == 0 -> reset succeeds and triggers policy reset
    pcb.reset()
    assert mock_policy.reset.call_count == 1
    assert pcb.local_policy is mock_policy  # policy reference is retained
    assert pcb.state == ProcessState.READY

    # Case B: resident_count > 0 -> reset rejected, policy reset NOT called
    pcb.page_table.map_page(vpn=0, frame_id=1)
    assert pcb.resident_count == 1

    with pytest.raises(InvalidProcessStateError):
        pcb.reset()

    # Policy reset call count did not increase
    assert mock_policy.reset.call_count == 1

    # Case C: local_policy is None -> reset succeeds normally
    pcb.page_table.unmap_page(0)
    pcb.local_policy = None
    pcb.reset()
    assert pcb.local_policy is None


def test_policy_adapter_resets_all_seven_policy_types_safely():
    """
    Verify that PolicyAdapter exposes reset() and safely resets all 7 policy types
    through PCB.reset() without violating integration guards.
    """
    policies = [
        LRUCache(3),
        FIFOCache(3),
        LFUCache(3),
        MRUCache(3),
        ARCCache(3),
        CLOCKCache(3),
        OptimalCache(3),
    ]

    for policy in policies:
        ref_seq = [VirtualPage(1, 0), VirtualPage(1, 1)] if isinstance(policy, OptimalCache) else None
        adapter = PolicyAdapter(policy, capacity=3, reference_sequence=ref_seq)
        pcb = ProcessControlBlock(pid=1, local_policy=adapter)

        # Confirm adapter exposes reset()
        assert hasattr(adapter, "reset")
        assert callable(adapter.reset)

        # Reset via PCB
        pcb.reset()

        # Confirm pcb retains local_policy reference
        assert pcb.local_policy is adapter
        assert pcb.state == ProcessState.READY

        # For integrated ARC, CLOCK, and OPT, confirm integration mode is preserved
        if hasattr(policy, "_is_integrated"):
            assert policy._is_integrated is True


def test_policy_reset_failure_leaves_pcb_unmutated():
    """
    Verify that if local_policy.reset() raises an exception, the PCB's page table,
    metrics, and state remain completely unmutated (failure atomicity).
    """
    faulty_policy = MagicMock()
    faulty_policy.reset.side_effect = RuntimeError("Policy reset hardware fault")

    pcb = ProcessControlBlock(pid=42, local_policy=faulty_policy)
    pcb.metrics.requests = 100
    pcb.metrics.hits = 75
    pcb.metrics.misses = 25

    with pytest.raises(RuntimeError) as exc_info:
        pcb.reset()
    assert "Policy reset hardware fault" in str(exc_info.value)

    # PCB state must NOT have been reset
    assert pcb.metrics.requests == 100
    assert pcb.metrics.hits == 75
    assert pcb.metrics.misses == 25
    assert pcb.state == ProcessState.NEW


