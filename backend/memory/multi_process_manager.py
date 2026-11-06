"""
Multi-Process Memory Manager coordinating shared physical memory, process table,
per-process page tables, global replacement policies, and backing store.
Stage 12.2 Phase 4C: Multi-Process Virtual Memory Management.
"""
from dataclasses import dataclass
from enum import Enum
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Union

from backend.algorithms.arc import ARCCache
from backend.algorithms.clock import CLOCKCache
from backend.algorithms.opt import OptimalCache
from backend.memory.allocator import FrameAllocator
from backend.memory.backing_store import BackingStore
from backend.memory.exceptions import (
    BackingStoreError,
    ConcurrentTransactionError,
    CorruptedManagerStateError,
    DiskReadError,
    DiskWriteError,
    DuplicateProcessError,
    InvalidAddressError,
    InvalidFrameError,
    InvalidProcessStateError,
    InvalidQuotaError,
    ManagerPanicError,
    MemorySystemError,
    NoFreeFrameError,
    PageTableError,
    ProcessNotFoundError,
    ProcessTerminatedError,
    UnsupportedOperationError,
)
from backend.memory.frame import PhysicalFrame
from backend.memory.manager import AccessResult, MemoryManager, MemoryMetrics
from backend.memory.page import VirtualPage
from backend.memory.page_table import PageTable
from backend.memory.policy_adapter import PolicyAdapter, VictimSelectionResult
from backend.memory.process import (
    ProcessControlBlock,
    ProcessQuota,
    ProcessState,
    ProcessTable,
)


class ReplacementScope(Enum):
    """Memory allocation and replacement scope."""
    GLOBAL = "GLOBAL"
    LOCAL = "LOCAL"


@dataclass
class MultiProcessMemoryMetrics(MemoryMetrics):
    """
    Cumulative operational metrics for MultiProcessMemoryManager.
    Maintains strict mathematical reconciliation identities across triggers and scopes.
    """
    quota_ceiling_evictions: int = 0
    directory_mandated_evictions: int = 0
    physical_exhaustion_evictions: int = 0
    clean_evictions: int = 0
    dirty_evictions: int = 0
    cross_process_evictions: int = 0
    self_evictions: int = 0
    arc_ghost_prunes: int = 0

    def reset(self) -> None:
        """Reset all metrics counters to zero."""
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.successful_pageins = 0
        self.successful_writebacks = 0
        self.failed_pagefaults = 0
        self.successful_evictions = 0
        self.quota_ceiling_evictions = 0
        self.directory_mandated_evictions = 0
        self.physical_exhaustion_evictions = 0
        self.clean_evictions = 0
        self.dirty_evictions = 0
        self.cross_process_evictions = 0
        self.self_evictions = 0
        self.arc_ghost_prunes = 0


@dataclass
class MultiProcessAccessResult(AccessResult):
    """Extended access result for multi-process memory references."""
    eviction_trigger: Optional[str] = None
    also_quota_ceiling: bool = False
    also_directory_mandated: bool = False
    also_physical_exhausted: bool = False
    victim_process_id: Optional[int] = None


