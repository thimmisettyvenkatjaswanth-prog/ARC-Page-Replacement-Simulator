"""
Operating System Memory Manager coordinating virtual memory, physical frames,
page tables, page replacement policies, and secondary backing store.
"""
from dataclasses import dataclass, field
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Union

from backend.memory.allocator import FrameAllocator
from backend.memory.backing_store import BackingStore
from backend.memory.exceptions import (
    BackingStoreError,
    ConcurrentTransactionError,
    CorruptedManagerStateError,
    DiskReadError,
    DiskWriteError,
    InvalidAddressError,
    InvalidFrameError,
    MemorySystemError,
    NoFreeFrameError,
)
from backend.memory.frame import PhysicalFrame
from backend.memory.page import VirtualPage
from backend.memory.page_table import PageTable
from backend.memory.policy_adapter import PolicyAdapter, VictimSelectionResult


@dataclass
class MemoryMetrics:
    """Cumulative operational metrics for the MemoryManager."""
    requests: int = 0
    hits: int = 0
    misses: int = 0
    successful_pageins: int = 0
    successful_writebacks: int = 0
    failed_pagefaults: int = 0
    successful_evictions: int = 0

    @property
    def hit_ratio(self) -> float:
        """Calculate hit ratio rounded to 4 decimal places."""
        if self.requests == 0:
            return 0.0
        return round(self.hits / self.requests, 4)


@dataclass
class AccessResult:
    """Result of a memory reference access request."""
    vpage: VirtualPage
    operation: str
    hit: bool
    frame_id: int
    evicted_page: Optional[VirtualPage] = None
    write_back_occurred: bool = False
    data: Any = None


