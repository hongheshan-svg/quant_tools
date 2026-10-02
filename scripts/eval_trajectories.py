"""评估导出的真实问股 JSON：python scripts/eval_trajectories.py turns.json。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.services.trajectory_eval import evaluate_trajectory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trajectories", type=Path, help="{案例标识: ChatTurn} JSON；须自行在离线或授权模型环境采集")
    parser.add_argument("--cases", type=Path, default=Path("evals/agent_trajectory/cases.json"))
    args = parser.parse_args()
    trajectories = json.loads(args.trajectories.read_text())
    results = {case["id"]: evaluate_trajectory(trajectories.get(case["id"], {}), case) for case in json.loads(args.cases.read_text())}
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(result["passed"] for result in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
