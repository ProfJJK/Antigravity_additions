import os
import time
import logging
import psutil
import subprocess
import hashlib
from pathlib import Path
from pydantic import BaseModel, Field

# [M] Provenance Tag: Manual Architectural Design
# [E] Environment: Python 3.10+, Antigravity Swarm

class MolecularEvaluationResult(BaseModel):
    status: str
    pivot: bool
    grid: str
    hessian: str
    tolerance: str
    artifacts_hash: str = Field(default="")
    provenance: str = Field(default="[E]")

class JobConfig(BaseModel):
    target_directory: Path
    max_iterations: int = Field(default=100, le=100)
    integration_grid: str = Field(default="defgrid1")
    geom_tolerance: str = Field(default="1e-4")
    hessian_approximation: str = Field(default="InHess XTB2")
    executable_path: Path

# Resolve cochem_platform zombie sweeper
_BASE_SRC = Path(__file__).resolve().parents[2] / "GitHub-Repo" / "CoChem-BASE" / "src"
if _BASE_SRC.exists() and str(_BASE_SRC) not in sys.path:
    sys.path.insert(0, str(_BASE_SRC))

try:
    from cochem_platform.zombie_sweeper_module import sweep_zombies
except ImportError:
    try:
        from zombie_sweeper_module import sweep_zombies
    except ImportError:
        def sweep_zombies() -> None:
            for proc in psutil.process_iter(['pid', 'status']):
                try:
                    if proc.info['status'] == psutil.STATUS_ZOMBIE:
                        proc.wait(timeout=1)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                    continue


def verify_file_integrity(file_path: Path) -> str:
    if not file_path.exists():
        return "[MISSING DATA]"
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()

def evaluate_molecular_parameters(config: JobConfig) -> MolecularEvaluationResult:
    config.target_directory.mkdir(parents=True, exist_ok=True)
    
    if not config.executable_path.exists():
        logging.error(f"[MISSING DATA] Executable path not found: {config.executable_path}")
        return MolecularEvaluationResult(
            status="ERR_MISSING_DATA",
            pivot=False,
            grid=config.integration_grid,
            hessian=config.hessian_approximation,
            tolerance=config.geom_tolerance,
            provenance="[M]"
        )

    out_file = config.target_directory / "evaluation.out"
    sweep_zombies()
    
    try:
        env = os.environ.copy()
        env["TEMPERATURE"] = "298.15"
        env["PRESSURE"] = "1.0"
        
        result = subprocess.run(creationflags=0x08000000, 
            [str(config.executable_path), "--grid", config.integration_grid, "--tol", config.geom_tolerance],
            cwd=str(config.target_directory),
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=3600
        )
        
        out_file.write_text(result.stdout)
        hash_val = verify_file_integrity(out_file)
        logging.info(f"Execution successful. Artifact SHA-256: {hash_val} [E]")
        
        return MolecularEvaluationResult(
            status="SUCCESS",
            pivot=False,
            grid=config.integration_grid,
            hessian=config.hessian_approximation,
            tolerance=config.geom_tolerance,
            artifacts_hash=hash_val,
            provenance="[E]"
        )
        
    except subprocess.TimeoutExpired:
        logging.error(f"Timeout expired for {config.target_directory}")
        return MolecularEvaluationResult(
            status="ERR_TIMEOUT",
            pivot=False,
            grid=config.integration_grid,
            hessian=config.hessian_approximation,
            tolerance=config.geom_tolerance,
            provenance="[M]"
        )
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed: {e}")
        return MolecularEvaluationResult(
            status="FAILURE",
            pivot=True,
            grid="defgrid3",
            hessian=config.hessian_approximation,
            tolerance="TolMaxG 1e-5",
            provenance="[M]"
        )
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return MolecularEvaluationResult(
            status="FAILURE",
            pivot=False,
            grid=config.integration_grid,
            hessian=config.hessian_approximation,
            tolerance=config.geom_tolerance,
            provenance="[M]"
        )

def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.info("Initializing Sequential orchestrator for adversarial checks. [M]")
    
    home_dir = Path.home()
    base_dir = home_dir / ".cochem_artifacts" / "adversarial_checks"
    base_dir.mkdir(parents=True, exist_ok=True)
    
    executable_path = Path(os.environ.get("ORCA_EXECUTABLE", "orca"))
    
    configs = [
        JobConfig(
            target_directory=base_dir / f"target_{i}",
            executable_path=executable_path
        ) for i in range(4)
    ]
    
    try:
        for cfg in configs:
            res = evaluate_molecular_parameters(cfg)
            logging.info(f"Check result: {res.model_dump_json()}")
    except Exception as e:
        logging.error(f"Polling loop encountered error: {e}")

if __name__ == "__main__":
    main()
