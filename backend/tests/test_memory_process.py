"""
Unit and integration tests for Stage 12.2 Phase 4B:
Multi-process memory structures (ProcessControlBlock, ProcessState, ProcessQuota,
ProcessMemoryMetrics, ProcessTable, and PhysicalFrame ownership accessors).
"""
import pytest

from backend.memory.exceptions import (
    DuplicateProcessError,
    InvalidAddressError,
    InvalidProcessStateError,
    InvalidQuotaError,
    ProcessNotFoundError,
    ProcessTerminatedError,
)
from backend.memory.frame import PhysicalFrame
from backend.memory.page import VirtualPage
from backend.memory.process import (
    ProcessControlBlock,
    ProcessQuota,
    ProcessState,
    ProcessTable,
)
from backend.memory.process_metrics import ProcessMemoryMetrics


# =============================================================================
# 1. ProcessState and Transitions
# =============================================================================

def test_process_state_values_and_predicates():
    """Verify all 7 lifecycle states and their predicate methods."""
    states = {s.value for s in ProcessState}
    assert states == {
        "NEW",
        "READY",
        "RUNNING",
        "BLOCKED",
        "TERMINATING",
        "TERMINATION_FAILED",
        "TERMINATED",
    }

    # is_active
    assert ProcessState.NEW.is_active() is True
    assert ProcessState.READY.is_active() is True
    assert ProcessState.RUNNING.is_active() is True
    assert ProcessState.BLOCKED.is_active() is True
    assert ProcessState.TERMINATING.is_active() is False
    assert ProcessState.TERMINATION_FAILED.is_active() is False
    assert ProcessState.TERMINATED.is_active() is False

    # can_access_memory
    assert ProcessState.READY.can_access_memory() is True
    assert ProcessState.RUNNING.can_access_memory() is True
    assert ProcessState.BLOCKED.can_access_memory() is True
    assert ProcessState.NEW.can_access_memory() is False
    assert ProcessState.TERMINATING.can_access_memory() is False
    assert ProcessState.TERMINATION_FAILED.can_access_memory() is False
    assert ProcessState.TERMINATED.can_access_memory() is False

    # is_terminated
    assert ProcessState.TERMINATED.is_terminated() is True
    assert ProcessState.RUNNING.is_terminated() is False


def test_process_state_valid_lifecycle_transitions():
    """
    Verify permitted state transitions:
    READY / RUNNING / BLOCKED -> TERMINATING -> TERMINATED
    TERMINATING -> TERMINATION_FAILED -> TERMINATING -> TERMINATED
    """
    pcb = ProcessControlBlock(pid=1, state=ProcessState.NEW)

    pcb.transition_to(ProcessState.READY)
    assert pcb.state == ProcessState.READY

    pcb.transition_to(ProcessState.RUNNING)
    assert pcb.state == ProcessState.RUNNING

    pcb.transition_to(ProcessState.BLOCKED)
    assert pcb.state == ProcessState.BLOCKED

    pcb.transition_to(ProcessState.READY)
    assert pcb.state == ProcessState.READY

    # Normal termination through TERMINATING
    pcb.transition_to(ProcessState.TERMINATING)
    assert pcb.state == ProcessState.TERMINATING

    # Failure during termination cleanup
    pcb.transition_to(ProcessState.TERMINATION_FAILED)
    assert pcb.state == ProcessState.TERMINATION_FAILED

    # Retry termination cleanup
    pcb.transition_to(ProcessState.TERMINATING)
    assert pcb.state == ProcessState.TERMINATING

    # Successful termination
    pcb.transition_to(ProcessState.TERMINATED)
    assert pcb.state == ProcessState.TERMINATED


def test_process_state_termination_cannot_bypass_terminating():
    """
    Ordinary termination must not bypass TERMINATING:
    Direct transitions from NEW, READY, RUNNING, BLOCKED, or TERMINATION_FAILED to TERMINATED are rejected.
    """
    for initial in (ProcessState.NEW, ProcessState.READY, ProcessState.RUNNING, ProcessState.BLOCKED, ProcessState.TERMINATION_FAILED):
        pcb = ProcessControlBlock(pid=1, state=initial)
        with pytest.raises(InvalidProcessStateError) as exc_info:
            pcb.transition_to(ProcessState.TERMINATED)
        assert "Illegal process state transition" in str(exc_info.value)
        assert f"{initial.value} -> TERMINATED is not permitted" in str(exc_info.value)


