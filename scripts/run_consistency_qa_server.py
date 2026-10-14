from __future__ import annotations

import argparse

import uvicorn

from backend.app import alignment, main
from scripts.seed_consistency_qa_case import SeedEvaluator, SeedModel


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Run the API with deterministic QA grading models.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    alignment.LiveGradingModel = SeedModel
    alignment.LLMAlignmentEvaluator = SeedEvaluator
    uvicorn.run(main.app, host=args.host, port=args.port)


if __name__ == "__main__":
    main_cli()
