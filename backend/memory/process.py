"""
Process and Address-Space Management Structures.
Defines ProcessControlBlock, ProcessState, ProcessQuota, and ProcessTable.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.memory.policy_adapter import PolicyAdapter

from backend.memory.exceptions import (
    DuplicateProcessError,
    InvalidAddressError,
    InvalidProcessStateError,
    InvalidQuotaError,
    ProcessNotFoundError,
    ProcessTerminatedError,
)
from backend.memory.page_table import PageTable
from backend.memory.process_metrics import ProcessMemoryMetrics


class ProcessState(Enum):
    """
    Operating system process lifecycle states.
    """
    NEW = "NEW"
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    TERMINATING = "TERMINATING"
    TERMINATION_FAILED = "TERMINATION_FAILED"
    TERMINATED = "TERMINATED"

    def is_active(self) -> bool:
        """Check if process is in an active (non-terminated) state."""
        return self in (ProcessState.NEW, ProcessState.READY, ProcessState.RUNNING, ProcessState.BLOCKED)

    def can_access_memory(self) -> bool:
        """Check if process is permitted to issue memory references."""
        return self in (ProcessState.READY, ProcessState.RUNNING, ProcessState.BLOCKED)

    def is_terminated(self) -> bool:
        """Check if process is permanently terminated."""
        return self == ProcessState.TERMINATED


VALID_TRANSITIONS: Dict[ProcessState, Set[ProcessState]] = {
    ProcessState.NEW: {ProcessState.READY, ProcessState.TERMINATING},
    ProcessState.READY: {ProcessState.RUNNING, ProcessState.TERMINATING},
    ProcessState.RUNNING: {ProcessState.READY, ProcessState.BLOCKED, ProcessState.TERMINATING},
    ProcessState.BLOCKED: {ProcessState.READY, ProcessState.RUNNING, ProcessState.TERMINATING},
    ProcessState.TERMINATING: {ProcessState.TERMINATED, ProcessState.TERMINATION_FAILED},
    ProcessState.TERMINATION_FAILED: {ProcessState.TERMINATING},
    ProcessState.TERMINATED: set(),
}


@dataclass
class ProcessQuota:
    """
    Individual process physical frame allocation constraints.
    
    Attributes:
      - min_frames: Guaranteed lower allocation reserve (integer >= 0).
      - max_frames: Hard allocation ceiling (integer >= 1 or None).
    """
    min_frames: int = 0
    max_frames: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.min_frames, int) or isinstance(self.min_frames, bool) or self.min_frames < 0:
            raise InvalidQuotaError(f"min_frames must be an integer >= 0, got {self.min_frames!r}")
            
        if self.max_frames is not None:
            if not isinstance(self.max_frames, int) or isinstance(self.max_frames, bool):
                raise InvalidQuotaError(f"max_frames must be an integer >= 1 or None, got {self.max_frames!r}")
            if self.max_frames < 1:
                raise InvalidQuotaError(f"max_frames must be >= 1, got {self.max_frames!r}")
            if self.max_frames < self.min_frames:
                raise InvalidQuotaError(
                    f"max_frames ({self.max_frames}) cannot be less than min_frames ({self.min_frames})."
                )


@dataclass
class ProcessControlBlock:
    """
    Process Control Block (PCB) representing a single process address space.
    
    Owns:
      - pid: Unique non-negative process identifier.
      - name: Human-readable diagnostic process label.
      - state: Current lifecycle state (ProcessState).
      - page_table: Dedicated per-process PageTable(pid).
      - metrics: Fine-grained per-process memory metrics.
      - quota: Process-specific frame quota limits.
      - reference_stream_metadata: Optional trace metadata (e.g. arrival time, priority).
      - local_policy: Optional per-process PolicyAdapter for local replacement scopes.
    """
    pid: int
    name: str = ""
    state: ProcessState = ProcessState.NEW
    quota: ProcessQuota = field(default_factory=ProcessQuota)
    metrics: ProcessMemoryMetrics = field(default_factory=ProcessMemoryMetrics)
    reference_stream_metadata: Optional[Dict[str, Any]] = None
    local_policy: Optional["PolicyAdapter"] = None
    page_table: PageTable = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.pid, int) or isinstance(self.pid, bool) or self.pid < 0:
            raise InvalidAddressError(f"Process ID (pid) must be an integer >= 0, got {self.pid!r}")

        if not self.name:
            self.name = f"Process_{self.pid}"

        if not isinstance(self.state, ProcessState):
            raise InvalidProcessStateError(f"Invalid process state: {self.state!r}")

        self.page_table = PageTable(self.pid)

    def transition_to(self, new_state: ProcessState) -> None:
        """
        Transition process to a new lifecycle state, validating allowed transitions.
        
        Raises:
            InvalidProcessStateError: If transition is illegal.
        """
        if not isinstance(new_state, ProcessState):
            raise InvalidProcessStateError(f"Target state must be a ProcessState enum, got {new_state!r}")

        allowed = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise InvalidProcessStateError(
                f"Illegal process state transition for PID {self.pid}: "
                f"{self.state.value} -> {new_state.value} is not permitted."
            )
        self.state = new_state

    def can_access_memory(self) -> bool:
        """Check if process is in a state permitted to perform memory references."""
        return self.state.can_access_memory()

    def assert_can_access_memory(self) -> None:
        """
        Assert process is legally capable of issuing memory accesses.
        
        Raises:
            ProcessTerminatedError: If process has terminated.
            InvalidProcessStateError: If process is in NEW, TERMINATING, or TERMINATION_FAILED.
        """
        if self.state == ProcessState.TERMINATED:
            raise ProcessTerminatedError(
                f"Process {self.pid} is TERMINATED and cannot perform memory accesses."
            )
        if not self.can_access_memory():
            raise InvalidProcessStateError(
                f"Process {self.pid} is in state {self.state.value} and cannot perform memory accesses."
            )

    @property
    def resident_count(self) -> int:
        """Number of virtual pages currently resident in physical memory."""
        return len(self.page_table.get_resident_pages())

    def force_emergency_terminate(self, reason: str = "emergency") -> None:
        """
        Emergency uncoordinated termination forcing state directly to TERMINATED.
        
        WARNING: This operation immediately transitions the process state to TERMINATED
        without performing frame reclamation, page table unmapping, backing store
        write-backs, or policy cleanup. It does NOT clear page-table mappings or reclaim
        physical frames. Resident pages remain tracked in the PCB's PageTable so that
        the coordinated memory management layer can safely detect and reclaim them during
        panic recovery.
        """
        self.state = ProcessState.TERMINATED

    def reset_metrics(self) -> None:
        """
        Reset process-local telemetry metrics to zero.
        Does not affect process state, quotas, or resident page table mappings.
        """
        self.metrics.reset()

    def reset(self) -> None:
        """
        Reset process metadata and metrics, returning process to READY state.
        
        Safety Invariant:
          A PCB does not own the central FrameAllocator or replacement-policy state.
          Silently discarding resident page-table mappings inside PCB.reset() would create
          orphaned physical frames and corrupted residency accounting across the system.
          
          Therefore, if any resident pages exist (resident_count > 0), reset() is rejected
          with InvalidProcessStateError. Resident pages must be properly reclaimed through
          coordinated MemoryManager routines before a full PCB reset can be performed.
        
        Raises:
            InvalidProcessStateError: If resident pages are still mapped to this process.
        """
        if self.resident_count > 0:
            raise InvalidProcessStateError(
                f"Cannot reset ProcessControlBlock for PID {self.pid}: {self.resident_count} pages "
                f"remain resident in physical memory. Full reset requires coordinated cleanup "
                f"through MemoryManager to prevent orphaned frames and policy desynchronization."
            )
        if self.local_policy is not None and hasattr(self.local_policy, "reset"):
            self.local_policy.reset()
        self.page_table.reset()
        self.metrics.reset()
        if self.state != ProcessState.TERMINATED:
            self.state = ProcessState.READY


_UNSET = object()


class ProcessTable:
    """
    Kernel Process Registry managing ProcessControlBlocks.
    
    Guarantees:
      - Unique PID enforcement.
      - Controlled lifecycle creation and termination.
      - Safe removal only after termination.
      - Active process enumeration.
      - Physical memory capacity bounds (C).
      - Aggregate minimum-quota validation (sum_{p in active} min_frames <= C).
    """

    def __init__(self, capacity: Optional[int] = _UNSET) -> None:
        if capacity is not _UNSET:
            if capacity is None or isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
                raise InvalidQuotaError(f"ProcessTable capacity must be a positive integer, got {capacity!r}")
            self._capacity: Optional[int] = capacity
        else:
            self._capacity: Optional[int] = None
        self._processes: Dict[int, ProcessControlBlock] = {}

    @property
    def capacity(self) -> Optional[int]:
        """Configured physical memory capacity (C), or None if unconstrained."""
        return self._capacity

    def _validate_aggregate_quota(self, candidate_min_frames: int, exclude_pid: Optional[int] = None) -> None:
        """
        Validate that adding candidate_min_frames will not cause aggregate non-terminated minimum
        quotas to exceed physical capacity C.
        
        Enforces: sum_{p in non-terminated processes} p.quota.min_frames <= C
        """
        if self._capacity is None:
            return

        current_sum = sum(
            p.quota.min_frames
            for p in self._processes.values()
            if p.state != ProcessState.TERMINATED and (exclude_pid is None or p.pid != exclude_pid)
        )
        new_sum = current_sum + candidate_min_frames
        if new_sum > self._capacity:
            raise InvalidQuotaError(
                f"Aggregate minimum quota sum ({new_sum}) exceeds physical capacity ({self._capacity})."
            )

    def create_process(
        self,
        pid: int,
        name: str = "",
        quota: Optional[ProcessQuota] = None,
        metadata: Optional[Dict[str, Any]] = None,
        local_policy: Optional["PolicyAdapter"] = None,
    ) -> ProcessControlBlock:
        """
        Create, register, and initialize a new process in READY state.
        
        Raises:
            DuplicateProcessError: If PID is already registered.
            InvalidAddressError: If PID is negative or not an integer.
            InvalidQuotaError: If quota bounds are invalid or aggregate min_frames exceeds physical capacity.
        """
        if pid in self._processes:
            raise DuplicateProcessError(f"Cannot create process: PID {pid} is already registered.")

        pcb = ProcessControlBlock(
            pid=pid,
            name=name,
            state=ProcessState.READY,
            quota=quota if quota is not None else ProcessQuota(),
            reference_stream_metadata=metadata,
            local_policy=local_policy,
        )
        self._validate_aggregate_quota(pcb.quota.min_frames)
        self._processes[pid] = pcb
        return pcb

    def register_process(self, pcb: ProcessControlBlock) -> ProcessControlBlock:
        """
        Register a pre-constructed ProcessControlBlock.
        
        Raises:
            TypeError: If pcb is not a ProcessControlBlock instance.
            DuplicateProcessError: If PID is already registered.
            InvalidQuotaError: If aggregate min_frames exceeds physical capacity.
        """
        if not isinstance(pcb, ProcessControlBlock):
            raise TypeError(f"Expected ProcessControlBlock instance, got {type(pcb).__name__}")

        if pcb.pid in self._processes:
            raise DuplicateProcessError(f"Cannot register process: PID {pcb.pid} is already registered.")

        candidate_min = pcb.quota.min_frames if pcb.state != ProcessState.TERMINATED else 0
        self._validate_aggregate_quota(candidate_min)

        self._processes[pcb.pid] = pcb
        return pcb

    def set_process_quota(self, pid: int, quota: ProcessQuota) -> None:
        """
        Update the quota for an existing registered process, validating aggregate constraints.
        
        Raises:
            ProcessNotFoundError: If PID is not found.
            TypeError: If quota is not a ProcessQuota instance.
            InvalidQuotaError: If new quota causes aggregate min_frames to exceed capacity.
        """
        pcb = self.get_process(pid)
        if not isinstance(quota, ProcessQuota):
            raise TypeError(f"Expected ProcessQuota instance, got {type(quota).__name__}")

        candidate_min = quota.min_frames if pcb.state != ProcessState.TERMINATED else 0
        self._validate_aggregate_quota(candidate_min, exclude_pid=pid)
        pcb.quota = quota

    def get_process(self, pid: int) -> ProcessControlBlock:
        """
        Retrieve ProcessControlBlock by PID.
        
        Raises:
            ProcessNotFoundError: If PID is not registered.
        """
        if pid not in self._processes:
            raise ProcessNotFoundError(f"Process with PID {pid} not found in process table.")
        return self._processes[pid]

    def has_process(self, pid: int) -> bool:
        """Check if PID is currently registered."""
        return pid in self._processes

    def get_active_processes(self) -> List[ProcessControlBlock]:
        """Return sorted list of all active (non-terminated) processes."""
        return sorted(
            [p for p in self._processes.values() if p.state.is_active()],
            key=lambda p: p.pid,
        )

    def get_all_processes(self) -> List[ProcessControlBlock]:
        """Return sorted list of all registered processes."""
        return sorted(list(self._processes.values()), key=lambda p: p.pid)

    def remove_process(self, pid: int) -> ProcessControlBlock:
        """
        Remove a process from the registry.
        Enforces lifecycle safety: process must be in TERMINATED state and have 0 resident pages.
        
        Raises:
            ProcessNotFoundError: If PID is not found.
            InvalidProcessStateError: If process is not TERMINATED or has resident pages remaining.
        """
        pcb = self.get_process(pid)
        if pcb.state != ProcessState.TERMINATED:
            raise InvalidProcessStateError(
                f"Cannot remove process {pid}: process is in state {pcb.state.value}. "
                f"Only TERMINATED processes can be removed from the process table."
            )
        if pcb.resident_count > 0:
            raise InvalidProcessStateError(
                f"Cannot remove process {pid}: process has {pcb.resident_count} resident page(s) mapped. "
                f"All resident pages must be unmapped and reclaimed before removing a process from the process table."
            )

        del self._processes[pid]
        return pcb

    @property
    def count(self) -> int:
        """Total number of registered processes (active and terminated)."""
        return len(self._processes)

    @property
    def active_count(self) -> int:
        """Total number of active (non-terminated) processes."""
        return len(self.get_active_processes())

    def reset(self) -> None:
        """
        Reset and clear all registered processes from the table.
        
        Safety Invariant:
          ProcessTable cannot silently discard active processes or processes that
          own physical memory frames. All registered processes must be in TERMINATED state
          with resident_count == 0 before ProcessTable.reset() is permitted.
          
        Raises:
            InvalidProcessStateError: If any registered process is not in TERMINATED state
                                      or has resident pages remaining.
        """
        non_terminated = [
            p.pid for p in self._processes.values() if p.state != ProcessState.TERMINATED
        ]
        if non_terminated:
            raise InvalidProcessStateError(
                f"Cannot reset ProcessTable while non-terminated processes exist: {non_terminated}. "
                f"All processes must be in TERMINATED state before ProcessTable can be reset."
            )

        with_resident_pages = [
            p.pid for p in self._processes.values() if p.resident_count > 0
        ]
        if with_resident_pages:
            raise InvalidProcessStateError(
                f"Cannot reset ProcessTable while processes with resident pages exist: {with_resident_pages}. "
                f"All resident pages must be unmapped and reclaimed before ProcessTable can be reset."
            )

        self._processes.clear()

    def __contains__(self, pid: int) -> bool:
        return pid in self._processes

    def __len__(self) -> int:
        return len(self._processes)

    def __repr__(self) -> str:
        cap_str = f", capacity={self._capacity}" if self._capacity is not None else ""
        return f"ProcessTable(total={self.count}, active={self.active_count}{cap_str})"