def test_process_state_invalid_transitions():
    """Verify invalid transitions raise InvalidProcessStateError."""
    pcb = ProcessControlBlock(pid=1, state=ProcessState.NEW)

    # NEW cannot jump directly to RUNNING
    with pytest.raises(InvalidProcessStateError) as exc_info:
        pcb.transition_to(ProcessState.RUNNING)
    assert "Illegal process state transition" in str(exc_info.value)

    # TERMINATED has zero allowed outgoing transitions
    pcb.state = ProcessState.TERMINATED
    for target in (ProcessState.NEW, ProcessState.READY, ProcessState.RUNNING, ProcessState.TERMINATING):
        with pytest.raises(InvalidProcessStateError):
            pcb.transition_to(target)

    # Target must be a ProcessState enum
    pcb.state = ProcessState.READY
    with pytest.raises(InvalidProcessStateError):
        pcb.transition_to("RUNNING")  # type: ignore


def test_emergency_termination_operation():
    """Verify force_emergency_terminate immediately sets state to TERMINATED."""
    pcb = ProcessControlBlock(pid=1, state=ProcessState.RUNNING)
    pcb.force_emergency_terminate(reason="simulated_fatal_kernel_panic")
    assert pcb.state == ProcessState.TERMINATED
    assert pcb.can_access_memory() is False


# =============================================================================
# 2. ProcessQuota Validation
# =============================================================================

def test_process_quota_valid_initialization():
    """Verify legal ProcessQuota construction and default values."""
    q_default = ProcessQuota()
    assert q_default.min_frames == 0
    assert q_default.max_frames is None

    q_custom = ProcessQuota(min_frames=2, max_frames=8)
    assert q_custom.min_frames == 2
    assert q_custom.max_frames == 8

    # Boundary: min == max
    q_fixed = ProcessQuota(min_frames=4, max_frames=4)
    assert q_fixed.min_frames == 4
    assert q_fixed.max_frames == 4


def test_process_quota_validation_errors():
    """Verify invalid quota bounds raise InvalidQuotaError (and ValueError)."""
    assert issubclass(InvalidQuotaError, ValueError)

    # Negative min_frames
    with pytest.raises(InvalidQuotaError) as exc1:
        ProcessQuota(min_frames=-1)
    assert "min_frames must be an integer >= 0" in str(exc1.value)

    # max_frames == 0 disallowed
    with pytest.raises(InvalidQuotaError) as exc2:
        ProcessQuota(max_frames=0)
    assert "max_frames must be >= 1" in str(exc2.value)

    # Negative max_frames disallowed
    with pytest.raises(InvalidQuotaError):
        ProcessQuota(max_frames=-5)

    # max_frames < min_frames disallowed
    with pytest.raises(InvalidQuotaError) as exc3:
        ProcessQuota(min_frames=5, max_frames=3)
    assert "cannot be less than min_frames" in str(exc3.value)

    # Non-integer / boolean types
    with pytest.raises(InvalidQuotaError):
        ProcessQuota(min_frames=True)  # type: ignore
    with pytest.raises(InvalidQuotaError):
        ProcessQuota(max_frames=False)  # type: ignore
    with pytest.raises(InvalidQuotaError):
        ProcessQuota(min_frames="2")  # type: ignore
    with pytest.raises(InvalidQuotaError):
        ProcessQuota(max_frames="10")  # type: ignore

    # Compatibility: callers catching generic ValueError also catch InvalidQuotaError
    with pytest.raises(ValueError):
        ProcessQuota(min_frames=-10)


# =============================================================================
# 3. ProcessMemoryMetrics
# =============================================================================

