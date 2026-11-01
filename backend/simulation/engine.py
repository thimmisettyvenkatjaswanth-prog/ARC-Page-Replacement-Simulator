"""
Simulation Engine for Page Replacement Algorithms.
Executes cache replacement policies against reference strings and collects
step-by-step telemetry, list transitions, and performance metrics.

Independent of FastAPI and frontend frameworks.
"""
import time
from typing import Any, Dict, List, Optional, Sequence


class SimulationEngine:
    """
    Simulation Engine that runs page replacement algorithms sequentially
    against page reference streams and records detailed execution traces.
    
    Supports any algorithm implementing:
      - request(page) -> Dict[str, Any]
      - get_state() -> Dict[str, Any]
      - (optional) reset() -> None
    """

    def __init__(self, algorithm: Any, reference_string: Optional[Sequence[Any]] = None):
        """
        Initialize simulation engine.
        
        Args:
            algorithm: Cache replacement instance (e.g. ARCCache or LRUCache).
            reference_string: Optional sequence of page references.
        """
        if algorithm is None:
            raise ValueError("Algorithm instance must be provided")
            
        self.algorithm = algorithm
        self.reference_string: List[Any] = list(reference_string) if reference_string is not None else []
        self.steps: List[Dict[str, Any]] = []
        self.metrics: Dict[str, Any] = {}

    def run(self, reference_string: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
        """
        Execute simulation over the reference string.
        
        Args:
            reference_string: Optional override of the reference string.
            
        Returns:
            Dictionary containing:
              - metrics: Cumulative performance metrics (hits, misses, ratios, runtime)
              - steps: Step-by-step telemetry history
              - final_state: Final state snapshot of the cache
        """
        if reference_string is not None:
            ref_stream = list(reference_string)
        else:
            ref_stream = self.reference_string

        self.steps = []
        cumulative_hits = 0
        cumulative_misses = 0
        
        start_time = time.perf_counter()
        
        for step_num, page in enumerate(ref_stream, start=1):
            req_result = self.algorithm.request(page)
            state = self.algorithm.get_state()
            
            is_hit = bool(req_result.get("hit", False))
            if is_hit:
                cumulative_hits += 1
            else:
                cumulative_misses += 1
                
            cum_hit_ratio = round(cumulative_hits / step_num, 4)
            cum_miss_ratio = round(cumulative_misses / step_num, 4)
            
            # Record step telemetry
            step_record: Dict[str, Any] = {
                "step": step_num,
                "page": page,
                "hit": is_hit,
                "hit_type": req_result.get("hit_type", "HIT" if is_hit else "MISS"),
                "evicted_page": req_result.get("evicted_page"),
                "movement": req_result.get("movement", ""),
                "state": state,
                "cumulative_hits": cumulative_hits,
                "cumulative_misses": cumulative_misses,
                "cumulative_page_faults": cumulative_misses,
                "cumulative_hit_ratio": cum_hit_ratio,
                "cumulative_miss_ratio": cum_miss_ratio
            }
            
            # Attach algorithm-specific fields if available
            if "p" in state:
                step_record["p"] = state["p"]
            if "T1" in state:
                step_record["T1"] = state["T1"]
                step_record["T2"] = state["T2"]
                step_record["B1"] = state["B1"]
                step_record["B2"] = state["B2"]
            if "pages" in state:
                step_record["pages"] = state["pages"]
            if "mru" in state:
                step_record["mru"] = state["mru"]
                step_record["lru"] = state["lru"]
                
            self.steps.append(step_record)
            
        end_time = time.perf_counter()
        elapsed_sec = end_time - start_time
        elapsed_ms = round(elapsed_sec * 1000.0, 4)
        
        # Calculate summary metrics (with zero-division protection)
        total_requests = len(ref_stream)
        total_hits = cumulative_hits
        total_misses = cumulative_misses
        total_replacements = getattr(self.algorithm, "replacements", 0)
        
        hit_ratio = round(total_hits / total_requests, 4) if total_requests > 0 else 0.0
        miss_ratio = round(total_misses / total_requests, 4) if total_requests > 0 else 0.0
        page_fault_rate = miss_ratio
        
        self.metrics = {
            "total_requests": total_requests,
            "total_hits": total_hits,
            "total_misses": total_misses,
            "total_page_faults": total_misses,
            "total_replacements": total_replacements,
            "hit_ratio": hit_ratio,
            "miss_ratio": miss_ratio,
            "page_fault_rate": page_fault_rate,
            "execution_time_seconds": round(elapsed_sec, 6),
            "execution_time_ms": elapsed_ms
        }
        
        return {
            "metrics": self.metrics,
            "steps": self.steps,
            "final_state": self.algorithm.get_state()
        }

