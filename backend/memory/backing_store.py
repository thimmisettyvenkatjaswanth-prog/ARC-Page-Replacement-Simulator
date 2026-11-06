"""
Simulated Backing Store (Swap / Secondary Storage) for Virtual Memory Management.
"""
from typing import Any, Callable, Dict, Optional, Set

from backend.memory.exceptions import (
    DiskReadError,
    DiskWriteError,
    PageNotFoundError,
)
from backend.memory.page import VirtualPage


class BackingStore:
    """
    Simulated secondary storage device for memory paging.
    
    Provides:
      - Page storage indexed by VirtualPage identity.
      - Simulated read_page() and write_page() operations.
      - Error injection hooks for verifying transaction resilience.
      - Full telemetry tracking for reads, writes, and simulated disk I/O errors.
    """

    def __init__(
        self,
        auto_populate: bool = True,
        default_factory: Optional[Callable[[VirtualPage], Any]] = None
    ):
        """
        Initialize simulated backing store.
        
        Args:
            auto_populate: If True, uninitialized pages generate default content on read.
            default_factory: Optional callable to generate initial page content.
        """
        self.auto_populate: bool = auto_populate
        self.default_factory: Optional[Callable[[VirtualPage], Any]] = default_factory
        
        # Primary storage: VirtualPage -> data payload
        self._storage: Dict[VirtualPage, Any] = {}
        
        # Failure injection flags
        self._fail_reads: Set[VirtualPage] = set()
        self._fail_writes: Set[VirtualPage] = set()
        self._next_read_fails: bool = False
        self._next_write_fails: bool = False
        
        # Telemetry counters
        self.successful_reads: int = 0
        self.successful_writes: int = 0
        self.failed_reads: int = 0
        self.failed_writes: int = 0

    def read_page(self, vpage: VirtualPage) -> Any:
        """
        Simulate reading a virtual page from secondary storage into memory.
        
        Args:
            vpage: VirtualPage to fetch from backing store.
            
        Returns:
            The stored data content.
            
        Raises:
            DiskReadError: If a simulated disk read error is injected.
            PageNotFoundError: If auto_populate is False and the page was never written.
        """
        if not isinstance(vpage, VirtualPage):
            raise TypeError(f"vpage must be a VirtualPage instance, got {type(vpage).__name__}")

        # Check injected failure conditions
        if self._next_read_fails or (vpage in self._fail_reads):
            self._next_read_fails = False
            self.failed_reads += 1
            raise DiskReadError(f"Simulated backing store read failure for page {vpage}")

        if vpage not in self._storage:
            if self.auto_populate:
                initial_data = self.default_factory(vpage) if self.default_factory else f"data_{vpage.pid}_{vpage.vpn}"
                self._storage[vpage] = initial_data
            else:
                self.failed_reads += 1
                raise PageNotFoundError(f"Page {vpage} does not exist on backing store.")

        self.successful_reads += 1
        return self._storage[vpage]

    def write_page(self, vpage: VirtualPage, data: Any) -> None:
        """
        Simulate writing a dirty virtual page from physical memory to secondary storage.
        
        Args:
            vpage: VirtualPage being written back.
            data: Page content payload to persist.
            
        Raises:
            DiskWriteError: If a simulated disk write error is injected.
        """
        if not isinstance(vpage, VirtualPage):
            raise TypeError(f"vpage must be a VirtualPage instance, got {type(vpage).__name__}")

        # Check injected failure conditions
        if self._next_write_fails or (vpage in self._fail_writes):
            self._next_write_fails = False
            self.failed_writes += 1
            raise DiskWriteError(f"Simulated backing store write failure for page {vpage}")

        self._storage[vpage] = data
        self.successful_writes += 1

    def initialize_page(self, vpage: VirtualPage, data: Any) -> None:
        """Explicitly pre-populate a page in backing storage without incrementing metrics."""
        if not isinstance(vpage, VirtualPage):
            raise TypeError(f"vpage must be a VirtualPage instance, got {type(vpage).__name__}")
        self._storage[vpage] = data

    def contains_page(self, vpage: VirtualPage) -> bool:
        """Check if page content currently exists in backing storage."""
        return vpage in self._storage

    def get_stored_data(self, vpage: VirtualPage) -> Optional[Any]:
        """Inspect stored data for a page without incrementing read metrics."""
        return self._storage.get(vpage)

    # -------------------------------------------------------------------------
    # Fault Injection Controls for Transaction Testing
    # -------------------------------------------------------------------------

    def fail_next_read(self) -> None:
        """Trigger an immediate failure on the next read_page() call."""
        self._next_read_fails = True

    def fail_next_write(self) -> None:
        """Trigger an immediate failure on the next write_page() call."""
        self._next_write_fails = True

    def inject_read_error(self, vpage: VirtualPage) -> None:
        """Cause all future read_page() calls for vpage to fail until cleared."""
        self._fail_reads.add(vpage)

    def inject_write_error(self, vpage: VirtualPage) -> None:
        """Cause all future write_page() calls for vpage to fail until cleared."""
        self._fail_writes.add(vpage)

    def clear_error_injections(self) -> None:
        """Remove all simulated failure flags."""
        self._fail_reads.clear()
        self._fail_writes.clear()
        self._next_read_fails = False
        self._next_write_fails = False

    def reset(self) -> None:
        """Clear storage, error injections, and all telemetry counters."""
        self._storage.clear()
        self.clear_error_injections()
        self.successful_reads = 0
        self.successful_writes = 0
        self.failed_reads = 0
        self.failed_writes = 0

    def __len__(self) -> int:
        """Number of unique virtual pages currently persisted."""
        return len(self._storage)

    def __repr__(self) -> str:
        return (
            f"BackingStore(stored_pages={len(self._storage)}, "
            f"reads={self.successful_reads}, writes={self.successful_writes}, "
            f"failed_reads={self.failed_reads}, failed_writes={self.failed_writes})"
        )