def test_process_memory_metrics_lifecycle_and_reset():
    """Verify process memory metrics tracking, calculations, and pristine reset."""
    m = ProcessMemoryMetrics()

    # Initial zero values
    assert m.requests == 0
    assert m.hits == 0
    assert m.misses == 0
    assert m.hit_ratio == 0.0
    assert m.page_fault_rate == 0.0
    assert m.total_evictions == 0

    # Accumulate telemetry
    m.requests = 10
    m.hits = 7
    m.misses = 3
    m.pageins = 3
    m.writebacks = 1
    m.clean_evictions = 2
    m.dirty_evictions = 1
    m.resident_frames = 5
    m.evictions_suffered = 2
    m.evictions_caused = 1

    assert m.hit_ratio == 0.7
    assert m.page_fault_rate == 0.3
    assert m.total_evictions == 3

    # Reset
    m.reset()
    assert m.requests == 0
    assert m.hits == 0
    assert m.misses == 0
    assert m.pageins == 0
    assert m.writebacks == 0
    assert m.clean_evictions == 0
    assert m.dirty_evictions == 0
    assert m.resident_frames == 0
    assert m.evictions_suffered == 0
    assert m.evictions_caused == 0
    assert m.hit_ratio == 0.0
    assert m.page_fault_rate == 0.0


# =============================================================================
# 4. ProcessControlBlock (PCB)
# =============================================================================

def test_process_control_block_construction_and_defaults():
    """Verify ProcessControlBlock initialization, defaults, and dedicated page table."""
    pcb = ProcessControlBlock(pid=42)

    assert pcb.pid == 42
    assert pcb.name == "Process_42"
    assert pcb.state == ProcessState.NEW
    assert pcb.quota == ProcessQuota()
    assert pcb.page_table.pid == 42
    assert len(pcb.page_table) == 0
    assert pcb.resident_count == 0


def test_process_control_block_pid_validation():
    """Verify invalid process identifiers raise InvalidAddressError."""
    # Negative PID
    with pytest.raises(InvalidAddressError) as exc1:
        ProcessControlBlock(pid=-1)
    assert "pid" in str(exc1.value)

    # Boolean PID
    with pytest.raises(InvalidAddressError):
        ProcessControlBlock(pid=True)  # type: ignore

    # Non-integer PID
    with pytest.raises(InvalidAddressError):
        ProcessControlBlock(pid="10")  # type: ignore
    with pytest.raises(InvalidAddressError):
        ProcessControlBlock(pid=3.14)  # type: ignore


def test_independent_page_tables_and_identical_vpns():
    """Verify two processes accessing identical VPNs remain strictly isolated in distinct PageTables."""
    p1 = ProcessControlBlock(pid=1)
    p2 = ProcessControlBlock(pid=2)

    # Both processes map VPN 10, but to different physical frames
    p1.page_table.map_page(vpn=10, frame_id=0)
    p2.page_table.map_page(vpn=10, frame_id=5)

    assert p1.page_table.get_frame_id(10) == 0
    assert p2.page_table.get_frame_id(10) == 5

    assert p1.resident_count == 1
    assert p2.resident_count == 1

    # Unmapping in p1 has zero effect on p2
    p1.page_table.unmap_page(10)
    assert not p1.page_table.is_valid(10)
    assert p2.page_table.is_valid(10)
    assert p2.page_table.get_frame_id(10) == 5


def test_pcb_memory_access_permission_assertions():
    """Verify assert_can_access_memory checks permitted process states."""
    pcb = ProcessControlBlock(pid=1, state=ProcessState.READY)

    # READY, RUNNING, BLOCKED permitted
    pcb.assert_can_access_memory()

    pcb.state = ProcessState.RUNNING
    pcb.assert_can_access_memory()

    pcb.state = ProcessState.BLOCKED
    pcb.assert_can_access_memory()

    # NEW rejected
    pcb.state = ProcessState.NEW
    with pytest.raises(InvalidProcessStateError):
        pcb.assert_can_access_memory()

    # TERMINATING rejected
    pcb.state = ProcessState.TERMINATING
    with pytest.raises(InvalidProcessStateError):
        pcb.assert_can_access_memory()

    # TERMINATION_FAILED rejected
    pcb.state = ProcessState.TERMINATION_FAILED
    with pytest.raises(InvalidProcessStateError):
        pcb.assert_can_access_memory()

    # TERMINATED raises ProcessTerminatedError
    pcb.state = ProcessState.TERMINATED
    with pytest.raises(ProcessTerminatedError) as exc_info:
        pcb.assert_can_access_memory()
    assert "TERMINATED and cannot perform memory accesses" in str(exc_info.value)


