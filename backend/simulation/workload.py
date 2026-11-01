"""
Workload Generator for Cache Simulation.
Generates synthetic and custom page-reference strings for benchmarking
page replacement policies under diverse locality and access conditions.

Independent of replacement algorithms and web frameworks.
"""
import random
from typing import Any, List, Optional, Sequence, Union


def generate_custom(pages: Sequence[Any], length: Optional[int] = None) -> List[Any]:
    """
    Validate and return a user-provided custom reference string.
    
    Args:
        pages: Sequence of page identifiers.
        length: Optional target length. If provided and greater than len(pages),
                the sequence is repeated to fill the length. If smaller, it is truncated.
                
    Returns:
        List of page identifiers.
    """
    if pages is None:
        raise ValueError("Pages sequence cannot be None")
    
    page_list = list(pages)
    if not page_list:
        raise ValueError("Custom page sequence cannot be empty")
        
    if length is not None:
        if not isinstance(length, int) or length <= 0:
            raise ValueError(f"Length must be a positive integer, got {length}")
        # Repeat or slice to match requested length
        result = [page_list[i % len(page_list)] for i in range(length)]
        return result
        
    return page_list


def generate_sequential(length: int, num_pages: int, start_page: int = 1) -> List[int]:
    """
    Generate sequential page references cycling from start_page to start_page + num_pages - 1.
    
    Useful for simulating sequential scan patterns and evaluating scan resistance.
    
    Args:
        length: Total number of references to generate (positive integer).
        num_pages: Number of distinct sequential pages before cycling (positive integer).
        start_page: First page identifier in the sequence (default 1).
        
    Returns:
        List of sequentially referenced page identifiers.
    """
    if not isinstance(length, int) or length <= 0:
        raise ValueError(f"Length must be a positive integer, got {length}")
    if not isinstance(num_pages, int) or num_pages <= 0:
        raise ValueError(f"num_pages must be a positive integer, got {num_pages}")
        
    return [start_page + (i % num_pages) for i in range(length)]


def generate_random(
    length: int,
    num_pages: int,
    start_page: int = 1,
    seed: Optional[int] = None
) -> List[int]:
    """
    Generate uniformly random page references over a range of pages.
    
    Args:
        length: Total number of references to generate (positive integer).
        num_pages: Total number of distinct pages (positive integer).
        start_page: Starting page identifier (default 1).
        seed: Optional random seed for deterministic reproducibility.
        
    Returns:
        List of randomly referenced page identifiers.
    """
    if not isinstance(length, int) or length <= 0:
        raise ValueError(f"Length must be a positive integer, got {length}")
    if not isinstance(num_pages, int) or num_pages <= 0:
        raise ValueError(f"num_pages must be a positive integer, got {num_pages}")
        
    rng = random.Random(seed)
    end_page = start_page + num_pages - 1
    return [rng.randint(start_page, end_page) for _ in range(length)]


def generate_repeated_locality(
    length: int,
    num_pages: int,
    hot_pages_count: int,
    hot_prob: float = 0.8,
    start_page: int = 1,
    seed: Optional[int] = None
) -> List[int]:
    """
    Generate page references with skewed temporal/frequency locality (e.g., 80-20 Zipfian rule).
    A small 'hot' working set receives the majority of requests, while the remaining
    'cold' pages receive the rest.
    
    Args:
        length: Total number of references to generate (positive integer).
        num_pages: Total number of distinct pages (positive integer).
        hot_pages_count: Number of hot pages (1 <= hot_pages_count <= num_pages).
        hot_prob: Probability of referencing a hot page (0.0 <= hot_prob <= 1.0).
        start_page: Starting page identifier (default 1).
        seed: Optional random seed for deterministic reproducibility.
        
    Returns:
        List of page references exhibiting high locality.
    """
    if not isinstance(length, int) or length <= 0:
        raise ValueError(f"Length must be a positive integer, got {length}")
    if not isinstance(num_pages, int) or num_pages <= 0:
        raise ValueError(f"num_pages must be a positive integer, got {num_pages}")
    if not isinstance(hot_pages_count, int) or not (1 <= hot_pages_count <= num_pages):
        raise ValueError(f"hot_pages_count must be between 1 and num_pages ({num_pages}), got {hot_pages_count}")
    if not (0.0 <= hot_prob <= 1.0):
        raise ValueError(f"hot_prob must be between 0.0 and 1.0, got {hot_prob}")
        
    rng = random.Random(seed)
    
    hot_set = list(range(start_page, start_page + hot_pages_count))
    cold_set = list(range(start_page + hot_pages_count, start_page + num_pages))
    
    result: List[int] = []
    for _ in range(length):
        if cold_set and rng.random() > hot_prob:
            result.append(rng.choice(cold_set))
        else:
            result.append(rng.choice(hot_set))
            
    return result