class MultiProcessMemoryManager(MemoryManager):
    """
    Multi-Process Memory Manager supporting Global (and Local) replacement scopes,
    per-process page tables, process control blocks, quota reservations, and failure-safe teardown.
    """

    def __init__(
        self,
        capacity: int,
        algorithm: Union[str, Any] = "LRU",
        backing_store: Optional[BackingStore] = None,
        reference_sequence: Optional[Sequence[Any]] = None,
        scope: ReplacementScope = ReplacementScope.GLOBAL,
    ):
        super().__init__(
            capacity=capacity,
            algorithm=algorithm,
            backing_store=backing_store,
            reference_sequence=reference_sequence,
        )
        self.scope: ReplacementScope = (
            scope if isinstance(scope, ReplacementScope) else ReplacementScope(scope)
        )
        self.process_table: ProcessTable = ProcessTable(capacity=capacity)
        self.metrics: MultiProcessMemoryMetrics = MultiProcessMemoryMetrics()
        self._reference_sequence: Optional[List[Any]] = (
            list(reference_sequence) if reference_sequence is not None else None
        )
        self._algorithm_spec: Union[str, Any] = algorithm
        self._trace_pids: Set[int] = set()
        if self._reference_sequence is not None:
            self._trace_pids = {
                getattr(v, "pid", None) for v in self._reference_sequence
            } - {None}

    def set_scope(self, scope: Union[str, ReplacementScope]) -> None:
        """
        Set replacement scope (GLOBAL or LOCAL).
        Switching scope on an active simulation without reset is strictly prohibited.
        """
        with self._lock:
            new_scope = scope if isinstance(scope, ReplacementScope) else ReplacementScope(scope)
            if new_scope == self.scope:
                return
            if self.process_table.count > 0 or self.allocator.free_count < self.capacity:
                raise UnsupportedOperationError(
                    "Cannot switch replacement scope while processes or allocated frames are active. "
                    "Coordinated reset() is required before changing scope."
                )
            self.scope = new_scope

    def _create_local_policy(
        self, pid: int, quota: Optional[ProcessQuota] = None
    ) -> PolicyAdapter:
        """
        Instantiate an independent PolicyAdapter for local replacement.
        Local capacity is set to min(quota.max_frames, physical_capacity) if finite, else physical_capacity.
        """
        local_cap = self.capacity
        if quota is not None and quota.max_frames is not None:
            local_cap = min(quota.max_frames, self.capacity)

        algo = self._algorithm_spec
        if isinstance(algo, str):
            algo_name = algo.upper()
        else:
            name_map = {
                "LRUCache": "LRU",
                "FIFOCache": "FIFO",
                "LFUCache": "LFU",
                "MRUCache": "MRU",
                "ARCCache": "ARC",
                "CLOCKCache": "CLOCK",
                "OptimalCache": "OPT",
            }
            algo_name = name_map.get(type(algo).__name__, type(algo).__name__).upper()

        if algo_name in ("OPT", "OPTIMAL"):
            if self._reference_sequence is None:
                raise ValueError("OPT replacement policy requires a reference_sequence.")
            local_seq = [v for v in self._reference_sequence if getattr(v, "pid", None) == pid]
            return PolicyAdapter(
                "OPT",
                capacity=local_cap,
                reference_sequence=local_seq,
            )
        elif algo_name == "CLOCK":
            return PolicyAdapter(
                "CLOCK",
                capacity=self.capacity,
                get_frame_ref_bit=self._get_frame_ref_bit,
                set_frame_ref_bit=self._set_frame_reference_bit_internal,
                is_frame_allocated=lambda fid, p=pid: (
                    self.frames[fid].is_allocated and self.frames[fid].owner_pid == p
                ),
            )
        elif algo_name == "ARC":
            return PolicyAdapter(
                "ARC",
                capacity=local_cap,
            )
        else:
            return PolicyAdapter(
                algo_name,
                capacity=local_cap,
            )

    # =========================================================================
    # Process Registry & Lifecycle Management
    # =========================================================================

    def create_process(
        self,
        pid: int,
        name: str = "",
        quota: Optional[ProcessQuota] = None,
        metadata: Optional[Dict[str, Any]] = None,
        local_policy: Optional[Any] = None,
    ) -> ProcessControlBlock:
        """Create, register, and initialize a new process in READY state."""
        with self._lock:
            eff_policy = local_policy
            if self.scope == ReplacementScope.LOCAL and eff_policy is None:
                eff_policy = self._create_local_policy(pid, quota)
            pcb = self.process_table.create_process(
                pid=pid,
                name=name,
                quota=quota,
                metadata=metadata,
                local_policy=eff_policy,
            )
            self.page_tables[pid] = pcb.page_table
            return pcb

    def register_process(self, pcb: ProcessControlBlock) -> ProcessControlBlock:
        """Register a pre-constructed ProcessControlBlock."""
        with self._lock:
            if self.scope == ReplacementScope.LOCAL and pcb.local_policy is None:
                pcb.local_policy = self._create_local_policy(pcb.pid, pcb.quota)
            res = self.process_table.register_process(pcb)
            self.page_tables[pcb.pid] = pcb.page_table
            return res

    def get_process(self, pid: int) -> ProcessControlBlock:
        """Retrieve ProcessControlBlock by PID."""
        return self.process_table.get_process(pid)

    def has_process(self, pid: int) -> bool:
        """Check if PID is registered."""
        return self.process_table.has_process(pid)

    def get_active_processes(self) -> List[ProcessControlBlock]:
        """Return list of all active processes."""
        return self.process_table.get_active_processes()

    def get_all_processes(self) -> List[ProcessControlBlock]:
        """Return list of all registered processes."""
        return self.process_table.get_all_processes()

    def set_process_quota(self, pid: int, quota: ProcessQuota) -> None:
        """Update quota for a process, validating aggregate capacity bounds."""
        with self._lock:
            self.process_table.set_process_quota(pid, quota)

    def get_page_table(self, pid: int) -> PageTable:
        """Retrieve the dedicated PageTable for the given process ID."""
        if not isinstance(pid, int) or isinstance(pid, bool) or pid < 0:
            raise InvalidAddressError(f"Process ID must be an integer >= 0, got {pid!r}")
        if not self.process_table.has_process(pid):
            raise ProcessNotFoundError(f"Process {pid} is not registered in ProcessTable.")
        return self.process_table.get_process(pid).page_table

    # =========================================================================
    # Quota-Eligibility Predicate Generator
    # =========================================================================

    def _make_eligibility_callback(
        self, req_pid: int, force_self_only: bool = False
    ) -> Callable[[Any], bool]:
        """
        Construct unified quota-eligibility predicate:
          IsEligible(V, P_req):
            - If V belongs to requester (V.pid == req_pid):
                Eligible if requester has at least one resident page to replace (resident_count > 0).
            - If force_self_only is True (e.g. requester at max_frames ceiling S-3):
                Foreign pages are strictly ineligible.
            - If V belongs to a foreign process (V.pid != req_pid):
                Eligible only if evicting it leaves the victim process at or above min_frames:
                victim_pcb.resident_count > victim_pcb.quota.min_frames
        """
        req_pcb = self.process_table.get_process(req_pid)

        def is_eligible(candidate: Any) -> bool:
            if candidate is None:
                return False
            c_pid = getattr(candidate, "pid", None)
            if c_pid is None:
                return False

            if c_pid == req_pid:
                return req_pcb.resident_count > 0

            if force_self_only:
                return False

            if not self.process_table.has_process(c_pid):
                return True

            foreign_pcb = self.process_table.get_process(c_pid)
            return foreign_pcb.resident_count > foreign_pcb.quota.min_frames

        return is_eligible

    # =========================================================================
    # Memory Access (3-Phase Staged Transaction with Overlap Classification)
    # =========================================================================

    def access_page(
        self,
        vpage: VirtualPage,
        operation: str = "READ",
        data: Any = None,
    ) -> MultiProcessAccessResult:
        """
        Execute memory reference access in a staged 3-phase transaction.
        Enforces Decision Matrix (S-1 to S-13) and Overlap Telemetry Hierarchy.
        """
        if not isinstance(vpage, VirtualPage):
            raise TypeError(f"Expected VirtualPage, got {type(vpage).__name__}")
        if operation not in ("READ", "WRITE"):
            raise ValueError(f"Invalid operation '{operation}'. Must be 'READ' or 'WRITE'.")

        with self._lock:
            if not self.is_consistent:
                raise CorruptedManagerStateError(
                    "MemoryManager is in an inconsistent state due to a previous unrecoverable error."
                )
            if self._in_transaction:
                raise ConcurrentTransactionError(
                    "Illegal re-entrant access detected: MemoryManager does not permit nested access_page calls."
                )

            self._in_transaction = True
            val_token: Optional[Any] = None
            victim_token: Optional[Any] = None

            try:
                # ---------------------------------------------------------
                # Phase 1: Pure Read-Only Pre-Validation & Resolution
                # ---------------------------------------------------------
                if self._trace_pids:
                    for tp in self._trace_pids:
                        if not self.process_table.has_process(tp):
                            raise UnsupportedOperationError(
                                f"Cannot execute request: PID {tp} appearing in OPT reference trace is not registered in ProcessTable."
                            )

                if not self.process_table.has_process(vpage.pid):
                    raise ProcessNotFoundError(f"Process {vpage.pid} not found in ProcessTable.")

                req_pcb = self.process_table.get_process(vpage.pid)
                req_pcb.assert_can_access_memory()

                active_policy = req_pcb.local_policy if self.scope == ReplacementScope.LOCAL else self.policy
                if active_policy is None:
                    active_policy = self._create_local_policy(vpage.pid, req_pcb.quota)
                    req_pcb.local_policy = active_policy

                page_table = req_pcb.page_table
                is_hit = page_table.is_valid(vpage.vpn)

                # Policy request pre-validation
                val_token = active_policy.validate_request(vpage)

                target_fid: Optional[int] = None
                victim_vpage: Optional[VirtualPage] = None
                victim_pcb: Optional[ProcessControlBlock] = None
                victim_result: Optional[Any] = None
                write_back_occurred: bool = False
                victim_frame_was_dirty: bool = False

                primary_trigger: Optional[str] = None
                also_quota_ceiling: bool = False
                also_directory_mandated: bool = False
                also_physical_exhausted: bool = False

                if is_hit:
                    # Scenario S-1: Cache hit
                    target_fid = page_table.get_frame_id(vpage.vpn)
                else:
                    # Page Fault: Evaluate Decision Matrix and Overlap Telemetry
                    is_quota_ceiling = (
                        req_pcb.quota.max_frames is not None
                        and req_pcb.resident_count >= req_pcb.quota.max_frames
                    )
                    is_arc = isinstance(getattr(active_policy, "policy", active_policy), ARCCache)
                    is_directory_mandated = (
                        is_arc
                        and hasattr(active_policy, "requires_eviction")
                        and active_policy.requires_eviction(vpage, val_token)
                    )
                    is_physical_exhausted = not self.allocator.has_free_frame()

                    needs_eviction = is_quota_ceiling or is_directory_mandated or is_physical_exhausted

                    if not needs_eviction:
                        # Scenarios S-2 / S-7: Allocate free frame without eviction
                        target_fid = self.allocator.peek_free_frame()
                    else:
                        # Eviction required: S-3, S-4, S-5, S-8, S-9, S-11, S-12
                        # Telemetry precedence: 1. Quota Ceiling > 2. Directory Mandated > 3. Physical Exhaustion
                        if is_quota_ceiling:
                            primary_trigger = "PRIMARY_QUOTA_CEILING"
                            also_directory_mandated = bool(is_directory_mandated)
                            also_physical_exhausted = bool(is_physical_exhausted)
                            force_self_only = True
                        elif is_directory_mandated:
                            primary_trigger = "PRIMARY_DIRECTORY_MANDATED"
                            also_quota_ceiling = False
                            also_physical_exhausted = bool(is_physical_exhausted)
                            force_self_only = (self.scope == ReplacementScope.LOCAL)
                        else:
                            primary_trigger = "PRIMARY_PHYSICAL_EXHAUSTION"
                            also_quota_ceiling = False
                            also_directory_mandated = False
                            force_self_only = (self.scope == ReplacementScope.LOCAL)

                        if self.scope == ReplacementScope.LOCAL:
                            force_self_only = True

                        is_eligible_cb = self._make_eligibility_callback(
                            vpage.pid, force_self_only=force_self_only
                        )
                        victim_result = active_policy.select_victim(
                            self._get_frame_ref_bit,
                            token=val_token,
                            is_eligible_cb=is_eligible_cb,
                        )

                        if victim_result is None:
                            # S-6 (starvation), S-12 (local OOM), or S-13 (universal catch-all)
                            active_policy.abort_request(val_token)
                            raise NoFreeFrameError(
                                f"No eligible victim page available for eviction in process {vpage.pid}."
                            )

                        if isinstance(victim_result, VictimSelectionResult):
                            victim_vpage = victim_result.victim
                            victim_token = (
                                victim_result.token if victim_result.token is not None else val_token
                            )
                            victim_pcb = self.process_table.get_process(victim_vpage.pid)
                            victim_pte = victim_pcb.page_table.get_entry(victim_vpage.vpn)
                            target_fid = (
                                victim_result.frame_id
                                if victim_result.frame_id is not None
                                else victim_pte.frame_id
                            )
                        elif hasattr(victim_result, "victim_page") and hasattr(victim_result, "victim_frame_id"):
                            victim_token = victim_result
                            victim_vpage = victim_result.victim_page
                            target_fid = victim_result.victim_frame_id
                            victim_pcb = self.process_table.get_process(victim_vpage.pid)
                        else:
                            victim_vpage = victim_result
                            victim_token = val_token
                            victim_pcb = self.process_table.get_process(victim_vpage.pid)
                            victim_pte = victim_pcb.page_table.get_entry(victim_vpage.vpn)
                            target_fid = victim_pte.frame_id

                # ---------------------------------------------------------
                # Phase 2: Fallible External Operations (Pre-Commit)
                # ---------------------------------------------------------
                page_data: Any = None

                if not is_hit:
                    # Step 2a: Dirty Write-Back
                    if victim_vpage is not None:
                        victim_frame = self.frames[target_fid]
                        victim_frame_was_dirty = victim_frame.dirty
                        if victim_frame.dirty:
                            try:
                                self.backing_store.write_page(victim_vpage, victim_frame.data)
                                victim_frame._set_dirty(False)
                                write_back_occurred = True
                            except Exception as exc:
                                active_policy.abort_request(victim_token)
                                self.metrics.failed_pagefaults += 1
                                raise DiskWriteError(
                                    f"Dirty write-back failed for {victim_vpage}: {exc}"
                                ) from exc

                    # Step 2b: Incoming Page-In
                    try:
                        page_data = self.backing_store.read_page(vpage)
                    except Exception as exc:
                        active_policy.abort_request(victim_token)
                        self.metrics.failed_pagefaults += 1
                        raise DiskReadError(
                            f"Page-in read failed for {vpage}: {exc}"
                        ) from exc

                # ---------------------------------------------------------
                # Phase 3: Protected Commit Boundary (Zero-Fail Mutation)
                # ---------------------------------------------------------
                try:
                    if not is_hit:
                        if victim_vpage is not None:
                            # Step 3a: Victim unmapping & policy eviction
                            victim_pcb.page_table.unmap_page(victim_vpage.vpn)
                            active_policy.evict_victim(victim_result, victim_token)
                        else:
                            # Free frame allocation
                            allocated_fid = self.allocator.allocate_frame()
                            if allocated_fid != target_fid:
                                target_fid = allocated_fid

                        # Step 3b: Frame assignment
                        frame = self.frames[target_fid]
                        frame_data = data if operation == "WRITE" else page_data
                        frame.assign_page(vpage, data=frame_data)
                        frame._set_dirty(operation == "WRITE")
                        frame._set_reference_bit(True)

                        # Step 3c: Requester mapping
                        req_pcb.page_table.map_page(vpage.vpn, frame_id=target_fid)

                        # Step 3d: Policy insertion & commit
                        active_policy.insert_page(vpage, frame_id=target_fid, token=victim_token)
                        active_policy.commit_request(victim_token, is_hit=False)

                        # Step 3e: Telemetry Attribution
                        req_pcb.metrics.requests += 1
                        req_pcb.metrics.misses += 1
                        req_pcb.metrics.pageins += 1
                        req_pcb.metrics.resident_frames = req_pcb.resident_count

                        self.metrics.requests += 1
                        self.metrics.misses += 1
                        self.metrics.successful_pageins += 1

                        if write_back_occurred:
                            self.metrics.successful_writebacks += 1
                            if victim_pcb is not None:
                                victim_pcb.metrics.writebacks += 1

                        # ARC ghost prune detection (Operation A)
                        if getattr(val_token, "ghost_evicted", None) is not None or getattr(victim_token, "ghost_evicted", None) is not None:
                            self.metrics.arc_ghost_prunes += 1

                        if victim_vpage is not None:
                            self.metrics.successful_evictions += 1
                            if victim_pcb is not None:
                                victim_pcb.metrics.resident_frames = victim_pcb.resident_count

                            # Primary trigger telemetry
                            if primary_trigger == "PRIMARY_QUOTA_CEILING":
                                self.metrics.quota_ceiling_evictions += 1
                            elif primary_trigger == "PRIMARY_DIRECTORY_MANDATED":
                                self.metrics.directory_mandated_evictions += 1
                            elif primary_trigger == "PRIMARY_PHYSICAL_EXHAUSTION":
                                self.metrics.physical_exhaustion_evictions += 1

                            # State cleanliness
                            if victim_frame_was_dirty:
                                self.metrics.dirty_evictions += 1
                                if victim_pcb is not None:
                                    victim_pcb.metrics.dirty_evictions += 1
                            else:
                                self.metrics.clean_evictions += 1
                                if victim_pcb is not None:
                                    victim_pcb.metrics.clean_evictions += 1

                            # Cross-process vs self-eviction
                            if victim_vpage.pid != vpage.pid:
                                self.metrics.cross_process_evictions += 1
                                req_pcb.metrics.evictions_caused += 1
                                if victim_pcb is not None:
                                    victim_pcb.metrics.evictions_suffered += 1
                            else:
                                self.metrics.self_evictions += 1

                    else:
                        # Hit Commit
                        frame = self.frames[target_fid]
                        frame._set_reference_bit(True)
                        if operation == "WRITE":
                            frame._set_dirty(True)
                            frame.store_data(data)

                        active_policy.record_hit(vpage, token=val_token)
                        active_policy.commit_request(val_token, is_hit=True)

                        req_pcb.metrics.requests += 1
                        req_pcb.metrics.hits += 1
                        self.metrics.requests += 1
                        self.metrics.hits += 1

                except Exception as fatal_exc:
                    self.is_consistent = False
                    raise CorruptedManagerStateError(
                        f"Catastrophic failure during commit boundary: {fatal_exc}"
                    ) from fatal_exc

                return MultiProcessAccessResult(
                    vpage=vpage,
                    operation=operation,
                    hit=is_hit,
                    frame_id=target_fid,
                    evicted_page=victim_vpage,
                    write_back_occurred=write_back_occurred,
                    data=data if operation == "WRITE" else (page_data if not is_hit else self.frames[target_fid].data),
                    eviction_trigger=primary_trigger,
                    also_quota_ceiling=also_quota_ceiling,
                    also_directory_mandated=also_directory_mandated,
                    also_physical_exhausted=also_physical_exhausted,
                    victim_process_id=victim_vpage.pid if victim_vpage is not None else None,
                )

            finally:
                self._in_transaction = False

    # =========================================================================
    # Process Termination & Coordinated Teardown (6-Stage Protocol)
    # =========================================================================

    def terminate_process(self, pid: int, flush_dirty: bool = True) -> None:
        """
        Unified 6-stage lifecycle process termination.
        Performs coordinated teardown for CLOCK and general policies.
        """
        with self._lock:
            if not self.is_consistent:
                raise CorruptedManagerStateError("Cannot terminate process: MemoryManager state is corrupted.")

            pcb = self.process_table.get_process(pid)

            if pcb.state == ProcessState.TERMINATED:
                return  # Idempotent no-op

            active_policy = pcb.local_policy if self.scope == ReplacementScope.LOCAL else self.policy
            is_clock = isinstance(getattr(active_policy, "policy", active_policy), CLOCKCache)

            if is_clock:
                self._coordinated_clock_process_teardown(pcb)
            else:
                self._coordinated_general_process_teardown(pcb, flush_dirty=flush_dirty)

    def _coordinated_clock_process_teardown(self, pcb: ProcessControlBlock) -> None:
        """
        Manager-owned atomic teardown for processes under integrated CLOCK.
        Audit frame/PTE/allocator/CLOCK invariants, write dirty frames, clear slots,
        unmap PTEs, free frames to allocator, and transition to TERMINATED.
        """
        active_policy = pcb.local_policy if self.scope == ReplacementScope.LOCAL else self.policy
        clock: CLOCKCache = active_policy.policy

        # ---------------------------------------------------------
        # STAGE 1: Read-Only Preflight Invariant Validation
        # ---------------------------------------------------------
        if self._in_transaction:
            raise UnsupportedOperationError(
                f"Cannot terminate process {pcb.pid}: MemoryManager transaction is currently active."
            )
        if active_policy._pending_token is not None:
            raise UnsupportedOperationError(
                f"Cannot terminate process {pcb.pid}: PolicyAdapter has an active pending token."
            )
        if getattr(clock, "_pending_token", None) is not None:
            raise UnsupportedOperationError(
                f"Cannot terminate process {pcb.pid}: CLOCK pending token is active."
            )
        if getattr(clock, "_active_commit_token", None) is not None:
            raise UnsupportedOperationError(
                f"Cannot terminate process {pcb.pid}: CLOCK active commit token session is active."
            )

        owned_frames = [f for f in self.frames.values() if f.is_allocated and f.owner_pid == pcb.pid]
        resident_vpns = set(pcb.page_table.get_resident_pages())

        if len(owned_frames) != len(resident_vpns):
            self.is_consistent = False
            raise CorruptedManagerStateError(
                f"Pre-teardown audit failed: {len(owned_frames)} allocated frames vs "
                f"{len(resident_vpns)} PCB resident pages for PID {pcb.pid}."
            )

        # Direction 1: Audit all allocated frames owned by PID
        for frame in owned_frames:
            slot = frame.frame_id
            vpage = frame.vpage
            vpn = frame.vpn

            if vpn not in resident_vpns:
                self.is_consistent = False
                raise CorruptedManagerStateError(f"Frame {slot} contains VPN {vpn} not marked resident in page table.")
            if vpage not in clock.page_to_slot or clock.page_to_slot[vpage] != slot:
                self.is_consistent = False
                raise CorruptedManagerStateError(f"CLOCK page_to_slot mismatch for {vpage}: expected slot {slot}.")
            if clock.slots[slot] != vpage:
                self.is_consistent = False
                raise CorruptedManagerStateError(f"CLOCK slot {slot} mismatch: expected {vpage}, found {clock.slots[slot]}.")
            if not self.allocator.is_allocated(slot):
                self.is_consistent = False
                raise CorruptedManagerStateError(f"Allocator invariant violated: frame {slot} marked free in allocator.")
            if not pcb.page_table.is_valid(vpn) or pcb.page_table.get_frame_id(vpn) != slot:
                self.is_consistent = False
                raise CorruptedManagerStateError(f"PTE mismatch for VPN {vpn}: frame={pcb.page_table.get_frame_id(vpn)}.")

        # Direction 2: Audit all CLOCK slots and mappings owned by PID
        for slot_idx, slot_vpage in enumerate(clock.slots):
            if slot_vpage is not None and getattr(slot_vpage, "pid", None) == pcb.pid:
                if slot_vpage not in clock.page_to_slot or clock.page_to_slot[slot_vpage] != slot_idx:
                    self.is_consistent = False
                    raise CorruptedManagerStateError(f"CLOCK slot {slot_idx} holds {slot_vpage} but page_to_slot disagrees.")
                frame = self.frames[slot_idx]
                if not frame.is_allocated or frame.vpage != slot_vpage:
                    self.is_consistent = False
                    raise CorruptedManagerStateError(f"CLOCK slot {slot_idx} allocated in CLOCK but not in PhysicalFrame.")
                if not self.allocator.is_allocated(slot_idx):
                    self.is_consistent = False
                    raise CorruptedManagerStateError(f"CLOCK slot {slot_idx} allocated in CLOCK but marked free in allocator.")

        # ---------------------------------------------------------
        # STAGE 2: Transition PCB to TERMINATING
        # ---------------------------------------------------------
        pcb.transition_to(ProcessState.TERMINATING)

        # ---------------------------------------------------------
        # STAGE 3: Fallible Storage Write-Back
        # ---------------------------------------------------------
        for frame in owned_frames:
            if frame.dirty:
                try:
                    self.backing_store.write_page(frame.vpage, frame.data)
                    frame._set_dirty(False)
                except DiskWriteError as e:
                    pcb.transition_to(ProcessState.TERMINATION_FAILED)
                    raise DiskWriteError(f"Termination write-back failed for PID {pcb.pid}: {e}") from e

        # ---------------------------------------------------------
        # STAGE 5: Destructive In-Memory Teardown (Protected Lockstep)
        # ---------------------------------------------------------
        try:
            for frame in owned_frames:
                slot = frame.frame_id
                vpage = frame.vpage
                vpn = frame.vpn

                # Step 5a: Clear CLOCK slot and mapping
                clock.slots[slot] = None
                if vpage in clock.page_to_slot:
                    del clock.page_to_slot[vpage]

                # Step 5b: Clear physical frame
                frame.clear_frame()

                # Step 5c: Invalidate PTE
                pcb.page_table.unmap_page(vpn)

                # Step 5d: Release frame to allocator pool
                self.allocator.free_frame(slot)

            # Step 5e: Monotonically advance CLOCK version
            clock.state_version += 1

            if self.scope == ReplacementScope.LOCAL and pcb.local_policy is not None:
                pcb.local_policy.reset()

            # ---------------------------------------------------------
            # STAGE 6: Final Lifecycle Transition to TERMINATED
            # ---------------------------------------------------------
            pcb.transition_to(ProcessState.TERMINATED)

        except Exception as fatal_exc:
            self.is_consistent = False
            raise ManagerPanicError(f"Catastrophic failure during CLOCK teardown: {fatal_exc}") from fatal_exc

    def _coordinated_general_process_teardown(
        self, pcb: ProcessControlBlock, flush_dirty: bool = True
    ) -> None:
        """
        Manager-owned atomic teardown for processes under general policies (ARC, OPT, LRU, FIFO, LFU, MRU).
        """
        active_policy = pcb.local_policy if self.scope == ReplacementScope.LOCAL else self.policy
        policy_inst = getattr(active_policy, "policy", None)

        # ---------------------------------------------------------
        # STAGE 1: Read-Only Preflight Invariant Validation
        # ---------------------------------------------------------
        if self._in_transaction:
            raise UnsupportedOperationError(
                f"Cannot terminate process {pcb.pid}: MemoryManager transaction is currently active."
            )
        if active_policy._pending_token is not None:
            raise UnsupportedOperationError(
                f"Cannot terminate process {pcb.pid}: PolicyAdapter has an active pending token."
            )

        # ARC transaction checks
        if hasattr(policy_inst, "_pending_plan") and policy_inst._pending_plan is not None:
            raise UnsupportedOperationError(
                f"Cannot terminate process {pcb.pid}: ARC pending plan is active ({policy_inst._pending_plan.case})."
            )
        if hasattr(policy_inst, "_active_commit_plan") and policy_inst._active_commit_plan is not None:
            raise UnsupportedOperationError(
                f"Cannot terminate process {pcb.pid}: ARC commit plan session is active."
            )

        # OPT transaction & remaining references checks
        if isinstance(policy_inst, OptimalCache):
            if getattr(policy_inst, "_pending_token", None) is not None:
                raise UnsupportedOperationError(
                    f"Cannot terminate process {pcb.pid}: OPT pending token is active."
                )
            if getattr(policy_inst, "_active_commit_token", None) is not None:
                raise UnsupportedOperationError(
                    f"Cannot terminate process {pcb.pid}: OPT active commit token session is active."
                )
            if self.scope == ReplacementScope.GLOBAL:
                if policy_inst.has_pending_references_for_pid(pcb.pid):
                    remaining_refs = policy_inst.get_pending_references_for_pid(pcb.pid)
                    raise InvalidProcessStateError(
                        f"Cannot terminate process {pcb.pid}: {len(remaining_refs)} unconsumed future references exist in Global OPT trace."
                    )

        owned_frames = [f for f in self.frames.values() if f.is_allocated and f.owner_pid == pcb.pid]
        resident_vpns = set(pcb.page_table.get_resident_pages())

        if len(owned_frames) != len(resident_vpns):
            self.is_consistent = False
            raise CorruptedManagerStateError(
                f"Pre-teardown audit failed: {len(owned_frames)} allocated frames vs "
                f"{len(resident_vpns)} PCB resident pages for PID {pcb.pid}."
            )

        # ---------------------------------------------------------
        # STAGE 2: Transition PCB to TERMINATING
        # ---------------------------------------------------------
        pcb.transition_to(ProcessState.TERMINATING)

        # ---------------------------------------------------------
        # STAGE 3: Fallible Storage Write-Back
        # ---------------------------------------------------------
        if flush_dirty:
            for frame in owned_frames:
                if frame.dirty:
                    try:
                        self.backing_store.write_page(frame.vpage, frame.data)
                        frame._set_dirty(False)
                    except DiskWriteError as e:
                        pcb.transition_to(ProcessState.TERMINATION_FAILED)
                        raise DiskWriteError(f"Termination write-back failed for PID {pcb.pid}: {e}") from e

        # ---------------------------------------------------------
        # STAGE 5: Destructive In-Memory Teardown
        # ---------------------------------------------------------
        try:
            if self.scope == ReplacementScope.GLOBAL:
                self.policy.purge_process(pcb.pid)
            elif pcb.local_policy is not None:
                pcb.local_policy.reset()

            for frame in owned_frames:
                slot = frame.frame_id
                vpn = frame.vpn

                frame.clear_frame()
                pcb.page_table.unmap_page(vpn)
                self.allocator.free_frame(slot)

            # ---------------------------------------------------------
            # STAGE 6: Final Lifecycle Transition to TERMINATED
            # ---------------------------------------------------------
            pcb.transition_to(ProcessState.TERMINATED)

        except Exception as fatal_exc:
            self.is_consistent = False
            raise ManagerPanicError(f"Catastrophic failure during general teardown: {fatal_exc}") from fatal_exc

    # =========================================================================
    # Coordinated Multi-Process Reset
    # =========================================================================

    def reset(self) -> None:
        """
        Coordinated reset of memory manager and all registered process structures.
        """
        with self._lock:
            self.allocator.reset()
            for frame in self.frames.values():
                frame.clear_frame()
            self.policy.reset()
            self.metrics.reset()
            self._in_transaction = False
            self.is_consistent = True

            for pcb in self.process_table.get_all_processes():
                pcb.page_table.reset()
                pcb.metrics.reset()
                if pcb.local_policy is not None and hasattr(pcb.local_policy, "reset"):
                    pcb.local_policy.reset()
                pcb.state = ProcessState.TERMINATED

            self.process_table.reset()
            self.page_tables.clear()
