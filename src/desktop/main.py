"""Qt6 桌面端入口。"""

import io
import sys

from PyQt6.QtWidgets import QApplication

from src.desktop.main_window import MainWindow


def run_desktop():
    # 统一控制台编码，避免中文日志乱码
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    try:
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n!!! Qt 桌面端异常退出: {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    run_desktop()