class MemoryManager:
    """
    OS Memory Manager coordinating Virtual Memory translation and Physical Residency.
    
    Subsystems managed:
      - frames: Dict[int, PhysicalFrame] (authoritative hardware status)
      - page_tables: Dict[int, PageTable] (per-process virtual mapping)
      - allocator: FrameAllocator (free frame queue)
      - policy: PolicyAdapter (page replacement decision engine)
      - backing_store: BackingStore (simulated swap/secondary storage)
    
    Transactional Guarantees:
      - 3-Phase Staged Commit (Pre-validation -> Fallible I/O -> Protected Commit).
      - Strict preservation of dirty bits on failed write-backs.
      - Preservation of resident victim state on failed page-ins.
      - Concurrency and re-entrancy protection with panic fail-safe.
    """

    def __init__(
        self,
        capacity: int,
        algorithm: Union[str, Any] = "LRU",
        backing_store: Optional[BackingStore] = None,
        reference_sequence: Optional[Sequence[Any]] = None,
    ):
        """
        Initialize MemoryManager.
        
        Args:
            capacity: Total number of physical frames (integer >= 1).
            algorithm: Replacement algorithm name or policy instance.
            backing_store: Optional BackingStore instance.
            reference_sequence: Optional reference sequence for offline policies (e.g. OPT).
        """
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError(f"Capacity must be a positive integer >= 1, got {capacity!r}")

        self.capacity: int = capacity
        
        # Subsystems
        self.allocator: FrameAllocator = FrameAllocator(capacity)
        self.frames: Dict[int, PhysicalFrame] = {fid: PhysicalFrame(fid) for fid in range(capacity)}
        self.page_tables: Dict[int, PageTable] = {}
        self.backing_store: BackingStore = backing_store if backing_store is not None else BackingStore()
        self.policy: PolicyAdapter = PolicyAdapter(
            algorithm,
            capacity,
            get_frame_ref_bit=self._get_frame_ref_bit,
            set_frame_ref_bit=self._set_frame_reference_bit_internal,
            is_frame_allocated=self._is_frame_allocated,
            reference_sequence=reference_sequence,
        )
        
        # Telemetry
        self.metrics: MemoryMetrics = MemoryMetrics()
        
        # Concurrency & Consistency State
        self._lock: threading.RLock = threading.RLock()
        self._in_transaction: bool = False
        self.is_consistent: bool = True

    def get_page_table(self, pid: int) -> PageTable:
        """Retrieve or initialize the PageTable for the specified process ID."""
        if not isinstance(pid, int) or isinstance(pid, bool) or pid < 0:
            raise InvalidAddressError(f"Process ID must be a non-negative integer, got {pid!r}")

        if pid not in self.page_tables:
            self.page_tables[pid] = PageTable(pid)
        return self.page_tables[pid]

    def get_frame(self, frame_id: int) -> PhysicalFrame:
        """Get the PhysicalFrame for the given frame ID."""
        if not (0 <= frame_id < self.capacity):
            raise InvalidFrameError(f"Frame ID {frame_id} out of bounds [0, {self.capacity - 1}].")
        return self.frames[frame_id]

    def get_resident_pages(self, pid: Optional[int] = None) -> List[VirtualPage]:
        """Return a sorted list of all virtual pages currently resident in physical frames."""
        resident = []
        for frame in self.frames.values():
            if frame.is_allocated and frame.vpage is not None:
                if pid is None or frame.vpage.pid == pid:
                    resident.append(frame.vpage)
        return sorted(resident)

    def _get_frame_ref_bit(self, frame_id: int) -> bool:
        """Callback providing authoritative physical frame reference bit to policies."""
        if 0 <= frame_id < self.capacity:
            return self.frames[frame_id].reference_bit
        return False

    def _is_frame_allocated(self, frame_id: int) -> bool:
        """Callback checking if a physical frame is currently allocated/resident."""
        if 0 <= frame_id < self.capacity:
            return self.frames[frame_id].is_allocated
        return False

    # -------------------------------------------------------------------------
    # Controlled Hardware Mutators
    # -------------------------------------------------------------------------

    def set_frame_reference_bit(self, frame_id: int, value: bool) -> None:
        """
        Public, coordinated interface for external reference-bit modification.
        
        Guards against mutation during an active memory transaction.
        """
        with self._lock:
            if self._in_transaction:
                raise ConcurrentTransactionError(
                    f"External mutation rejected: MemoryManager is currently executing "
                    f"an active transaction. Frame {frame_id} cannot be modified externally."
                )
            self._set_frame_reference_bit_internal(frame_id, value)

    def _set_frame_reference_bit_internal(self, frame_id: int, value: bool) -> None:
        """Internal mutation path authorized for MemoryManager lifecycle."""
        frame = self.get_frame(frame_id)
        frame._set_reference_bit(value)
        if hasattr(self.policy.policy, "notify_frame_bit_changed"):
            self.policy.policy.notify_frame_bit_changed(frame_id)

    def set_frame_dirty(self, frame_id: int, value: bool) -> None:
        """
        Public, coordinated interface for external dirty-bit modification.
        
        Guards against mutation during an active memory transaction.
        """
        with self._lock:
            if self._in_transaction:
                raise ConcurrentTransactionError(
                    f"External dirty-bit mutation rejected: MemoryManager has an active transaction."
                )
            self._set_frame_dirty_internal(frame_id, value)

    def _set_frame_dirty_internal(self, frame_id: int, value: bool) -> None:
        """Internal mutation path authorized for MemoryManager lifecycle."""
        self.get_frame(frame_id)._set_dirty(value)

    # -------------------------------------------------------------------------
    # Core Memory Access Lifecycle (3-Phase Transaction)
    # -------------------------------------------------------------------------

    def access_page(
        self,
        pid: int,
        vpn: int,
        operation: str = "READ",
        write_data: Any = None
    ) -> AccessResult:
        """
        Process a memory access request following the 3-phase transaction model.
        
        Phase 1: Pure Read-Only Pre-Validation & Resolution
        Phase 2: Fallible External Operations (Write-Back & Page-In)
        Phase 3: Protected Commit Boundary (Hardware & Policy Mutation)
        
        Args:
            pid: Process ID requesting the memory reference.
            vpn: Virtual Page Number requested.
            operation: "READ" or "WRITE" (default "READ").
            write_data: Optional payload to store on write.
            
        Returns:
            AccessResult describing the access event, hit/fault status, and frame.
            
        Raises:
            CorruptedManagerStateError: If manager is in a panic state.
            ConcurrentTransactionError: If an overlapping access is attempted.
            BackingStoreError: If simulated disk I/O fails during Phase 2.
        """
        operation_upper = operation.upper()
        if operation_upper not in ("READ", "WRITE"):
            raise ValueError(f"Operation must be 'READ' or 'WRITE', got {operation!r}")

        vpage = VirtualPage(pid=pid, vpn=vpn)

        with self._lock:
            # Panic guard: reject all operations if state was previously corrupted
            if not self.is_consistent:
                raise CorruptedManagerStateError(
                    "MemoryManager is in an inconsistent panic state; accesses are blocked."
                )

            # Re-entrancy guard
            if self._in_transaction:
                raise ConcurrentTransactionError(
                    "Illegal re-entrant access detected: MemoryManager does not permit nested access_page calls."
                )

            self._in_transaction = True
            val_token: Optional[Any] = None
            victim_token: Optional[Any] = None
            try:
                # =============================================================
                # Phase 1: Pure Read-Only Pre-Validation & Resolution
                # =============================================================
                page_table = self.get_page_table(pid)
                is_hit = page_table.is_valid(vpn)
                
                # Policy request pre-validation
                val_token = self.policy.validate_request(vpage)

                target_fid: Optional[int] = None
                victim_vpage: Optional[VirtualPage] = None
                write_back_occurred: bool = False

                if is_hit:
                    target_fid = page_table.get_frame_id(vpn)
                else:
                    # Page Fault
                    policy_needs_eviction = (
                        hasattr(self.policy, "requires_eviction")
                        and self.policy.requires_eviction(vpage, val_token)
                    )
                    if not policy_needs_eviction and self.allocator.has_free_frame():
                        target_fid = self.allocator.peek_free_frame()
                    else:
                        # Physical memory is full or policy mandates eviction; select victim via policy
                        victim_result = self.policy.select_victim(self._get_frame_ref_bit, token=val_token)
                        if victim_result is None:
                            if self.allocator.has_free_frame():
                                target_fid = self.allocator.peek_free_frame()
                            else:
                                raise NoFreeFrameError("Policy returned None for victim selection while memory is full.")
                        else:
                            # Extract victim details depending on token vs direct page
                            if isinstance(victim_result, VictimSelectionResult):
                                victim_vpage = victim_result.victim
                                victim_token = victim_result.token if victim_result.token is not None else val_token
                                victim_pte = self.get_page_table(victim_vpage.pid).get_entry(victim_vpage.vpn)
                                target_fid = (
                                    victim_result.frame_id
                                    if victim_result.frame_id is not None
                                    else victim_pte.frame_id
                                )
                            elif hasattr(victim_result, "victim_page") and hasattr(victim_result, "victim_frame_id"):
                                victim_token = victim_result
                                victim_vpage = victim_result.victim_page
                                target_fid = victim_result.victim_frame_id
                            else:
                                victim_vpage = victim_result
                                victim_token = val_token
                                victim_pte = self.get_page_table(victim_vpage.pid).get_entry(victim_vpage.vpn)
                                target_fid = victim_pte.frame_id

                # =============================================================
                # Phase 2: Fallible External Operations (Pre-Commit)
                # =============================================================
                page_data: Any = None

                if not is_hit:
                    # Step 2a: Dirty Write-Back (if victim is modified)
                    if victim_vpage is not None:
                        victim_frame = self.frames[target_fid]
                        if victim_frame.dirty:
                            try:
                                self.backing_store.write_page(victim_vpage, victim_frame.data)
                                # Successful write-back clears dirty bit
                                victim_frame._set_dirty(False)
                                self.metrics.successful_writebacks += 1
                                write_back_occurred = True
                            except Exception as write_exc:
                                # Failed write-back leaves dirty set and aborts request cleanly
                                self.metrics.failed_pagefaults += 1
                                raise DiskWriteError(f"Failed write-back for victim {victim_vpage}") from write_exc

                    # Step 2b: Incoming Page-In from Backing Store
                    try:
                        page_data = self.backing_store.read_page(vpage)
                        self.metrics.successful_pageins += 1
                    except Exception as read_exc:
                        # Page-in failed: victim remains resident & clean (if written back); aborts cleanly
                        self.metrics.failed_pagefaults += 1
                        raise DiskReadError(f"Failed to read page {vpage} from backing store") from read_exc

                # =============================================================
                # Phase 3: Protected Commit Boundary (Hardware & Policy Mutation)
                # =============================================================
                commit_token = victim_token if victim_token is not None else val_token
                try:
                    if not is_hit:
                        # Miss Commit
                        if victim_vpage is not None:
                            # 1. Unmap victim in its process PageTable
                            victim_pt = self.get_page_table(victim_vpage.pid)
                            victim_pt.unmap_page(victim_vpage.vpn)

                            # 2. Evict victim in policy
                            self.policy.evict_victim(victim_vpage, commit_token)
                            self.metrics.successful_evictions += 1
                        else:
                            # 3. Permanently allocate free frame
                            self.allocator.allocate_frame()

                        # 4. Assign physical frame metadata
                        target_frame = self.frames[target_fid]
                        target_frame.assign_page(
                            vpage=vpage,
                            dirty=(operation_upper == "WRITE"),
                            reference=True,
                            data=write_data if operation_upper == "WRITE" else page_data
                        )

                        # 5. Map incoming page in PageTable
                        page_table.map_page(vpn=vpn, frame_id=target_fid)

                        # 6. Insert page into policy
                        self.policy.insert_page(vpage, target_fid, commit_token)
                    else:
                        # Hit Commit
                        target_frame = self.frames[target_fid]
                        target_frame._set_reference_bit(True)
                        if operation_upper == "WRITE":
                            target_frame._set_dirty(True)
                            if write_data is not None:
                                target_frame._data = write_data

                        # 7. Record hit in policy
                        self.policy.record_hit(vpage, commit_token)

                    # 8. Commit request advancement
                    self.policy.commit_request(commit_token, is_hit)

                    # 9. Update telemetry counters
                    self.metrics.requests += 1
                    if is_hit:
                        self.metrics.hits += 1
                    else:
                        self.metrics.misses += 1

                except Exception as commit_exc:
                    # Standard unexpected runtime error during commit marks panic state
                    self.is_consistent = False
                    raise CorruptedManagerStateError(
                        f"Fatal commit failure during access to page {vpage}; manager marked inconsistent."
                    ) from commit_exc
                except BaseException:
                    # Process-control interruption (KeyboardInterrupt, SystemExit) marks panic and re-raises
                    self.is_consistent = False
                    raise

                return AccessResult(
                    vpage=vpage,
                    operation=operation_upper,
                    hit=is_hit,
                    frame_id=target_fid,
                    evicted_page=victim_vpage,
                    write_back_occurred=write_back_occurred,
                    data=target_frame.data
                )

            except BaseException:
                if hasattr(self.policy, "abort_request"):
                    try:
                        abort_token = victim_token if victim_token is not None else val_token
                        self.policy.abort_request(abort_token)
                    except Exception:
                        pass
                raise
            finally:
                self._in_transaction = False

    def reset(self) -> None:
        """
        Perform an atomic, coordinated reset of all virtual and physical memory subsystems.
        """
        with self._lock:
            if self._in_transaction:
                raise ConcurrentTransactionError("Cannot reset MemoryManager while a transaction is active.")

            try:
                # 1. Reset all physical frames
                for frame in self.frames.values():
                    frame.clear_frame()

                # 2. Reset all page tables
                for pt in self.page_tables.values():
                    pt.reset()
                self.page_tables.clear()

                # 3. Reset frame allocator
                self.allocator.reset()

                # 4. Reset backing store
                self.backing_store.reset()

                # 5. Reset replacement policy
                self.policy.reset()

                # 6. Reset telemetry metrics
                self.metrics = MemoryMetrics()

                # 7. Restore consistency
                self.is_consistent = True
            except BaseException:
                self.is_consistent = False
                raise

    def __repr__(self) -> str:
        return (
            f"MemoryManager(capacity={self.capacity}, algorithm={self.policy.name}, "
            f"resident={len(self.get_resident_pages())}, requests={self.metrics.requests}, "
            f"hit_ratio={self.metrics.hit_ratio})"
        )