def test_pcb_reset_rejects_resident_pages():
    """
    Verify PCB.reset() rejects reset when resident pages exist to prevent orphaned frames.
    Proves that resetting PCB cannot silently discard active resident mappings.
    """
    pcb = ProcessControlBlock(pid=5, state=ProcessState.RUNNING)
    pcb.page_table.map_page(vpn=1, frame_id=2)
    pcb.metrics.requests = 15
    pcb.metrics.hits = 10

    assert pcb.resident_count == 1

    # Attempting full reset with resident pages MUST raise InvalidProcessStateError
    with pytest.raises(InvalidProcessStateError) as exc_info:
        pcb.reset()
    assert "Cannot reset ProcessControlBlock for PID 5" in str(exc_info.value)
    assert "remain resident in physical memory" in str(exc_info.value)

    # Resident mappings and metrics are preserved intact
    assert pcb.resident_count == 1
    assert pcb.page_table.is_valid(1)
    assert pcb.metrics.requests == 15

    # Local metrics can be safely reset via reset_metrics()
    pcb.reset_metrics()
    assert pcb.metrics.requests == 0
    assert pcb.resident_count == 1

    # After coordinated unmapping (resident_count == 0), reset() succeeds
    pcb.page_table.unmap_page(1)
    assert pcb.resident_count == 0

    pcb.reset()
    assert pcb.state == ProcessState.READY
    assert pcb.resident_count == 0
    assert pcb.metrics.requests == 0


# =============================================================================
# 5. ProcessTable Registry
# =============================================================================

def test_process_table_create_and_lookup():
    """Verify process creation, registration, and lookup in ProcessTable."""
    pt = ProcessTable()
    assert pt.count == 0
    assert pt.active_count == 0

    quota = ProcessQuota(min_frames=1, max_frames=4)
    pcb = pt.create_process(pid=10, name="WorkerProcess", quota=quota)

    assert pcb.pid == 10
    assert pcb.name == "WorkerProcess"
    assert pcb.quota == quota
    assert pcb.state == ProcessState.READY

    assert pt.count == 1
    assert pt.active_count == 1
    assert pt.has_process(10) is True
    assert 10 in pt
    assert pt.get_process(10) is pcb


def test_process_table_duplicate_pid_rejection():
    """Verify duplicate PID creation and registration raises DuplicateProcessError."""
    pt = ProcessTable()
    pt.create_process(pid=1)

    with pytest.raises(DuplicateProcessError) as exc1:
        pt.create_process(pid=1)
    assert "PID 1 is already registered" in str(exc1.value)

    duplicate_pcb = ProcessControlBlock(pid=1)
    with pytest.raises(DuplicateProcessError) as exc2:
        pt.register_process(duplicate_pcb)
    assert "PID 1 is already registered" in str(exc2.value)


def test_process_table_not_found_rejection():
    """Verify querying an unregistered process raises ProcessNotFoundError."""
    pt = ProcessTable()
    with pytest.raises(ProcessNotFoundError) as exc_info:
        pt.get_process(999)
    assert "PID 999 not found" in str(exc_info.value)


def test_process_table_enumeration_and_active_filtering():
    """Verify get_active_processes filters out TERMINATED processes."""
    pt = ProcessTable()
    p1 = pt.create_process(pid=1)
    p2 = pt.create_process(pid=2)
    p3 = pt.create_process(pid=3)

    assert pt.count == 3
    assert pt.active_count == 3

    # Terminate process 2 via proper lifecycle: TERMINATING -> TERMINATED
    p2.transition_to(ProcessState.TERMINATING)
    p2.transition_to(ProcessState.TERMINATED)

    assert pt.count == 3
    assert pt.active_count == 2

    active = pt.get_active_processes()
    assert [p.pid for p in active] == [1, 3]

    all_procs = pt.get_all_processes()
    assert [p.pid for p in all_procs] == [1, 2, 3]