def generate_looping(
    length: int,
    loop: Union[int, Sequence[Any]],
    start_page: int = 1
) -> List[Any]:
    """
    Generate cyclic looping page references.
    
    Useful for demonstrating cyclic cache thrashing (e.g. loops of size c + 1).
    
    Args:
        length: Total number of references to generate (positive integer).
        loop: Either an integer specifying loop size (1..loop) or an explicit sequence.
        start_page: Starting page identifier if loop is an integer (default 1).
        
    Returns:
        List of cyclic page references of the requested length.
    """
    if not isinstance(length, int) or length <= 0:
        raise ValueError(f"Length must be a positive integer, got {length}")
        
    if isinstance(loop, int):
        if loop <= 0:
            raise ValueError(f"Loop size must be a positive integer, got {loop}")
        loop_seq = list(range(start_page, start_page + loop))
    else:
        loop_seq = list(loop)
        if not loop_seq:
            raise ValueError("Loop sequence cannot be empty")
            
    return [loop_seq[i % len(loop_seq)] for i in range(length)]


def generate_workload(workload_type: str, length: int, **kwargs) -> List[Any]:
    """
    Unified factory function for generating reference strings by workload name.
    
    Supported workload types:
      - 'custom': requires 'pages' in kwargs
      - 'sequential': requires 'num_pages' in kwargs
      - 'random': requires 'num_pages' in kwargs
      - 'repeated_locality': requires 'num_pages' and 'hot_pages_count' in kwargs
      - 'looping': requires 'loop' in kwargs
      
    Args:
        workload_type: Name of the workload strategy.
        length: Total length of the generated sequence.
        **kwargs: Additional parameters specific to the workload generator.
        
    Returns:
        List of generated page identifiers.
    """
    workload_type_lower = workload_type.strip().lower()
    
    if workload_type_lower == "custom":
        pages = kwargs.get("pages")
        if pages is None:
            raise ValueError("Workload type 'custom' requires 'pages' parameter")
        return generate_custom(pages=pages, length=length)
        
    elif workload_type_lower == "sequential":
        num_pages = kwargs.get("num_pages", length)
        start_page = kwargs.get("start_page", 1)
        return generate_sequential(length=length, num_pages=num_pages, start_page=start_page)
        
    elif workload_type_lower == "random":
        num_pages = kwargs.get("num_pages", 10)
        start_page = kwargs.get("start_page", 1)
        seed = kwargs.get("seed")
        return generate_random(length=length, num_pages=num_pages, start_page=start_page, seed=seed)
        
    elif workload_type_lower in ("repeated_locality", "locality", "zipfian"):
        num_pages = kwargs.get("num_pages", 10)
        hot_pages_count = kwargs.get("hot_pages_count", max(1, num_pages // 5))
        hot_prob = kwargs.get("hot_prob", 0.8)
        start_page = kwargs.get("start_page", 1)
        seed = kwargs.get("seed")
        return generate_repeated_locality(
            length=length,
            num_pages=num_pages,
            hot_pages_count=hot_pages_count,
            hot_prob=hot_prob,
            start_page=start_page,
            seed=seed
        )
        
    elif workload_type_lower == "looping":
        loop = kwargs.get("loop", kwargs.get("loop_size", 5))
        start_page = kwargs.get("start_page", 1)
        return generate_looping(length=length, loop=loop, start_page=start_page)
        
    else:
        raise ValueError(
            f"Unknown workload type '{workload_type}'. "
            "Supported types: 'custom', 'sequential', 'random', 'repeated_locality', 'looping'."
        )

