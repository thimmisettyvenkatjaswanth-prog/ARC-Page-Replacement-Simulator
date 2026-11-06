"""
OS Virtual Memory Management subsystem package.
"""
from backend.memory.allocator import FrameAllocator
from backend.memory.backing_store import BackingStore
from backend.memory.exceptions import (
    BackingStoreError,
    BindingError,
    ConcurrentTransactionError,
    CorruptedManagerStateError,
    DiskReadError,
    DiskWriteError,
    DoubleFreeError,
    DuplicateProcessError,
    InvalidAddressError,
    InvalidFrameError,
    InvalidProcessStateError,
    InvalidQuotaError,
    ManagerPanicError,
    MemorySystemError,
    NoFreeFrameError,
    PageNotFoundError,
    PageTableError,
    ProcessError,
    ProcessNotFoundError,
    ProcessTerminatedError,
    StaleTokenError,
    UnsupportedOperationError,
    UnsupportedPolicyError,
)
from backend.memory.frame import PhysicalFrame
from backend.memory.page import VirtualPage
from backend.memory.page_table import PageTable, PageTableEntry
from backend.memory.process import (
    ProcessControlBlock,
    ProcessQuota,
    ProcessState,
    ProcessTable,
)
from backend.memory.process_metrics import ProcessMemoryMetrics

def __getattr__(name: str):
    if name in ("MemoryManager", "AccessResult", "MemoryMetrics"):
        from backend.memory.manager import AccessResult, MemoryManager, MemoryMetrics
        globals()["AccessResult"] = AccessResult
        globals()["MemoryManager"] = MemoryManager
        globals()["MemoryMetrics"] = MemoryMetrics
        return globals()[name]
    if name in ("PolicyAdapter", "VictimSelectionResult"):
        from backend.memory.policy_adapter import PolicyAdapter, VictimSelectionResult
        globals()["PolicyAdapter"] = PolicyAdapter
        globals()["VictimSelectionResult"] = VictimSelectionResult
        return globals()[name]
    if name in ("MultiProcessMemoryManager", "ReplacementScope", "MultiProcessMemoryMetrics", "MultiProcessAccessResult"):
        from backend.memory.multi_process_manager import (
            MultiProcessAccessResult,
            MultiProcessMemoryManager,
            MultiProcessMemoryMetrics,
            ReplacementScope,
        )
        globals()["MultiProcessAccessResult"] = MultiProcessAccessResult
        globals()["MultiProcessMemoryManager"] = MultiProcessMemoryManager
        globals()["MultiProcessMemoryMetrics"] = MultiProcessMemoryMetrics
        globals()["ReplacementScope"] = ReplacementScope
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    # Core Structures
    "VirtualPage",
    "PhysicalFrame",
    "PageTableEntry",
    "PageTable",
    "FrameAllocator",
    "BackingStore",
    "PolicyAdapter",
    "VictimSelectionResult",
    "MemoryManager",
    "MemoryMetrics",
    "AccessResult",
    # Multi-Process Structures
    "ProcessState",
    "ProcessQuota",
    "ProcessMemoryMetrics",
    "ProcessControlBlock",
    "ProcessTable",
    # Exceptions
    "MemorySystemError",
    "InvalidAddressError",
    "InvalidFrameError",
    "NoFreeFrameError",
    "DoubleFreeError",
    "PageTableError",
    "BackingStoreError",
    "DiskReadError",
    "DiskWriteError",
    "PageNotFoundError",
    "ConcurrentTransactionError",
    "StaleTokenError",
    "BindingError",
    "UnsupportedOperationError",
    "UnsupportedPolicyError",
    "CorruptedManagerStateError",
    "ProcessError",
    "DuplicateProcessError",
    "ProcessNotFoundError",
    "InvalidProcessStateError",
    "ProcessTerminatedError",
    "InvalidQuotaError",
    "ManagerPanicError",
    # Multi-Process Manager & Scope
    "MultiProcessMemoryManager",
    "ReplacementScope",
    "MultiProcessMemoryMetrics",
    "MultiProcessAccessResult",
]
