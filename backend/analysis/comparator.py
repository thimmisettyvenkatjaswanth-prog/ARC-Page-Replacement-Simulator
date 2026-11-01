"""
Comparative Analysis Layer for Cache Replacement Algorithms.
Executes ARC, LRU, and FIFO simultaneously over identical reference streams
and cache capacities to benchmark and rank their performance.

Independent of FastAPI and frontend frameworks.
"""
from typing import Any, Dict, List, Optional, Sequence
from backend.algorithms.arc import ARCCache
from backend.algorithms.fifo import FIFOCache
from backend.algorithms.lru import LRUCache
from backend.simulation.engine import SimulationEngine


class CacheComparator:
    """
    Orchestrates comparative benchmarking across multiple page replacement algorithms.
    Guarantees that all algorithms receive fresh instances, identical workloads,
    and identical capacities without state leakage.
    """

    def __init__(self, capacity: int, reference_string: Sequence[Any]):
        """
        Initialize the comparator.
        
        Args:
            capacity: Cache capacity c (positive integer >= 1).
            reference_string: Sequence of page references.
        """
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {capacity}")
        if reference_string is None:
            raise ValueError("Reference string cannot be None")
            
        self.capacity: int = capacity
        self.reference_string: List[Any] = list(reference_string)

    @staticmethod
    def evaluate_comparison(results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate winner selection and ranking strictly based on page faults.
        
        Decision rules:
          1. Find minimum page_faults across all algorithms.
          2. Include all algorithms sharing the minimum page-fault count in best_algorithms.
          3. If exactly one algorithm has the minimum:
             - is_tie = False
             - best_algorithm = algorithm name
          4. If multiple algorithms share the minimum:
             - is_tie = True
             - best_algorithm = None
             - best_algorithms = all tied algorithms
             
        IMPORTANT: Secondary metrics (hits, replacements, execution time) are NEVER
        used to break a page-fault tie. Algorithms with equal page faults are tied
        together in the ranking.
        
        Args:
            results: Per-algorithm results dictionary.
            
        Returns:
            Dictionary containing best_algorithm, best_algorithms, is_tie, and comparison_summary.
        """
        min_faults = min(res["page_faults"] for res in results.values())
        max_hit_ratio = max(res["hit_ratio"] for res in results.values())
        fastest_time = min(res["execution_time_ms"] for res in results.values())

        # Identify all algorithms achieving the minimum page-fault count
        winners = [name for name, res in results.items() if res["page_faults"] == min_faults]
        is_tie = len(winners) > 1
        best_algorithm: Optional[str] = None if is_tie else winners[0]

        # Group algorithms strictly by page faults ascending
        fault_groups: Dict[int, List[str]] = {}
        for name in results.keys():
            faults = results[name]["page_faults"]
            fault_groups.setdefault(faults, []).append(name)

        ranking: List[Dict[str, Any]] = []
        current_rank = 1
        for faults in sorted(fault_groups.keys()):
            tied_names = fault_groups[faults]
            ranking.append({
                "rank": current_rank,
                "algorithms": tied_names,
                "page_faults": faults,
                "is_tie": len(tied_names) > 1
            })
            current_rank += len(tied_names)

        # Generate comparative summary description
        if is_tie:
            summary_text = (
                f"Tie between {', '.join(winners)} with {min_faults} page faults "
                f"({max_hit_ratio * 100:.2f}% hit ratio)."
            )
        else:
            other_faults = ", ".join(
                f"{name}: {results[name]['page_faults']}"
                for name in results
                if name != best_algorithm
            )
            summary_text = (
                f"{best_algorithm} achieved the best performance with fewest page faults "
                f"({min_faults} vs {other_faults})."
            )

        return {
            "best_algorithm": best_algorithm,
            "best_algorithms": winners,
            "is_tie": is_tie,
            "comparison_summary": {
                "ranking": ranking,
                "fewest_page_faults": min_faults,
                "highest_hit_ratio": max_hit_ratio,
                "fastest_execution_ms": fastest_time,
                "summary_text": summary_text
            }
        }

    def compare(self, include_steps: bool = False) -> Dict[str, Any]:
        """
        Execute comparison across ARC, LRU, and FIFO.
        
        Args:
            include_steps: If True, attaches step-by-step telemetry for each algorithm.
            
        Returns:
            Structured, JSON-serializable dictionary containing comparative metrics,
            per-algorithm results, ranking, and tie-aware winner selection.
        """
        # Instantiate fresh, unpolluted algorithm instances
        algorithms: Dict[str, Any] = {
            "ARC": ARCCache(self.capacity),
            "LRU": LRUCache(self.capacity),
            "FIFO": FIFOCache(self.capacity)
        }

        results: Dict[str, Any] = {}

        for name, algo_instance in algorithms.items():
            engine = SimulationEngine(algo_instance, self.reference_string)
            sim_output = engine.run()
            metrics = sim_output["metrics"]

            algo_summary: Dict[str, Any] = {
                "algorithm": name,
                "capacity": self.capacity,
                "total_requests": metrics["total_requests"],
                "hits": metrics["total_hits"],
                "misses": metrics["total_misses"],
                "page_faults": metrics["total_page_faults"],
                "replacements": metrics["total_replacements"],
                "hit_ratio": metrics["hit_ratio"],
                "miss_ratio": metrics["miss_ratio"],
                "page_fault_rate": metrics["page_fault_rate"],
                "execution_time_ms": metrics["execution_time_ms"],
                "final_state": sim_output["final_state"]
            }

            if include_steps:
                algo_summary["steps"] = sim_output["steps"]

            results[name] = algo_summary

        # Evaluate winners and ranking strictly based on page faults
        evaluation = self.evaluate_comparison(results)

        return {
            "capacity": self.capacity,
            "reference_string": self.reference_string,
            "results": results,
            "best_algorithm": evaluation["best_algorithm"],
            "best_algorithms": evaluation["best_algorithms"],
            "is_tie": evaluation["is_tie"],
            "comparison_summary": evaluation["comparison_summary"]
        }


def compare_algorithms(
    reference_string: Sequence[Any],
    capacity: int,
    include_steps: bool = False
) -> Dict[str, Any]:
    """
    Convenience function to compare ARC, LRU, and FIFO on identical parameters.
    
    Args:
        reference_string: Sequence of page references.
        capacity: Cache capacity c (positive integer >= 1).
        include_steps: Whether to include step-by-step traces.
        
    Returns:
        Structured, JSON-serializable comparison dictionary.
    """
    comparator = CacheComparator(capacity=capacity, reference_string=reference_string)
    return comparator.compare(include_steps=include_steps)
