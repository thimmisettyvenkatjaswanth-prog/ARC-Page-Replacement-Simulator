"""
Process Memory Metrics tracking fine-grained per-process virtual memory telemetry.
"""
from dataclasses import dataclass


@dataclass
class ProcessMemoryMetrics:
    """
    Cumulative and instantaneous memory metrics for a single ProcessControlBlock.
    
    Counters:
      - requests: Total memory reference requests issued by this process.
      - hits: Cache/residency hits for pages of this process.
      - misses: Page faults incurred by this process.
      - pageins: Successful page-in disk operations for this process.
      - writebacks: Successful dirty write-back operations for this process.
      - clean_evictions: Clean pages of this process evicted.
      - dirty_evictions: Dirty pages of this process evicted and written back.
      - resident_frames: Current count of physical frames mapped to this process.
      - evictions_suffered: Times a resident page of this process was evicted by another process.
      - evictions_caused: Times a page fault in this process caused eviction of another process's page.
    """
    requests: int = 0
    hits: int = 0
    misses: int = 0
    pageins: int = 0
    writebacks: int = 0
    clean_evictions: int = 0
    dirty_evictions: int = 0
    resident_frames: int = 0
    evictions_suffered: int = 0
    evictions_caused: int = 0

    @property
    def hit_ratio(self) -> float:
        """Calculate process hit ratio rounded to 4 decimal places."""
        if self.requests == 0:
            return 0.0
        return round(self.hits / self.requests, 4)

    @property
    def page_fault_rate(self) -> float:
        """Calculate process page fault rate rounded to 4 decimal places."""
        if self.requests == 0:
            return 0.0
        return round(self.misses / self.requests, 4)

    @property
    def total_evictions(self) -> int:
        """Total local evictions (clean + dirty) of this process's pages."""
        return self.clean_evictions + self.dirty_evictions

    def reset(self) -> None:
        """Reset all metrics counters to pristine zero state."""
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.pageins = 0
        self.writebacks = 0
        self.clean_evictions = 0
        self.dirty_evictions = 0
        self.resident_frames = 0
        self.evictions_suffered = 0
        self.evictions_caused = 0