def test_process_table_lifecycle_safe_removal():
    """Verify removal is only permitted for processes in TERMINATED state."""
    pt = ProcessTable()
    p = pt.create_process(pid=7)

    # Attempting to remove an active (READY) process raises InvalidProcessStateError
    with pytest.raises(InvalidProcessStateError) as exc_active:
        pt.remove_process(7)
    assert "Only TERMINATED processes can be removed" in str(exc_active.value)

    # Attempting to remove non-existent process
    with pytest.raises(ProcessNotFoundError):
        pt.remove_process(999)

    # Terminate process cleanly
    p.transition_to(ProcessState.TERMINATING)
    p.transition_to(ProcessState.TERMINATED)

    # Safe removal
    removed = pt.remove_process(7)
    assert removed is p
    assert pt.count == 0
    assert not pt.has_process(7)


def test_process_table_reset_rejects_active_processes():
    """
    Verify ProcessTable.reset() rejects reset when active processes exist.
    Prevents silent removal of active processes while physical frames may be mapped.
    """
    pt = ProcessTable()
    p1 = pt.create_process(pid=1)
    p2 = pt.create_process(pid=2)

    assert len(pt) == 2

    # Reset with active processes MUST fail
    with pytest.raises(InvalidProcessStateError) as exc_info:
        pt.reset()
    assert "Cannot reset ProcessTable while non-terminated processes exist" in str(exc_info.value)
    assert len(pt) == 2

    # Terminate all processes through proper lifecycle
    p1.transition_to(ProcessState.TERMINATING)
    p1.transition_to(ProcessState.TERMINATED)
    p2.transition_to(ProcessState.TERMINATING)
    p2.transition_to(ProcessState.TERMINATED)

    # Reset now succeeds
    pt.reset()
    assert len(pt) == 0
    assert pt.count == 0


# =============================================================================
# 6. ProcessTable Resource Safety Audit Tests
# =============================================================================

def test_process_table_remove_terminated_with_resident_pages_rejected_and_registry_unchanged():
    """
    Verify ProcessTable.remove_process rejects removing a TERMINATED process if resident
    pages remain mapped. Verifies the process table registry is not altered on failure.
    """
    pt = ProcessTable()
    p = pt.create_process(pid=10, name="TerminatedWorker")
    p.page_table.map_page(vpn=1, frame_id=0)

    # Cleanly terminate process
    p.transition_to(ProcessState.TERMINATING)
    p.transition_to(ProcessState.TERMINATED)

    assert p.state == ProcessState.TERMINATED
    assert p.resident_count == 1

    # Attempting removal must fail because resident_count > 0
    with pytest.raises(InvalidProcessStateError) as exc_info:
        pt.remove_process(10)
    assert "resident page(s) mapped" in str(exc_info.value)
    assert "PID 10" in str(exc_info.value) or "process 10" in str(exc_info.value)

    # Registry must remain strictly unchanged on failure
    assert pt.count == 1
    assert pt.has_process(10) is True
    assert pt.get_process(10) is p

    # Once resident pages are unmapped (reclaimed), removal succeeds
    p.page_table.unmap_page(1)
    assert p.resident_count == 0

    removed = pt.remove_process(10)
    assert removed is p
    assert pt.count == 0
    assert not pt.has_process(10)


