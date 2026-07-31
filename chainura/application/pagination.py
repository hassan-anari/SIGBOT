from dataclasses import dataclass
from typing import Generic, TypeVar

from chainura.errors import InvalidDestinationInputError

T = TypeVar("T")
PAGE_SIZE = 8


@dataclass(frozen=True, slots=True)
class PageRequest:
    page: int = 1
    page_size: int = PAGE_SIZE

    def __post_init__(self) -> None:
        if isinstance(self.page, bool) or self.page < 1 or self.page_size != PAGE_SIZE:
            raise InvalidDestinationInputError("invalid page request")

    @property
    def offset(self) -> int: return (self.page - 1) * self.page_size


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    page: int
    page_size: int
    total: int

    @property
    def pages(self) -> int: return max(1, (self.total + self.page_size - 1) // self.page_size)
