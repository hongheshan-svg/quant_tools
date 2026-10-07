"""ETF 规则轮动命令行：python scripts/etf_rotation.py [--refresh] [--output 路径]。"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config_loader import load_config
from src.services.etf_rotation import ETFRotationService


def main():
    parser = argparse.ArgumentParser(description="国内 ETF 双动量轮动研究回测")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--end")
    args = parser.parse_args()
    try:
        result = ETFRotationService(load_config()).run({"refresh": args.refresh, **({"end": args.end} if args.end else {})})
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        else:
            print(text)
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
