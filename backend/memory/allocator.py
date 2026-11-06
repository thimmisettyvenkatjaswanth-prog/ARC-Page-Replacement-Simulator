"""
Physical Frame Allocator managing free and occupied physical frames.
"""
from collections import deque
from typing import List, Optional, Set

from backend.memory.exceptions import (
    DoubleFreeError,
    InvalidFrameError,
    NoFreeFrameError,
)


class FrameAllocator:
    """
    Manages allocation, tracking, and reclamation of physical memory frames.
    
    Guarantees:
      - Fixed capacity of physical frames (IDs 0 to capacity - 1).
      - FIFO allocation order for available free frames.
      - Detection and rejection of double-free attempts.
      - Bounds checking on all frame identifiers.
    """

    def __init__(self, capacity: int):
        """
        Initialize the FrameAllocator with fixed physical capacity.
        
        Args:
            capacity: Total number of physical frames (integer >= 1).
        """
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError(f"Physical frame capacity must be a positive integer >= 1, got {capacity!r}")

        self.capacity: int = capacity
        
        # Free list of frame IDs maintained as a FIFO deque
        self._free_frames: deque[int] = deque(range(capacity))
        
        # Fast lookup set of currently allocated frame IDs
        self._allocated_frames: Set[int] = set()

    @property
    def free_count(self) -> int:
        """Number of currently available unallocated physical frames."""
        return len(self._free_frames)

    @property
    def allocated_count(self) -> int:
        """Number of currently allocated physical frames."""
        return len(self._allocated_frames)

    def has_free_frame(self) -> bool:
        """Check if at least one free physical frame is available."""
        return len(self._free_frames) > 0

    def peek_free_frame(self) -> Optional[int]:
        """
        Inspect the next free frame ID that would be allocated, without removing it.
        
        Returns:
            The next available frame ID, or None if no frames are free.
        """
        if self._free_frames:
            return self._free_frames[0]
        return None

    def allocate_frame(self) -> int:
        """
        Allocate the next available free physical frame in FIFO order.
        
        Returns:
            The allocated frame ID.
            
        Raises:
            NoFreeFrameError: If all physical frames are currently allocated.
        """
        if not self._free_frames:
            raise NoFreeFrameError(
                f"Physical memory exhausted: all {self.capacity} frames are currently allocated."
            )

        frame_id = self._free_frames.popleft()
        self._allocated_frames.add(frame_id)
        return frame_id

    def free_frame(self, frame_id: int) -> None:
        """
        Release an allocated physical frame back to the free pool.
        
        Args:
            frame_id: The ID of the frame to release.
            
        Raises:
            InvalidFrameError: If frame_id is out of bounds [0, capacity - 1].
            DoubleFreeError: If frame_id is already in the free pool or not allocated.
        """
        if not isinstance(frame_id, int) or isinstance(frame_id, bool):
            raise InvalidFrameError(f"Frame ID must be an integer, got {type(frame_id).__name__}")
        if not (0 <= frame_id < self.capacity):
            raise InvalidFrameError(
                f"Frame ID {frame_id} out of bounds for capacity {self.capacity} [0, {self.capacity - 1}]."
            )

        if frame_id not in self._allocated_frames:
            raise DoubleFreeError(
                f"Cannot free frame {frame_id}: frame is not currently allocated."
            )

        self._allocated_frames.remove(frame_id)
        self._free_frames.append(frame_id)

    def is_allocated(self, frame_id: int) -> bool:
        """Check if the given frame ID is currently allocated."""
        return frame_id in self._allocated_frames

    def is_free(self, frame_id: int) -> bool:
        """Check if the given frame ID is currently free."""
        return (0 <= frame_id < self.capacity) and (frame_id not in self._allocated_frames)

    def get_allocated_frames(self) -> Set[int]:
        """Return a copy of the set of all currently allocated frame IDs."""
        return set(self._allocated_frames)

    def get_free_frames(self) -> List[int]:
        """Return a list of all currently free frame IDs in FIFO queue order."""
        return list(self._free_frames)

    def reset(self) -> None:
        """Reset the allocator to initial state with all frames free."""
        self._free_frames = deque(range(self.capacity))
        self._allocated_frames.clear()

    def __len__(self) -> int:
        """Total capacity of the allocator."""
        return self.capacity

    def __repr__(self) -> str:
        return (
            f"FrameAllocator(capacity={self.capacity}, allocated={self.allocated_count}, "
            f"free={self.free_count})"
        )

