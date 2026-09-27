"""Qt6 桌面端一键启动脚本。"""

import argparse
import io
import os
import sys

# ---- PyInstaller 单文件 EXE 兼容 ----
# 单文件 EXE 运行时，代码解压到临时目录，但配置/数据需要从 EXE 所在目录读取
if getattr(sys, "frozen", False):
    # PyInstaller 打包后的 EXE
    _exe_dir = os.path.dirname(sys.executable)
    os.chdir(_exe_dir)  # 切换工作目录到 EXE 所在目录
    sys.path.insert(0, _exe_dir)
else:
    sys.path.insert(0, os.path.dirname(__file__))

# 设置控制台编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 确保运行时目录存在
for _d in ["data", "logs", "config"]:
    os.makedirs(_d, exist_ok=True)

# 禁用 AKShare / tqdm 进度条输出（避免与 Qt 线程冲突）
os.environ["AKSHARE_TQDM"] = "0"
os.environ["TQDM_DISABLE"] = "1"

from loguru import logger

from src.services.pipeline_service import PipelineService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", help="无界面模式（仅执行全流程）")
    parser.add_argument("--warmup-before-ui", action="store_true", help="启动 UI 前先执行一次全流程")
    args = parser.parse_args()

    logger.info("A股量化交易系统启动（Qt6 桌面版）...")
    pipeline = PipelineService()

    # headless 默认执行完整流程；GUI 默认先秒开界面，避免用户等待
    should_run_pipeline_now = args.headless or args.warmup_before_ui
    if should_run_pipeline_now:
        result = pipeline.run_full()
        logger.info(f"启动前流水线执行结果: {result}")
    else:
        logger.info("已启用快速启动：先打开界面，采集/分析由界面按钮或定时任务触发。")

    if args.headless:
        logger.info("headless 模式执行完成。")
        return

    logger.info("启动 Qt6 桌面界面...")
    from src.desktop.main import run_desktop

    run_desktop()


if __name__ == "__main__":
    main()
