"""
Main entry point for ARC Page Replacement Simulator API.
Serves API endpoints for cache simulation, comparative benchmarking,
synthetic workload generation, and hosts the static frontend dashboard.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from backend.algorithms.arc import ARCCache
from backend.algorithms.fifo import FIFOCache
from backend.algorithms.lru import LRUCache
from backend.analysis.comparator import compare_algorithms
from backend.simulation.engine import SimulationEngine
from backend.simulation.workload import generate_workload

app = FastAPI(
    title="ARC Page Replacement Simulator",
    description="Interactive simulation and benchmarking of Adaptive Replacement Cache (FAST 2003) vs LRU and FIFO.",
    version="1.0.0"
)

# Enable CORS for decoupled local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handler to return clean 400 responses for ValueErrors without leaking stack traces
@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Intercept ValueError and return clean HTTP 400 response."""
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)}
    )


# ---------------------------------------------------------
# Request Models
# ---------------------------------------------------------

ALGORITHM_MAP = {
    "ARC": ARCCache,
    "LRU": LRUCache,
    "FIFO": FIFOCache
}


class SimulationRequest(BaseModel):
    """Request payload for executing a single algorithm simulation."""
    algorithm: str = Field(..., description="Replacement algorithm: 'ARC', 'LRU', or 'FIFO'")
    capacity: int = Field(..., description="Cache capacity (positive integer >= 1)")
    reference_string: List[Any] = Field(..., description="Sequence of page references")

    @field_validator("algorithm")
    @classmethod
    def validate_algorithm(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("Algorithm name must be a string")
        norm = v.strip().upper()
        if norm not in ALGORITHM_MAP:
            raise ValueError(
                f"Unsupported algorithm '{v}'. Supported algorithms: {', '.join(ALGORITHM_MAP.keys())}"
            )
        return norm

    @field_validator("capacity")
    @classmethod
    def validate_capacity(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {v}")
        return v

    @field_validator("reference_string")
    @classmethod
    def validate_reference_string(cls, v: List[Any]) -> List[Any]:
        if not isinstance(v, list):
            raise ValueError("reference_string must be a list of page identifiers")
        return v


class ComparisonRequest(BaseModel):
    """Request payload for comparing ARC, LRU, and FIFO simultaneously."""
    capacity: int = Field(..., description="Cache capacity (positive integer >= 1)")
    reference_string: List[Any] = Field(..., description="Sequence of page references")
    include_steps: bool = Field(False, description="Whether to include step-by-step traces")

    @field_validator("capacity")
    @classmethod
    def validate_capacity(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {v}")
        return v

    @field_validator("reference_string")
    @classmethod
    def validate_reference_string(cls, v: List[Any]) -> List[Any]:
        if not isinstance(v, list):
            raise ValueError("reference_string must be a list of page identifiers")
        return v


class WorkloadRequest(BaseModel):
    """Request payload for generating synthetic or custom workloads."""
    workload_type: str = Field(..., description="Workload type: 'custom', 'sequential', 'random', 'repeated_locality', 'looping'")
    length: int = Field(..., description="Total length of the sequence (positive integer >= 1)")
    num_pages: Optional[int] = Field(None, description="Number of distinct pages")
    start_page: int = Field(1, description="Starting page identifier (default 1)")
    seed: Optional[int] = Field(None, description="Optional random seed for reproducibility")
    hot_pages_count: Optional[int] = Field(None, description="Count of hot pages for locality workloads")
    hot_prob: Optional[float] = Field(None, description="Probability of accessing hot pages (0.0 to 1.0)")
    loop: Optional[Union[int, List[Any]]] = Field(None, description="Loop size or explicit cycle sequence")
    loop_size: Optional[int] = Field(None, description="Alias for loop size")
    pages: Optional[List[Any]] = Field(None, description="Explicit page sequence for custom workload")

    @field_validator("length")
    @classmethod
    def validate_length(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"Workload length must be a positive integer >= 1, got {v}")
        return v

    @field_validator("workload_type")
    @classmethod
    def validate_workload_type(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("workload_type must be a string")
        norm = v.strip().lower()
        valid = {"custom", "sequential", "random", "repeated_locality", "locality", "zipfian", "looping"}
        if norm not in valid:
            raise ValueError(
                f"Unknown workload type '{v}'. Supported types: 'custom', 'sequential', 'random', 'repeated_locality', 'looping'"
            )
        return norm


# ---------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------

@app.get("/api/health")
def health_check() -> Dict[str, str]:
    """
    Health check endpoint to verify backend service status.
    
    Returns:
        JSON response with healthy status and API version.
    """
    return {
        "status": "healthy",
        "service": "ARC Page Replacement Simulator API",
        "version": "1.0.0"
    }


@app.post("/api/simulate")
def simulate_algorithm(payload: SimulationRequest) -> Dict[str, Any]:
    """
    Execute a step-by-step cache replacement simulation for a single algorithm.
    Guarantees a fresh algorithm instance with unpolluted state.
    
    Args:
        payload: SimulationRequest with algorithm, capacity, and reference_string.
        
    Returns:
        Simulation results including per-step history, final cache state, and cumulative metrics.
    """
    algo_cls = ALGORITHM_MAP[payload.algorithm]
    cache_instance = algo_cls(capacity=payload.capacity)
    engine = SimulationEngine(algorithm=cache_instance, reference_string=payload.reference_string)
    sim_result = engine.run()

    return {
        "algorithm": payload.algorithm,
        "capacity": payload.capacity,
        "reference_string": payload.reference_string,
        "metrics": sim_result["metrics"],
        "steps": sim_result["steps"],
        "final_state": sim_result["final_state"]
    }


@app.post("/api/compare")
def compare_cache_algorithms(payload: ComparisonRequest) -> Dict[str, Any]:
    """
    Benchmark ARC, LRU, and FIFO simultaneously over the identical reference stream.
    Reuses the existing compare_algorithms function and preserves page-fault-only winner selection.
    
    Args:
        payload: ComparisonRequest with capacity, reference_string, and include_steps.
        
    Returns:
        Structured comparison results with metrics, ranking tiers, and tie status.
    """
    return compare_algorithms(
        reference_string=payload.reference_string,
        capacity=payload.capacity,
        include_steps=payload.include_steps
    )


@app.post("/api/workload")
def create_workload(payload: WorkloadRequest) -> Dict[str, Any]:
    """
    Generate synthetic or custom page reference sequences using existing workload generators.
    
    Args:
        payload: WorkloadRequest with workload_type, length, and generation parameters.
        
    Returns:
        Dictionary containing generated reference_string, workload_type, length, and parameters.
    """
    kwargs: Dict[str, Any] = {}
    if payload.num_pages is not None:
        kwargs["num_pages"] = payload.num_pages
    if payload.start_page is not None:
        kwargs["start_page"] = payload.start_page
    if payload.seed is not None:
        kwargs["seed"] = payload.seed
    if payload.hot_pages_count is not None:
        kwargs["hot_pages_count"] = payload.hot_pages_count
    if payload.hot_prob is not None:
        kwargs["hot_prob"] = payload.hot_prob
    if payload.loop is not None:
        kwargs["loop"] = payload.loop
    elif payload.loop_size is not None:
        kwargs["loop"] = payload.loop_size
    if payload.pages is not None:
        kwargs["pages"] = payload.pages

    ref_string = generate_workload(
        workload_type=payload.workload_type,
        length=payload.length,
        **kwargs
    )

    return {
        "workload_type": payload.workload_type,
        "length": len(ref_string),
        "parameters": kwargs,
        "reference_string": ref_string
    }


# ---------------------------------------------------------
# Static Frontend Mount
# ---------------------------------------------------------
frontend_path = Path(__file__).resolve().parent.parent / "frontend"
if frontend_path.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
