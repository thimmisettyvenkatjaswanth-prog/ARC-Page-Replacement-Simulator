"""
Virtual Page representation and identity for OS Memory Management.
"""
from dataclasses import dataclass
from typing import Tuple

from backend.memory.exceptions import InvalidAddressError


@dataclass(frozen=True, order=True)
class VirtualPage:
    """
    Immutable representation of a Virtual Page in an operating system.
    
    Identified uniquely by the composite key (pid, vpn).
    Hashable, orderable, and suitable for dictionary keys and set elements.
    """
    pid: int
    vpn: int

    def __post_init__(self) -> None:
        """Validate process ID and virtual page number."""
        if not isinstance(self.pid, int) or isinstance(self.pid, bool):
            raise InvalidAddressError(f"Process ID (pid) must be an integer, got {type(self.pid).__name__}")
        if not isinstance(self.vpn, int) or isinstance(self.vpn, bool):
            raise InvalidAddressError(f"Virtual Page Number (vpn) must be an integer, got {type(self.vpn).__name__}")
        if self.pid < 0:
            raise InvalidAddressError(f"Process ID (pid) must be non-negative, got {self.pid}")
        if self.vpn < 0:
            raise InvalidAddressError(f"Virtual Page Number (vpn) must be non-negative, got {self.vpn}")

    @property
    def page_id(self) -> Tuple[int, int]:
        """Return composite identifier tuple (pid, vpn)."""
        return (self.pid, self.vpn)

    def __str__(self) -> str:
        return f"P{self.pid}:VPN{self.vpn}"

    def __repr__(self) -> str:
        return f"VirtualPage(pid={self.pid}, vpn={self.vpn})"

