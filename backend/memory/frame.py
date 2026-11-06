"""
Physical Frame representation and hardware MMU metadata.
"""
from typing import Any, Optional

from backend.memory.exceptions import InvalidFrameError
from backend.memory.page import VirtualPage


class PhysicalFrame:
    """
    Authoritative hardware representation of a Physical Memory Frame.
    
    Owns:
      - frame_id: Unique hardware frame index.
      - vpage: The VirtualPage currently mapped/resident in this frame (if occupied).
      - reference_bit: MMU reference/use bit (authoritative).
      - dirty_bit: MMU modified/dirty bit (authoritative).
      - data: Optional simulated payload stored in the frame.
    
    Hardware Invariant:
      External callers and replacement algorithms have strictly read-only access
      to reference_bit and dirty. Mutation is restricted to controlled internal methods
      coordinated by MemoryManager.
    """

    def __init__(self, frame_id: int):
        """
        Initialize an empty PhysicalFrame.
        
        Args:
            frame_id: Non-negative hardware frame identifier.
        """
        if not isinstance(frame_id, int) or isinstance(frame_id, bool):
            raise InvalidFrameError(f"Frame ID must be an integer, got {type(frame_id).__name__}")
        if frame_id < 0:
            raise InvalidFrameError(f"Frame ID must be non-negative, got {frame_id}")

        self._frame_id: int = frame_id
        self._vpage: Optional[VirtualPage] = None
        self._reference_bit: bool = False
        self._dirty: bool = False
        self._data: Any = None

    @property
    def frame_id(self) -> int:
        """Hardware frame identifier."""
        return self._frame_id

    @property
    def vpage(self) -> Optional[VirtualPage]:
        """Currently resident VirtualPage, or None if free."""
        return self._vpage

    @property
    def owner_pid(self) -> Optional[int]:
        """Process ID owning this resident frame, or None if free."""
        return self._vpage.pid if self._vpage is not None else None

    @property
    def vpn(self) -> Optional[int]:
        """Virtual Page Number resident in this frame, or None if free."""
        return self._vpage.vpn if self._vpage is not None else None

    @property
    def is_free(self) -> bool:
        """True if the frame currently holds no virtual page."""
        return self._vpage is None

    @property
    def is_allocated(self) -> bool:
        """True if the frame is assigned to a resident virtual page."""
        return self._vpage is not None

    @property
    def reference_bit(self) -> bool:
        """Authoritative read-only MMU reference bit."""
        return self._reference_bit

    @property
    def dirty(self) -> bool:
        """Authoritative read-only MMU dirty/modified bit."""
        return self._dirty

    @property
    def data(self) -> Any:
        """Simulated page content loaded in the frame."""
        return self._data

    def _set_reference_bit(self, value: bool) -> None:
        """Internal mutator restricted to MemoryManager coordination."""
        self._reference_bit = bool(value)

    def _set_dirty(self, value: bool) -> None:
        """Internal mutator restricted to MemoryManager coordination."""
        self._dirty = bool(value)

    def assign_page(
        self,
        vpage: VirtualPage,
        dirty: bool = False,
        reference: bool = True,
        data: Any = None
    ) -> None:
        """
        Assign a virtual page to this physical frame.
        
        Args:
            vpage: VirtualPage to load into this frame.
            dirty: Initial modified status (default False for clean load).
            reference: Initial reference status (default True on page fault load).
            data: Optional data payload to store.
        """
        if not isinstance(vpage, VirtualPage):
            raise TypeError(f"vpage must be a VirtualPage instance, got {type(vpage).__name__}")

        self._vpage = vpage
        self._dirty = bool(dirty)
        self._reference_bit = bool(reference)
        self._data = data

    def clear_frame(self) -> None:
        """
        Evict resident page and reset all hardware bits to pristine empty state.
        """
        self._vpage = None
        self._reference_bit = False
        self._dirty = False
        self._data = None

    def __str__(self) -> str:
        state = "FREE" if self.is_free else f"{self._vpage} (ref={int(self._reference_bit)}, dirty={int(self._dirty)})"
        return f"Frame[{self._frame_id}]: {state}"

    def __repr__(self) -> str:
        return (
            f"PhysicalFrame(frame_id={self._frame_id}, vpage={self._vpage!r}, "
            f"ref={self._reference_bit}, dirty={self._dirty})"
        )