def test_process_table_reset_with_resident_pages_rejected_and_registry_unchanged():
    """
    Verify ProcessTable.reset rejects reset if any process (even if TERMINATED) has
    resident pages remaining. Verifies the registry is not partially cleared on failure.
    """
    pt = ProcessTable()
    p1 = pt.create_process(pid=1, name="Worker1")
    p2 = pt.create_process(pid=2, name="Worker2")

    # Map a page only in p1
    p1.page_table.map_page(vpn=0, frame_id=3)

    # Terminate both processes
    p1.transition_to(ProcessState.TERMINATING)
    p1.transition_to(ProcessState.TERMINATED)
    p2.transition_to(ProcessState.TERMINATING)
    p2.transition_to(ProcessState.TERMINATED)

    assert p1.resident_count == 1
    assert p2.resident_count == 0

    # Reset must fail because p1 still has resident frames
    with pytest.raises(InvalidProcessStateError) as exc_info:
        pt.reset()
    assert "processes with resident pages exist" in str(exc_info.value)
    assert "1" in str(exc_info.value)

    # Registry must be strictly unchanged (no partial clearing)
    assert len(pt) == 2
    assert pt.has_process(1) is True
    assert pt.has_process(2) is True

    # Once p1's resident pages are reclaimed, reset succeeds completely
    p1.page_table.unmap_page(0)
    assert p1.resident_count == 0

    pt.reset()
    assert len(pt) == 0
    assert pt.count == 0


def test_emergency_termination_preserves_page_table_and_frame_residency():
    """
    Verify emergency termination does not reclaim frames or clear page-table mappings,
    and that ProcessTable and PCB safety guards prevent removal or reset while pages are resident.
    """
    pt = ProcessTable()
    pcb = pt.create_process(pid=5, name="PanicProcess")

    frame = PhysicalFrame(frame_id=2)
    vpage = VirtualPage(pid=5, vpn=7)
    frame.assign_page(vpage, dirty=True, reference=True, data="important_kernel_data")
    pcb.page_table.map_page(vpn=7, frame_id=2)

    assert pcb.resident_count == 1
    assert frame.owner_pid == 5
    assert frame.vpn == 7

    # Perform emergency uncoordinated termination
    pcb.force_emergency_terminate(reason="unhandled_panic")

    # State is TERMINATED, but resident mappings and physical frame remain intact
    assert pcb.state == ProcessState.TERMINATED
    assert pcb.resident_count == 1
    assert pcb.page_table.is_valid(7) is True
    assert pcb.page_table.get_frame_id(7) == 2
    assert frame.is_allocated is True
    assert frame.owner_pid == 5
    assert frame.data == "important_kernel_data"

    # PCB reset must be rejected
    with pytest.raises(InvalidProcessStateError):
        pcb.reset()

    # ProcessTable removal must be rejected
    with pytest.raises(InvalidProcessStateError):
        pt.remove_process(5)

    # ProcessTable reset must be rejected
    with pytest.raises(InvalidProcessStateError):
        pt.reset()

    # Registry remains untouched
    assert pt.has_process(5) is True
    assert pt.count == 1

    # Simulate coordinated teardown by reclaiming frame and unmapping
    frame.clear_frame()
    pcb.page_table.unmap_page(7)
    assert pcb.resident_count == 0
    assert frame.is_free is True

    # Now safe removal succeeds
    removed = pt.remove_process(5)
    assert removed is pcb
    assert pt.count == 0


# =============================================================================
# 7. PhysicalFrame Ownership Accessors
# =============================================================================

def test_physical_frame_ownership_accessors():
    """Verify owner_pid and vpn properties on PhysicalFrame reflect resident VirtualPage."""
    frame = PhysicalFrame(frame_id=3)

    # Free frame
    assert frame.is_free is True
    assert frame.is_allocated is False
    assert frame.vpage is None
    assert frame.owner_pid is None
    assert frame.vpn is None

    # Assign page
    vpage = VirtualPage(pid=99, vpn=12)
    frame.assign_page(vpage, dirty=True, reference=True, data="data_payload")

    assert frame.is_allocated is True
    assert frame.vpage == vpage
    assert frame.owner_pid == 99
    assert frame.vpn == 12
    assert frame.dirty is True
    assert frame.reference_bit is True
    assert frame.data == "data_payload"

    # Clear frame
    frame.clear_frame()
    assert frame.is_free is True
    assert frame.vpage is None
    assert frame.owner_pid is None
    assert frame.vpn is None
    assert frame.dirty is False
    assert frame.reference_bit is False
    assert frame.data is None
