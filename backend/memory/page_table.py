"""
Page Table and Page Table Entry (PTE) structures for virtual-to-physical address translation.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from backend.memory.exceptions import InvalidAddressError, InvalidFrameError


@dataclass
class PageTableEntry:
    """
    Hardware-mirrored Page Table Entry (PTE).
    
    Owns:
      - vpn: Virtual Page Number.
      - frame_id: Physical frame ID where this page resides, or None if swapped out.
      - valid: Present/Valid bit indicating whether the page is resident in physical memory.
      - read_only: Permission flag indicating write restrictions.
    """
    vpn: int
    frame_id: Optional[int] = None
    valid: bool = False
    read_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.vpn, int) or isinstance(self.vpn, bool):
            raise InvalidAddressError(f"VPN must be an integer, got {type(self.vpn).__name__}")
        if self.vpn < 0:
            raise InvalidAddressError(f"VPN must be non-negative, got {self.vpn}")
        if self.frame_id is not None:
            if not isinstance(self.frame_id, int) or isinstance(self.frame_id, bool):
                raise InvalidFrameError(f"Frame ID must be an integer, got {type(self.frame_id).__name__}")
            if self.frame_id < 0:
                raise InvalidFrameError(f"Frame ID must be non-negative, got {self.frame_id}")


class PageTable:
    """
    Per-process Virtual Memory Page Table.
    
    Manages virtual-to-physical address mappings for a specific Process ID.
    Authoritative owner of virtual page residency flags and frame pointers.
    """

    def __init__(self, pid: int):
        """
        Initialize an empty PageTable for the given process.
        
        Args:
            pid: Process ID owning this page table.
        """
        if not isinstance(pid, int) or isinstance(pid, bool):
            raise InvalidAddressError(f"Process ID must be an integer, got {type(pid).__name__}")
        if pid < 0:
            raise InvalidAddressError(f"Process ID must be non-negative, got {pid}")

        self.pid: int = pid
        self._entries: Dict[int, PageTableEntry] = {}

    def get_entry(self, vpn: int) -> PageTableEntry:
        """
        Retrieve or allocate the PageTableEntry for the specified VPN.
        
        If the VPN has never been accessed, allocates an invalid entry.
        """
        if not isinstance(vpn, int) or isinstance(vpn, bool) or vpn < 0:
            raise InvalidAddressError(f"VPN must be a non-negative integer, got {vpn!r}")

        if vpn not in self._entries:
            self._entries[vpn] = PageTableEntry(vpn=vpn, valid=False, frame_id=None)
        return self._entries[vpn]

    def map_page(
        self,
        vpn: int,
        frame_id: int,
        read_only: bool = False
    ) -> PageTableEntry:
        """
        Map a virtual page number to an allocated physical frame.
        
        Args:
            vpn: Virtual page number to map.
            frame_id: Physical frame ID allocated for this page.
            read_only: Whether the page is mapped with read-only permissions.
            
        Returns:
            The updated PageTableEntry marked valid=True.
        """
        entry = self.get_entry(vpn)
        if not isinstance(frame_id, int) or isinstance(frame_id, bool) or frame_id < 0:
            raise InvalidFrameError(f"Frame ID must be a non-negative integer, got {frame_id!r}")

        entry.frame_id = frame_id
        entry.valid = True
        entry.read_only = bool(read_only)
        return entry

    def unmap_page(self, vpn: int) -> Optional[PageTableEntry]:
        """
        Unmap a virtual page number upon eviction.
        
        Sets valid=False and clears frame_id=None.
        
        Returns:
            The modified entry, or None if the VPN was never in the table.
        """
        if vpn not in self._entries:
            return None

        entry = self._entries[vpn]
        entry.valid = False
        entry.frame_id = None
        return entry

    def is_valid(self, vpn: int) -> bool:
        """Check if a virtual page number is currently resident in physical memory."""
        entry = self._entries.get(vpn)
        return entry is not None and entry.valid

    def get_frame_id(self, vpn: int) -> Optional[int]:
        """Get the physical frame ID mapped to a resident VPN, or None if non-resident."""
        entry = self._entries.get(vpn)
        if entry is not None and entry.valid:
            return entry.frame_id
        return None

    def get_resident_pages(self) -> List[int]:
        """Return a sorted list of all virtual page numbers currently marked valid."""
        return sorted([vpn for vpn, entry in self._entries.items() if entry.valid])

    def reset(self) -> None:
        """Clear all page table entries, unmapping all pages."""
        self._entries.clear()

    def __len__(self) -> int:
        """Total number of tracked page table entries (valid and invalid)."""
        return len(self._entries)

    def __contains__(self, vpn: int) -> bool:
        """Check if entry exists and is currently valid/resident."""
        return self.is_valid(vpn)

    def __repr__(self) -> str:
        resident_count = len(self.get_resident_pages())
        return f"PageTable(pid={self.pid}, total_entries={len(self._entries)}, resident={resident_count})"

