"""
Exception hierarchy for Virtual Memory Management structures.
"""

class MemorySystemError(Exception):
    """Base exception for all memory management errors."""
    pass


class InvalidAddressError(MemorySystemError):
    """Raised when a process ID or virtual page number is invalid."""
    pass


class NoFreeFrameError(MemorySystemError):
    """Raised when memory allocation fails because physical frames are exhausted."""
    pass


class DoubleFreeError(MemorySystemError):
    """Raised when an attempt is made to release an unallocated or already free frame."""
    pass


class InvalidFrameError(MemorySystemError):
    """Raised when a frame ID is out of bounds or unrecognized."""
    pass


class PageTableError(MemorySystemError):
    """Base exception for page table lookup, permission, and mapping errors."""
    pass


class BackingStoreError(MemorySystemError):
    """Base exception for backing store (simulated disk) I/O errors."""
    pass


class DiskReadError(BackingStoreError):
    """Raised when reading a page from the backing store fails."""
    pass


class DiskWriteError(BackingStoreError):
    """Raised when writing a dirty page to the backing store fails."""
    pass


class PageNotFoundError(BackingStoreError):
    """Raised when a requested page is not found in the backing store."""
    pass


class ConcurrentTransactionError(MemorySystemError):
    """Raised when an external mutation is attempted during an active memory transaction."""
    pass


class StaleTokenError(MemorySystemError):
    """Raised when a policy replacement token is rejected due to version divergence."""
    pass


class BindingError(MemorySystemError):
    """Raised when an illegal policy-to-MemoryManager binding is attempted."""
    pass


class UnsupportedOperationError(MemorySystemError):
    """Raised when an operation is forbidden in the current operating mode."""
    pass


class UnsupportedPolicyError(MemorySystemError):
    """Raised when an unsupported or unverified replacement policy is configured."""
    pass


class CorruptedManagerStateError(MemorySystemError):
    """Raised when MemoryManager has entered an unrecoverable panic state."""
    pass


class ProcessError(MemorySystemError):
    """Base exception for all multi-process management errors."""
    pass


class DuplicateProcessError(ProcessError):
    """Raised when attempting to register a process with an existing PID."""
    pass


class ProcessNotFoundError(ProcessError):
    """Raised when querying a process ID that is not registered."""
    pass


class InvalidProcessStateError(ProcessError):
    """Raised when an illegal process lifecycle state transition is attempted."""
    pass


class ProcessTerminatedError(ProcessError):
    """Raised when an operation is attempted on an already terminated process."""
    pass


class InvalidQuotaError(MemorySystemError, ValueError):
    """Raised when a process quota configuration is invalid or violates capacity bounds."""
    pass


class ManagerPanicError(MemorySystemError):
    """Raised when an unrecoverable failure occurs during in-memory state teardown."""
    pass



