from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_global_view_qa_contract import export_contract


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="grading-global-contract-") as temp_dir:
        contract_path = export_contract(Path(temp_dir) / "global-view-contract.json")
        env = os.environ.copy()
        env["GLOBAL_QA_CONTRACT_PATH"] = str(contract_path)
        command = [
            "npm",
            "exec",
            "--",
            "vitest",
            "run",
            "--config",
            "vitest.integration.config.ts",
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT / "frontend",
            env=env,
            check=False,
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)
        print(f"Global View simulated frontend/backend flow passed: {contract_path.name}")


if __name__ == "__main__":
    main()
