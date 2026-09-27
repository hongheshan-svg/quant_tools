"""
PyQt6 桌面主窗口。
"""

from __future__ import annotations

import json
import traceback
import webbrowser
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from loguru import logger
from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src import trading_calendar
from src.config_loader import load_config, reload_config
from src.desktop.ai_settings_dialog import AISettingsDialog
from src.services.data_query_service import DataQueryService
from src.services.pipeline_service import PipelineService
from src.trading.constants import (
    ORDER_ACTIVE_STATUSES,
    ORDER_STATUS_CANCELED,
    ORDER_STATUS_FAILED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_PARTIAL,
    ORDER_STATUS_PENDING_CONFIRM,
    ORDER_STATUS_REJECTED,
    ORDER_STATUS_SUBMITTED,
)

ORDER_STATUS_CN = {
    ORDER_STATUS_PENDING_CONFIRM: ("待确认", "#f1fa8c"),
    ORDER_STATUS_SUBMITTED: ("已报", "#8be9fd"),
    ORDER_STATUS_PARTIAL: ("部分成交", "#8be9fd"),
    ORDER_STATUS_FILLED: ("已成交", "#50fa7b"),
    ORDER_STATUS_CANCELED: ("已撤单", "#8994b3"),
    ORDER_STATUS_REJECTED: ("已拒绝", "#ff5555"),
    ORDER_STATUS_FAILED: ("失败", "#ff5555"),
}
ORDER_SIDE_CN = {"buy": "买入", "sell": "卖出"}

PLOT_MIN_DIMENSION = 10
PRE_OPEN_END_HHMM = 915
OPEN_AUCTION_END_HHMM = 925
OPEN_MATCH_END_HHMM = 930
MORNING_SESSION_END_HHMM = 1130
LUNCH_BREAK_END_HHMM = 1300
AFTERNOON_SESSION_END_HHMM = 1457
CLOSING_AUCTION_END_HHMM = 1500
HALF_HOUR_MINUTE_MARK = 30
REFRESH_MIN_INTERVAL_SECONDS = 3.0
LOW_LIQUIDITY_THRESHOLD = 2
STRONG_LIQUIDITY_THRESHOLD = 2.5
EXTREME_LOW_LIQUIDITY_THRESHOLD = 1.5
HIGH_CONFIDENCE_THRESHOLD = 7
MEDIUM_CONFIDENCE_THRESHOLD = 4
CHANGE_PCT_COLUMN_INDEX = 5
NEWS_LEVEL_COLUMN_INDEX = 2
NEWS_TITLE_COLUMN_INDEX = 3
NEWS_TAG_COLUMN_INDEX = 4


def _item(value: Any) -> QTableWidgetItem:
    text = "" if value is None else str(value)
    widget_item = QTableWidgetItem(text)
    widget_item.setFlags(widget_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return widget_item


class _LogBridge(QObject):
    """
    loguru → UI 日志桥。
    loguru sink 可能在任意线程调用，通过 Qt 信号安全转发到主线程。
    """
    new_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._alive = True

    def write(self, message: str):
        if not self._alive:
            return
        text = message.rstrip("\n")
        if text:
            try:
                self.new_message.emit(text)
            except RuntimeError:
                # Qt C++ 对象已销毁，静默忽略
                self._alive = False

    def flush(self):
        pass

    def close(self):
        self._alive = False


class WorkerSignals(QObject):
    """后台任务信号。"""

    finished = pyqtSignal(object)
    error = pyqtSignal(str)


class WorkerTask(QRunnable):
    """后台任务包装。"""

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.finished.emit(result)
        except Exception as e:
            detail = f"{e}\n{traceback.format_exc(limit=5)}"
            self.signals.error.emit(detail)


class CandlestickWidget(QWidget):
    """轻量K线绘制控件（无第三方依赖）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._title = ""
        self._ma_series: dict[int, list[float | None]] = {}
        self._ma_colors: dict[int, str] = {
            10: "#ffb86c",
            20: "#8be9fd",
            30: "#f1fa8c",
            55: "#bd93f9",
            89: "#50fa7b",
            144: "#ff79c6",
            233: "#7dd3fc",
            377: "#c084fc",
            450: "#34d399",
            610: "#fca5a5",
        }
        self.setMinimumHeight(300)

    @staticmethod
    def _build_ma_series(rows: list[dict], period: int) -> list[float | None]:
        closes = [CandlestickWidget._to_float(row.get("close"), 0.0) for row in rows]
        out: list[float | None] = [None] * len(closes)
        if period <= 0:
            return out
        rolling = 0.0
        for i, val in enumerate(closes):
            rolling += val
            if i >= period:
                rolling -= closes[i - period]
            if i >= period - 1:
                out[i] = rolling / period
        return out

    def set_ohlc(
        self,
        rows: list[dict],
        title: str = "",
        max_bars: int | None = None,
        ma_periods: list[int] | None = None,
    ):
        full_data = list(rows or [])
        start_idx = 0
        if max_bars and max_bars > 0 and len(full_data) > max_bars:
            start_idx = len(full_data) - max_bars
        data = full_data[start_idx:]
        self._rows = data
        self._title = title

        self._ma_series = {}
        periods = [p for p in (ma_periods or []) if isinstance(p, int) and p > 0]
        for period in periods:
            full_series = self._build_ma_series(full_data, period)
            self._ma_series[period] = full_series[start_idx:]

        self.update()

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            num = float(value)
            if num != num:
                return default
            return num
        except Exception:
            return default

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#0f1628"))

        painter.setPen(QColor("#8fd6ff"))
        title_font = QFont()
        title_font.setPointSize(10)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(12, 20, self._title or "K线图")

        if not self._rows:
            painter.setPen(QColor("#8994b3"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无K线数据")
            return

        margin_l, margin_t, margin_r, margin_b = 55, 48, 16, 32
        plot = self.rect().adjusted(margin_l, margin_t, -margin_r, -margin_b)
        if plot.width() <= PLOT_MIN_DIMENSION or plot.height() <= PLOT_MIN_DIMENSION:
            return

        highs = [self._to_float(row.get("high")) for row in self._rows]
        lows = [self._to_float(row.get("low")) for row in self._rows]
        ma_values: list[float] = []
        for series in self._ma_series.values():
            ma_values.extend(self._to_float(v) for v in series if v is not None)
        max_price = max(highs) if highs else 0.0
        min_price = min(lows) if lows else 0.0
        if ma_values:
            max_price = max(max_price, max(ma_values))
            min_price = min(min_price, min(ma_values))
        if max_price <= min_price:
            max_price += 1.0
            min_price -= 1.0
        pad = (max_price - min_price) * 0.05
        max_price += pad
        min_price -= pad

        def y_of(price: float) -> float:
            ratio = (max_price - price) / (max_price - min_price)
            return plot.top() + ratio * plot.height()

        painter.setPen(QPen(QColor("#213153"), 1))
        for i in range(5):
            y = plot.top() + i * (plot.height() / 4)
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))

        painter.setPen(QPen(QColor("#304569"), 1))
        painter.drawRect(plot)

        n = len(self._rows)
        bar_w = plot.width() / max(1, n)
        body_w = max(1, int(bar_w * 0.65))

        for i, row in enumerate(self._rows):
            o = self._to_float(row.get("open"))
            c = self._to_float(row.get("close"))
            h = self._to_float(row.get("high"))
            low = self._to_float(row.get("low"))
            x = plot.left() + (i + 0.5) * bar_w
            y_open = y_of(o)
            y_close = y_of(c)
            y_high = y_of(h)
            y_low = y_of(low)
            up = c >= o
            color = QColor("#ff5555" if up else "#50fa7b")
            painter.setPen(QPen(color, 1))
            painter.drawLine(int(x), int(y_high), int(x), int(y_low))

            top = min(y_open, y_close)
            height = max(1.0, abs(y_close - y_open))
            left = x - body_w / 2
            if up:
                painter.fillRect(int(left), int(top), int(body_w), int(height), QColor("#ff5555"))
            else:
                painter.drawRect(int(left), int(top), int(body_w), int(height))

        # 均线叠加
        for period in sorted(self._ma_series.keys()):
            series = self._ma_series.get(period) or []
            if not series:
                continue
            color = QColor(self._ma_colors.get(period, "#dbe7ff"))
            painter.setPen(QPen(color, 1.2))
            last_x = None
            last_y = None
            for i, mv in enumerate(series):
                if mv is None:
                    last_x = None
                    last_y = None
                    continue
                x = plot.left() + (i + 0.5) * bar_w
                y = y_of(self._to_float(mv))
                if (last_x is not None) and (last_y is not None):
                    painter.drawLine(int(last_x), int(last_y), int(x), int(y))
                last_x = x
                last_y = y

        # 均线图例（顶部）
        legend_x = plot.left() + 4
        legend_y = plot.top() + 14
        legend_font = QFont()
        legend_font.setPointSize(8)
        painter.setFont(legend_font)
        for period in sorted(self._ma_series.keys()):
            series = self._ma_series.get(period) or []
            if not series:
                continue
            latest = None
            for mv in reversed(series):
                if mv is not None:
                    latest = mv
                    break
            color = QColor(self._ma_colors.get(period, "#dbe7ff"))
            painter.setPen(color)
            text = f"MA{period}"
            if latest is not None:
                text += f" {latest:.2f}"
            text_w = painter.fontMetrics().horizontalAdvance(text) + 12
            if legend_x + text_w > plot.right() - 6:
                legend_x = plot.left() + 4
                legend_y += 14
            painter.drawText(int(legend_x), int(legend_y), text)
            legend_x += text_w

        painter.setPen(QColor("#9bb4d1"))
        label_font = QFont()
        label_font.setPointSize(8)
        painter.setFont(label_font)
        painter.drawText(8, int(y_of(max_price)) + 4, f"{max_price:.2f}")
        mid = (max_price + min_price) / 2
        painter.drawText(8, int(y_of(mid)) + 4, f"{mid:.2f}")
        painter.drawText(8, int(y_of(min_price)) + 4, f"{min_price:.2f}")

        first_date = str(self._rows[0].get("trade_date", ""))
        last_date = str(self._rows[-1].get("trade_date", ""))
        painter.setPen(QColor("#7f93b8"))
        painter.drawText(plot.left(), plot.bottom() + 18, first_date)
        right_text = painter.fontMetrics().horizontalAdvance(last_date)
        painter.drawText(plot.right() - right_text, plot.bottom() + 18, last_date)


class StockDetailDialog(QDialog):
    """股票详情：日/周/年K线 + 全量日线数据。"""

    def __init__(self, query: DataQueryService, code: str, name: str, parent=None):
        super().__init__(parent)
        self.query = query
        self.code = (code or "").strip()
        self.name = (name or "").strip()
        self.daily_rows: list[dict] = []
        self.weekly_rows: list[dict] = []
        self.yearly_rows: list[dict] = []

        self.setWindowTitle(f"股票详情 - {self.code} {self.name}")
        self.resize(1260, 860)
        self._build_ui()
        self._load_data()

    @staticmethod
    def _to_float(v: Any, default: float = 0.0) -> float:
        try:
            if v is None:
                return default
            num = float(v)
            if num != num:
                return default
            return num
        except Exception:
            return default

    @staticmethod
    def _format_num(v: Any, digits: int = 2) -> str:
        if v is None:
            return ""
        try:
            value = float(v)
            if value != value:
                return ""
            return f"{value:.{digits}f}"
        except Exception:
            return str(v)

    @staticmethod
    def _aggregate_period(rows: list[dict], period: str) -> list[dict]:
        groups: dict[tuple, dict] = {}
        order: list[tuple] = []

        for row in rows:
            date_str = str(row.get("trade_date", ""))[:10]
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                continue

            if period == "week":
                y, w, _ = dt.isocalendar()
                key = (y, w)
                bucket = f"{y}-W{w:02d}"
            elif period == "year":
                key = (dt.year,)
                bucket = str(dt.year)
            else:
                raise ValueError(f"unsupported period: {period}")

            o = StockDetailDialog._to_float(row.get("open"))
            h = StockDetailDialog._to_float(row.get("high"))
            low = StockDetailDialog._to_float(row.get("low"))
            c = StockDetailDialog._to_float(row.get("close"))
            vol = StockDetailDialog._to_float(row.get("volume"))
            amt = StockDetailDialog._to_float(row.get("amount"))

            if key not in groups:
                groups[key] = {
                    "trade_date": bucket,
                    "open": o,
                    "high": h,
                    "low": low,
                    "close": c,
                    "volume": vol,
                    "amount": amt,
                }
                order.append(key)
            else:
                g = groups[key]
                g["high"] = max(g["high"], h)
                g["low"] = min(g["low"], low)
                g["close"] = c
                g["volume"] += vol
                g["amount"] += amt

        return [groups[k] for k in order]

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.summary_label = QLabel("正在加载...")
        self.summary_label.setStyleSheet("color:#8fd6ff; font-weight:600;")
        root.addWidget(self.summary_label)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, stretch=1)

        self.daily_chart = CandlestickWidget()
        self.weekly_chart = CandlestickWidget()
        self.yearly_chart = CandlestickWidget()
        self.daily_hint = QLabel("")
        self.weekly_hint = QLabel("")
        self.yearly_hint = QLabel("")
        for hint in (self.daily_hint, self.weekly_hint, self.yearly_hint):
            hint.setStyleSheet("color:#8994b3; font-size:11px;")

        self.tabs.addTab(self._build_chart_tab(self.daily_chart, self.daily_hint), "日线K线")
        self.tabs.addTab(self._build_chart_tab(self.weekly_chart, self.weekly_hint), "周线K线")
        self.tabs.addTab(self._build_chart_tab(self.yearly_chart, self.yearly_hint), "年线K线")

        self.table_all = QTableWidget()
        self.table_all.setColumnCount(11)
        self.table_all.setHorizontalHeaderLabels(
            ["日期", "开盘", "最高", "最低", "收盘", "涨跌幅%", "成交量", "成交额", "换手率%", "总市值", "流通市值"]
        )
        self.table_all.verticalHeader().setVisible(False)
        self.table_all.setAlternatingRowColors(True)
        self.table_all.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_all.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_all.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table_all.horizontalHeader().setStretchLastSection(True)
        self.table_all.setColumnWidth(0, 95)
        self.table_all.setColumnWidth(1, 70)
        self.table_all.setColumnWidth(2, 70)
        self.table_all.setColumnWidth(3, 70)
        self.table_all.setColumnWidth(4, 70)
        self.table_all.setColumnWidth(5, 75)
        self.table_all.setColumnWidth(6, 95)
        self.table_all.setColumnWidth(7, 115)
        self.table_all.setColumnWidth(8, 75)
        self.table_all.setColumnWidth(9, 120)
        self.table_all.setColumnWidth(10, 120)
        self.tabs.addTab(self.table_all, "全量数据")

    def _build_chart_tab(self, chart: CandlestickWidget, hint_label: QLabel) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        layout.addWidget(chart, stretch=1)
        layout.addWidget(hint_label)
        return w

    def _load_data(self):
        rows = self.query.get_stock_daily_history(self.code)
        self.daily_rows = rows
        self.weekly_rows = self._aggregate_period(rows, "week") if rows else []
        self.yearly_rows = self._aggregate_period(rows, "year") if rows else []

        if not rows:
            self.summary_label.setText(f"{self.code} {self.name} 暂无历史数据")
            self.daily_chart.set_ohlc([], "日线K线")
            self.weekly_chart.set_ohlc([], "周线K线")
            self.yearly_chart.set_ohlc([], "年线K线")
            self.table_all.setRowCount(0)
            return

        first_day = rows[0]["trade_date"]
        last_day = rows[-1]["trade_date"]
        latest_close = self._format_num(rows[-1].get("close"), 2)
        self.summary_label.setText(
            f"{self.code} {self.name} | 数据区间: {first_day} ~ {last_day} | 最新收盘: {latest_close} | 日线总数: {len(rows)}"
        )

        ma_periods = [10, 20, 30, 55, 89, 144, 233, 377, 450, 610]
        self.daily_chart.set_ohlc(
            rows,
            "日线K线（最近240根）",
            max_bars=240,
            ma_periods=ma_periods,
        )
        self.weekly_chart.set_ohlc(self.weekly_rows, "周线K线", max_bars=520)
        self.yearly_chart.set_ohlc(self.yearly_rows, "年线K线")
        self.daily_hint.setText(
            f"显示 {min(240, len(rows))} / {len(rows)} 根 | 均线: "
            + ", ".join(f"MA{p}" for p in ma_periods)
        )
        self.weekly_hint.setText(f"显示 {len(self.weekly_rows)} 根")
        self.yearly_hint.setText(f"显示 {len(self.yearly_rows)} 根")
        self._fill_all_table(rows)

    def _fill_all_table(self, rows: list[dict]):
        display_rows = list(reversed(rows))
        self.table_all.setRowCount(len(display_rows))
        for i, r in enumerate(display_rows):
            values = [
                r.get("trade_date"),
                self._format_num(r.get("open"), 2),
                self._format_num(r.get("high"), 2),
                self._format_num(r.get("low"), 2),
                self._format_num(r.get("close"), 2),
                self._format_num(r.get("change_pct"), 2),
                self._format_num(r.get("volume"), 0),
                self._format_num(r.get("amount"), 0),
                self._format_num(r.get("turnover"), 2),
                self._format_num(r.get("total_mv"), 0),
                self._format_num(r.get("circ_mv"), 0),
            ]
            for j, v in enumerate(values):
                table_item = _item(v)
                if j in (1, 2, 3, 4, 5):
                    table_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if j == CHANGE_PCT_COLUMN_INDEX:
                    try:
                        pct = float(v)
                    except Exception:
                        pct = 0.0
                    if pct > 0:
                        table_item.setForeground(QColor("#ff5555"))
                    elif pct < 0:
                        table_item.setForeground(QColor("#50fa7b"))
                self.table_all.setItem(i, j, table_item)


class MainWindow(QMainWindow):
    """主窗口。"""

    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.query = DataQueryService()
        self.pipeline = PipelineService(self.config)
        self.thread_pool = QThreadPool.globalInstance()

        self.setWindowTitle("A股量化交易系统 - Qt6 桌面版")
        self.resize(1600, 950)

        self._busy = False
        self._refresh_inflight = False
        self._pending_refresh = False
        self._auto_score_triggered = False
        self._auto_collect_inflight = False  # 自动采集是否正在进行
        self._max_log_lines = int(self.config.get("desktop", {}).get("max_log_lines", 800))
        self._build_ui()
        self._apply_theme()
        self._bind_actions()
        self._setup_loguru_sink()
        self.refresh_dashboard(force=True)
        # 交易日历：先读本地缓存（不联网），再在后台按需联网更新
        db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        trading_calendar.load(db_path, refresh=False)
        self.thread_pool.start(WorkerTask(trading_calendar.load, db_path))
        self._setup_auto_refresh()
        # 启动后自动检查是否需要评分
        # 不再自动触发全流程/AI研判，改为手动点击
        # QTimer.singleShot(3000, self._check_auto_score)

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)

        # 顶部状态栏
        top_bar = QHBoxLayout()
        self.status_label = QLabel("状态: 就绪")
        self.status_label.setStyleSheet("font-weight: 600;")
        self.trade_day_label = QLabel("")
        self.trade_day_label.setTextFormat(Qt.TextFormat.RichText)
        self.trade_day_label.setStyleSheet("font-weight: 600; font-size: 13px; padding: 0 12px;")
        self.trade_session_label = QLabel("")
        self.trade_session_label.setTextFormat(Qt.TextFormat.RichText)
        self.trade_session_label.setStyleSheet("font-weight: 600; font-size: 13px; padding: 0 8px;")
        self.last_refresh_label = QLabel("最近刷新: -")
        top_bar.addWidget(self.status_label)
        top_bar.addWidget(self.trade_day_label)
        top_bar.addWidget(self.trade_session_label)
        top_bar.addStretch()
        top_bar.addWidget(self.last_refresh_label)
        layout.addLayout(top_bar)

        # 操作区（数据自动采集，仅保留AI预测和设置按钮）
        action_box = QGroupBox("任务操作")
        action_layout = QHBoxLayout(action_box)
        self.btn_premarket = QPushButton("AI涨停预测")
        self.btn_premarket.setToolTip("根据当前所有数据，AI预测最可能涨停的10只")
        self.btn_ai_settings = QPushButton("AI设置")
        self.btn_ai_settings.setToolTip("切换AI平台（DeepSeek/通义千问/智谱/Kimi等）")
        self.btn_ai_settings.setStyleSheet(
            "QPushButton { background: #6a3b8a; color: #fff; font-weight: 700; "
            "  border-radius: 6px; padding: 5px 12px; }"
            "QPushButton:hover { background: #8a4fb0; }"
        )
        for b in [self.btn_premarket, self.btn_ai_settings]:
            action_layout.addWidget(b)
        action_layout.addStretch()
        layout.addWidget(action_box)

        # ====== 市场概况指标卡（可滚动） ======
        market_box = QGroupBox("市场全局概况")
        market_outer = QVBoxLayout(market_box)
        market_outer.setContentsMargins(4, 8, 4, 4)
        market_outer.setSpacing(2)

        # 缩量警告/AI点评 放在卡片上方，完整可见
        self.volume_warning_label = QLabel("")
        self.volume_warning_label.setWordWrap(True)
        self.volume_warning_label.setStyleSheet(
            "color: #ff5555; font-weight: bold; font-size: 13px; padding: 2px 4px;"
        )
        market_outer.addWidget(self.volume_warning_label)

        # 指标卡行 — 放在 QScrollArea 中支持水平滚动
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(72)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:horizontal { height: 6px; background: #0b1020; }"
            "QScrollBar::handle:horizontal { background: #2a4ea1; border-radius: 3px; min-width: 30px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }"
        )
        card_container = QWidget()
        card_layout = QHBoxLayout(card_container)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(5)

        self._mkt_cards: dict[str, QLabel] = {}
        card_defs = [
            ("amount", "两市成交额", "--", 105),
            ("emotion", "市场情绪", "--", 90),
            ("up_down", "涨/跌/平", "--", 115),
            ("limit", "涨停/跌停", "--", 80),
            ("sh", "上证指数", "--", 110),
            ("sz", "深证成指", "--", 110),
            ("cy", "创业板指", "--", 110),
            ("north", "北向资金", "--", 85),
            ("top_sec", "领涨板块", "--", 130),
        ]
        for key, title, default_val, width in card_defs:
            card = self._build_market_card(title, default_val, width)
            card_layout.addWidget(card["frame"])
            self._mkt_cards[key] = card["value_label"]
        card_layout.addStretch()

        scroll.setWidget(card_container)
        market_outer.addWidget(scroll)
        layout.addWidget(market_box)

        # ====== 主要数据区（QTabWidget） ======
        self.main_tabs = QTabWidget()
        self.main_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #233357; border-radius: 6px; background: #121a2d; }"
            "QTabBar::tab { background: #1a2750; border: 1px solid #233357; border-bottom: none; "
            "  border-top-left-radius: 8px; border-top-right-radius: 8px; "
            "  padding: 8px 20px; margin-right: 2px; font-weight: 600; color: #8994b3; }"
            "QTabBar::tab:selected { background: #121a2d; color: #8fd6ff; border-bottom: 2px solid #8fd6ff; }"
            "QTabBar::tab:hover { background: #224080; color: #dbe7ff; }"
        )

        # ---- Tab 1: 交易决策 ----
        tab1 = QWidget()
        tab1_layout = QVBoxLayout(tab1)
        tab1_layout.setContentsMargins(0, 4, 0, 0)

        # 统一交易决策表（盘前预测 + 盘中评分 合并）
        self.table_trade = self._build_table(
            ["排名", "代码", "名称", "涨跌幅", "来源", "AI研判", "信心", "走势类型", "买入时机", "板块", "AI分析逻辑"]
        )
        trade_header = self.table_trade.horizontalHeader()
        trade_header.setStretchLastSection(True)
        trade_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        trade_header.setSectionResizeMode(10, QHeaderView.ResizeMode.Stretch)
        trade_header.setMinimumSectionSize(30)
        self.table_trade.setColumnWidth(0, 32)
        self.table_trade.setColumnWidth(1, 62)
        self.table_trade.setColumnWidth(2, 62)
        self.table_trade.setColumnWidth(3, 62)
        self.table_trade.setColumnWidth(4, 56)
        self.table_trade.setColumnWidth(5, 52)
        self.table_trade.setColumnWidth(6, 36)
        self.table_trade.setColumnWidth(7, 120)
        self.table_trade.setColumnWidth(8, 160)
        self.table_trade.setColumnWidth(9, 80)
        self.table_trade.cellDoubleClicked.connect(self._open_stock_detail)
        self.trade_title_label = QLabel("交易决策（AI实时涨停预测）")
        self.trade_title_label.setStyleSheet(
            "font-weight: bold; font-size: 13px; color: #8be9fd; padding: 2px 6px;"
        )
        trade_wrap = QWidget()
        trade_vbox = QVBoxLayout(trade_wrap)
        trade_vbox.setContentsMargins(0, 0, 0, 0)
        trade_vbox.setSpacing(0)
        trade_vbox.addWidget(self.trade_title_label)
        trade_vbox.addWidget(self.table_trade)
        tab1_layout.addWidget(trade_wrap, stretch=1)
        self.main_tabs.addTab(tab1, "交易决策")

        # ---- Tab 2: 实时资讯流 ----
        tab_news = QWidget()
        tab_news_layout = QVBoxLayout(tab_news)
        tab_news_layout.setContentsMargins(0, 4, 0, 0)

        self.table_news = self._build_table(
            ["时间", "来源", "级别", "资讯标题（双击打开来源）", "AI决策（行业→个股→操作建议）"]
        )
        header = self.table_news.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(40)
        self.table_news.setColumnWidth(0, 50)
        self.table_news.setColumnWidth(1, 60)
        self.table_news.setColumnWidth(2, 68)
        self.table_news.setColumnWidth(3, 550)
        self._news_row_urls: dict[int, str] = {}
        self.table_news.cellDoubleClicked.connect(self._open_news_source)
        tab_news_layout.addWidget(self.table_news, stretch=1)
        self.main_tabs.addTab(tab_news, "实时资讯流")

        # ---- Tab 2: 研报搜索（全网多源搜索） ----

        # ---- Tab 3: 模拟交易 ----
        self.main_tabs.addTab(self._build_trading_tab(), "模拟交易")

        # ---- Tab 4: 信号绩效 ----
        self._performance_tab = self._build_performance_tab()
        self.main_tabs.addTab(self._performance_tab, "信号绩效")
        self.main_tabs.currentChanged.connect(self._on_main_tab_changed)

        layout.addWidget(self.main_tabs, stretch=1)

        # 日志区
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(180)
        self.log_text.setPlaceholderText("运行日志会显示在这里，便于调试。")
        layout.addWidget(self._wrap("运行日志", self.log_text))

        self.setCentralWidget(root)

    def _build_trading_tab(self) -> QWidget:
        """模拟交易：账户概览、订单（确认/撤单）、持仓。"""
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 4, 0, 0)

        bar = QHBoxLayout()
        self.trading_account_label = QLabel("模拟盘账户: -")
        self.trading_account_label.setStyleSheet("font-weight: 600; color: #8be9fd; padding: 2px 6px;")
        self.btn_prepare_orders = QPushButton("生成订单")
        self.btn_prepare_orders.setToolTip("把最新交易信号转为待确认订单（经过风控校验）")
        self.btn_confirm_order = QPushButton("确认下单")
        self.btn_confirm_order.setToolTip("确认选中的待确认订单，提交到模拟盘")
        self.btn_cancel_order = QPushButton("撤销订单")
        self.btn_cancel_order.setToolTip("撤销选中的未完成订单")
        self.btn_refresh_trading = QPushButton("刷新")
        bar.addWidget(self.trading_account_label)
        bar.addStretch()
        for b in (self.btn_prepare_orders, self.btn_confirm_order, self.btn_cancel_order, self.btn_refresh_trading):
            bar.addWidget(b)
        tab_layout.addLayout(bar)

        self.table_orders = self._build_table(
            ["创建时间", "信号日期", "代码", "名称", "方向", "委托价", "数量", "金额", "状态", "说明"]
        )
        self.table_orders.setColumnWidth(0, 150)
        self.table_positions = self._build_table(
            ["代码", "名称", "持仓", "可卖", "成本价", "最新价", "市值", "浮动盈亏"]
        )
        tab_layout.addWidget(self._wrap("订单（选中后可确认下单或撤单）", self.table_orders), stretch=3)
        tab_layout.addWidget(self._wrap("持仓", self.table_positions), stretch=2)

        self._trading_orders: list[dict] = []
        self._trading_refresh_inflight = False
        return tab

    def _build_performance_tab(self) -> QWidget:
        """信号绩效：按验证日入场，统计 1/3/5 日收益、胜率、涨停命中率和止损止盈模拟。"""
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 4, 0, 0)

        bar = QHBoxLayout()
        self.performance_label = QLabel("近 60 天信号绩效（切换到本页时自动计算）")
        self.performance_label.setStyleSheet("font-weight: 600; color: #8be9fd; padding: 2px 6px;")
        self.performance_label.setToolTip(
            "入场：开盘前生成的信号按验证日开盘价，盘中生成的按收盘价；N日收益按收盘价计算。\n"
            "止损止盈：优先用信号自带的价格计划，否则按风控配置；T+1，入场次日起判断，"
            "同一天同时触及止损和止盈按止损处理。"
        )
        self.btn_refresh_performance = QPushButton("重新计算")
        bar.addWidget(self.performance_label)
        bar.addStretch()
        bar.addWidget(self.btn_refresh_performance)
        tab_layout.addLayout(bar)

        self.table_performance = self._build_table(
            ["维度", "分组", "信号数", "已评估", "涨停命中率", "1日胜率", "1日均收益",
             "3日胜率", "3日均收益", "5日胜率", "5日均收益", "模拟收益", "止损率", "止盈率"]
        )
        self.table_performance_details = self._build_table(
            ["信号日", "验证日", "代码", "名称", "类型", "来源", "研判", "入场价",
             "1日", "3日", "5日", "涨停", "离场", "模拟收益"]
        )
        tab_layout.addWidget(self._wrap("分组统计", self.table_performance), stretch=2)
        tab_layout.addWidget(self._wrap("信号明细", self.table_performance_details), stretch=3)
        self._performance_inflight = False
        return tab

    def _bind_actions(self):
        self.btn_premarket.clicked.connect(lambda: self._run_task("AI涨停预测", self.pipeline.premarket_predict))
        self.btn_ai_settings.clicked.connect(self._open_ai_settings)
        self.btn_prepare_orders.clicked.connect(lambda: self._run_task("生成订单", self.pipeline.prepare_orders))
        self.btn_confirm_order.clicked.connect(self._confirm_selected_order)
        self.btn_cancel_order.clicked.connect(self._cancel_selected_order)
        self.btn_refresh_trading.clicked.connect(self._refresh_trading)
        self.btn_refresh_performance.clicked.connect(self._refresh_performance)

    def _setup_loguru_sink(self):
        """
        将 loguru 所有日志输出桥接到 UI 日志区。
        loguru sink 在任意线程调用，通过 _LogBridge 信号安全转发到主线程。
        """
        self._log_bridge = _LogBridge()
        self._log_bridge.new_message.connect(self._on_loguru_message)
        # 添加 loguru sink：格式与终端一致，级别 DEBUG 以上全部捕获
        self._loguru_sink_id = logger.add(
            self._log_bridge,
            format="{time:HH:mm:ss} | {level:<7} | {name}:{function}:{line} | {message}",
            level="DEBUG",
            colorize=False,
            enqueue=True,  # 线程安全：loguru 内部用队列序列化写入
        )
        self._log("loguru 日志已桥接到界面（DEBUG 及以上全部显示）")

    def _on_loguru_message(self, text: str):
        """接收 loguru 转发过来的日志，追加到日志区。"""
        self.log_text.append(text)
        self._trim_log_lines()
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _setup_auto_refresh(self):
        desktop_cfg = self.config.get("desktop", {})

        # 全局数据刷新（交易决策等）
        interval_seconds = int(desktop_cfg.get("refresh_interval_seconds", 30))
        self.timer = QTimer(self)
        self.timer.setInterval(max(5, interval_seconds) * 1000)
        self.timer.timeout.connect(self.refresh_dashboard)
        self.timer.start()
        self._log(f"全局刷新已开启: {interval_seconds}s")

        # 资讯流独立高频刷新（默认10秒）
        news_interval = int(desktop_cfg.get("news_refresh_seconds", 10))
        self._news_refresh_timer = QTimer(self)
        self._news_refresh_timer.setInterval(max(5, news_interval) * 1000)
        self._news_refresh_timer.timeout.connect(self._refresh_news_only)
        self._news_refresh_timer.start()
        self._log(f"资讯流实时刷新已开启: {news_interval}s")

        # 自动采集定时器（仅采集数据，不触发AI分析/评分/预测）
        auto_collect_enabled = bool(desktop_cfg.get("auto_collect_enabled", True))
        collect_interval = int(desktop_cfg.get("collect_interval_seconds", 90))
        if auto_collect_enabled:
            self.collect_timer = QTimer(self)
            self.collect_timer.setInterval(max(20, collect_interval) * 1000)
            self.collect_timer.timeout.connect(self._auto_collect)
            self.collect_timer.start()
            # 启动后8秒立即采集一次，快速获取今日数据
            QTimer.singleShot(8000, self._auto_collect)
            self._log(f"自动数据采集已开启: {collect_interval}s（AI分析/评分/预测需手动触发）")

        # 市场概况独立刷新（60秒一次）
        self._mkt_overview_timer = QTimer(self)
        self._mkt_overview_timer.setInterval(60 * 1000)
        self._mkt_overview_timer.timeout.connect(self._refresh_market_overview)
        self._mkt_overview_timer.start()
        # 首次启动5秒后采集一次
        QTimer.singleShot(5000, self._refresh_market_overview)

        # 交易时钟（每秒更新交易日/交易时段）
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_trade_clock)
        self._clock_timer.start()
        self._update_trade_clock()  # 立即更新一次

        # 研报Tab定时刷新（60秒，仅在无搜索关键词时自动刷新本地数据）

        # AI 预测/全流程不再自动运行，仅手动点击触发
        # 避免自动 AI 任务与手动操作竞争 API 资源

    # ---------- 交易日 / 交易时段判断 ----------
    @staticmethod
    def _is_trade_day(d: datetime | None = None) -> bool:
        """判断指定日期是否为A股交易日（交易日历只查内存，可在界面线程高频调用）。"""
        return trading_calendar.is_trade_day(d)

    @staticmethod
    def _get_trade_session(now: datetime | None = None) -> tuple[str, str]:
        """
        返回当前交易时段名称和颜色。
        Returns: (时段描述, 颜色hex)
        """
        if now is None:
            now = datetime.now()
        h, m = now.hour, now.minute
        t = h * 100 + m  # HHMM 格式便于比较

        if t < PRE_OPEN_END_HHMM:
            return "盘前（未开盘）", "#8994b3"
        if t < OPEN_AUCTION_END_HHMM:
            return "集合竞价（开盘）", "#f1fa8c"
        if t < OPEN_MATCH_END_HHMM:
            return "集合竞价撮合", "#f1fa8c"
        if t < MORNING_SESSION_END_HHMM:
            mins_left = (11 * 60 + 30) - (h * 60 + m)
            return f"上午盘交易中（距午休 {mins_left}分钟）", "#50fa7b"
        if t < LUNCH_BREAK_END_HHMM:
            return "午间休市", "#bd93f9"
        if t < AFTERNOON_SESSION_END_HHMM:
            mins_left = (14 * 60 + 57) - (h * 60 + m)
            return f"下午盘交易中（距收盘 {mins_left}分钟）", "#50fa7b"
        if t < CLOSING_AUCTION_END_HHMM:
            return "尾盘集合竞价", "#ff7b7b"
        return "已收盘", "#8994b3"

    def _update_trade_clock(self):
        """每秒更新交易日和交易时段显示。"""
        now = datetime.now()
        weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        is_td = self._is_trade_day(now)
        if is_td:
            self.trade_day_label.setText(
                f"<span style='color:#50fa7b;'>● 交易日</span>"
                f"<span style='color:#dbe7ff;'>  {date_str} {weekday_cn} {time_str}</span>"
            )
            session_text, session_color = self._get_trade_session(now)
            self.trade_session_label.setText(
                f"<span style='color:{session_color};'>| {session_text}</span>"
            )
        else:
            self.trade_day_label.setText(
                f"<span style='color:#ff5555;'>○ 非交易日</span>"
                f"<span style='color:#8994b3;'>  {date_str} {weekday_cn} {time_str}</span>"
            )
            # 计算下一个交易日
            next_td = now
            for _ in range(10):
                next_td = next_td + timedelta(days=1)
                if self._is_trade_day(next_td):
                    break
            self.trade_session_label.setText(
                f"<span style='color:#8994b3;'>| 休市中，下一交易日: {next_td.strftime('%m-%d')} "
                f"{['周一','周二','周三','周四','周五','周六','周日'][next_td.weekday()]}</span>"
            )

    def _refresh_news_only(self):
        """独立刷新资讯流（不影响交易决策表等）。"""
        if self._refresh_inflight:
            return
        worker = WorkerTask(self.query.get_unified_news)
        worker.signals.finished.connect(self._on_news_only_ready)
        worker.signals.error.connect(lambda err: logger.debug(f"资讯刷新异常: {err}"))
        self.thread_pool.start(worker)

    def _on_news_only_ready(self, rows: list):
        try:
            old_count = self.table_news.rowCount()
            self._fill_news(rows)
            new_count = self.table_news.rowCount()
            # 有新消息时滚到顶部
            if new_count > 0 and new_count != old_count:
                self.table_news.scrollToTop()
        except Exception as e:
            logger.debug(f"资讯流刷新填充异常: {e}")

    # ---------- 研报搜索 Tab ----------
    def _open_ai_settings(self):
        """打开 AI 平台配置对话框。"""
        llm_cfg = self.config.get("llm", {})
        dlg = AISettingsDialog(llm_cfg, parent=self)
        dlg.settings_saved.connect(self._apply_new_llm_config)
        dlg.exec()

    def _apply_new_llm_config(self, new_llm: dict):
        """保存后热重载所有 LLM 客户端。"""
        # 刷新全局配置缓存
        self.config = reload_config()
        # 更新 pipeline 的 config（后续新建 Analyzer/Advisor 会使用新配置）
        self.pipeline.config = self.config

        provider = new_llm.get("primary", {}).get("provider", "")
        model = new_llm.get("primary", {}).get("model", "")
        self._log(f"AI 平台已切换: {provider} / {model}")
        self.status_label.setText(f"AI: {provider}/{model}")

    def _refresh_market_overview(self):
        """后台刷新市场全局概况（不阻塞UI，不占用 _busy）。"""
        worker = WorkerTask(self.pipeline.refresh_market_overview)
        worker.signals.finished.connect(self._on_market_overview_ready)
        worker.signals.error.connect(lambda err: logger.debug(f"市场概况刷新异常: {err}"))
        self.thread_pool.start(worker)

    def _on_market_overview_ready(self, overview: dict):
        try:
            if overview:
                self._fill_market_overview(overview)
        except Exception as e:
            logger.debug(f"市场概况填充异常: {e}")

    def _auto_predict(self):
        """定时自动触发涨停预测（每30分钟刷新一次）。"""
        if self._busy:
            return
        now = datetime.now()
        now_key = now.strftime("%Y-%m-%d_%H")  # 每小时最多触发一次
        half = "a" if now.minute < HALF_HOUR_MINUTE_MARK else "b"
        run_key = f"{now_key}_{half}"
        if run_key == self._predict_last_run:
            return
        self._predict_last_run = run_key
        self._log(f"自动刷新涨停预测（{now.strftime('%H:%M')}）...")
        self._run_task("AI涨停预测", self.pipeline.premarket_predict)

    def _auto_collect(self):
        """
        自动采集数据（后台静默运行，不设 _busy，不阻塞手动按钮）。
        """
        if self._auto_collect_inflight:
            return
        self._auto_collect_inflight = True
        self._log("自动采集数据中…")
        worker = WorkerTask(self.pipeline.collect)
        worker.signals.finished.connect(self._on_auto_collect_done)
        worker.signals.error.connect(self._on_auto_collect_error)
        self.thread_pool.start(worker)

    def _on_auto_collect_done(self, result):
        self._auto_collect_inflight = False
        self._log(f"自动采集完成: {self._humanize_result(result)}")
        # 采集完成后自动刷新界面
        self.refresh_dashboard(force=True)

    def _on_auto_collect_error(self, err: str):
        self._auto_collect_inflight = False
        logger.warning(f"自动采集异常: {err}")

    def _check_auto_score(self):
        """
        启动后自动检查：
        1. 今日无评分数据 → 自动全流程（含AI研判）
        2. 有评分但无AI研判 → 自动触发AI综合研判
        """
        if self._auto_score_triggered or self._busy:
            return
        try:
            from datetime import date as _date

            from sqlalchemy import func

            from src.database.db import get_db_session
            from src.database.models import StockScore, TradeSignal
            today = _date.today().strftime("%Y-%m-%d")
            db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
            with get_db_session(db_path) as session:
                cnt = session.query(StockScore).filter(StockScore.score_date == today).count()
                if cnt == 0:
                    # 也检查最新评分日期是否有AI研判
                    latest_date = session.query(func.max(StockScore.score_date)).scalar()
                    if latest_date:
                        ai_cnt = (
                            session.query(TradeSignal)
                            .filter(
                                TradeSignal.signal_date == latest_date,
                                TradeSignal.ai_verdict.isnot(None),
                                TradeSignal.ai_verdict != "",
                            )
                            .count()
                        )
                        if ai_cnt == 0:
                            self._auto_score_triggered = True
                            self._log(f"检测到评分日 {latest_date} 缺少AI研判，自动启动AI综合研判...")
                            self._run_task("自动AI研判", self.pipeline.advise)
                            return
                    self._auto_score_triggered = True
                    self._log("今日尚无评分数据，自动启动全流程（采集→AI分析→评分信号→AI研判）...")
                    self._run_task("自动全流程", self.pipeline.run_full)
                else:
                    # 有评分但检查是否有AI研判
                    ai_cnt = (
                        session.query(TradeSignal)
                        .filter(
                            TradeSignal.signal_date == today,
                            TradeSignal.ai_verdict.isnot(None),
                            TradeSignal.ai_verdict != "",
                        )
                        .count()
                    )
                    self._auto_score_triggered = True
                    if ai_cnt == 0:
                        self._log(f"今日已有 {cnt} 条评分但缺少AI研判，自动启动AI综合研判...")
                        self._run_task("自动AI研判", self.pipeline.advise)
                    else:
                        self._log(f"今日已有 {cnt} 条评分、{ai_cnt} 条AI研判，跳过自动任务。")
        except Exception as e:
            self._log(f"自动检查异常: {e}")

    def _build_market_card(self, title: str, default_val: str, width: int = 110) -> dict:
        """构建单个市场指标卡片。"""
        frame = QFrame()
        frame.setMinimumWidth(width)
        frame.setMaximumWidth(width + 40)
        frame.setFixedHeight(58)
        frame.setStyleSheet(
            "QFrame {"
            "  background-color: #151f38;"
            "  border: 1px solid #233357;"
            "  border-radius: 8px;"
            "  padding: 2px;"
            "}"
        )
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(4, 2, 4, 2)
        vbox.setSpacing(1)

        title_label = QLabel(title)
        title_label.setStyleSheet("color: #6b8fb8; font-size: 10px; border: none;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(title_label)

        value_label = QLabel(default_val)
        value_label.setTextFormat(Qt.TextFormat.RichText)
        value_label.setWordWrap(False)
        value_label.setStyleSheet("color: #dbe7ff; font-size: 13px; font-weight: bold; border: none;")
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(value_label)

        return {"frame": frame, "value_label": value_label}

    def _build_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setShowGrid(False)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        return table

    def _wrap(self, title: str, widget: QWidget) -> QGroupBox:
        box = QGroupBox(title)
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(widget)
        return box

    def _apply_theme(self):
        """深色科技风主题。"""
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #0b1020;
                color: #dbe7ff;
            }
            QWidget {
                color: #dbe7ff;
                font-size: 12px;
            }
            QGroupBox {
                border: 1px solid #233357;
                border-radius: 10px;
                margin-top: 8px;
                padding-top: 10px;
                background-color: #121a2d;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px 0 6px;
                color: #8fd6ff;
            }
            QPushButton {
                background-color: #1a2750;
                border: 1px solid #2a4ea1;
                border-radius: 8px;
                padding: 7px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #224080;
            }
            QPushButton:pressed {
                background-color: #162f62;
            }
            QPushButton:disabled {
                background-color: #2d3345;
                border-color: #3a435d;
                color: #8994b3;
            }
            QTableWidget {
                background-color: #0f1628;
                alternate-background-color: #131d33;
                border: 1px solid #26385f;
                border-radius: 8px;
                gridline-color: #26385f;
                selection-background-color: #1f4d99;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #192742;
                color: #9bd8ff;
                padding: 6px;
                border: none;
                border-right: 1px solid #2a3d66;
                font-weight: 700;
            }
            QTextEdit {
                background-color: #0f1628;
                border: 1px solid #2a3d66;
                border-radius: 8px;
                color: #c8d7ff;
                font-family: Consolas, "Microsoft YaHei UI";
                font-size: 12px;
            }
            QLabel {
                color: #dbe7ff;
            }
            QScrollBar:horizontal {
                background: #0b1020;
                height: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal {
                background: #2a4ea1;
                min-width: 40px;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #3b6fd4;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
            """
        )

    def _set_busy(self, busy: bool, status: str):
        self._busy = busy
        for b in (self.btn_premarket, self.btn_prepare_orders, self.btn_confirm_order, self.btn_cancel_order):
            b.setEnabled(not busy)
        self.status_label.setText(f"状态: {status}")

    def _run_task(self, name: str, fn: Callable):
        if self._busy:
            self._log("已有任务执行中，请稍候。")
            return
        self._set_busy(True, f"{name}中...")
        self._log(f"开始任务: {name}")
        worker = WorkerTask(fn)
        worker.signals.finished.connect(lambda result: self._on_task_done(name, result))
        worker.signals.error.connect(lambda err: self._on_task_error(name, err))
        self.thread_pool.start(worker)

    def _on_task_done(self, name: str, result: Any):
        self._log(f"任务完成: {name} -> {self._humanize_result(result)}")
        self._set_busy(False, "就绪")
        self._pending_refresh = False  # 清除旧的待刷新标记
        # 任务完成后强制刷新界面，确保显示最新数据
        self.refresh_dashboard(force=True)
        # 交易接口返回 ok=False 表示业务失败（如资金不足），需要提示用户
        if isinstance(result, dict) and result.get("ok") is False:
            QMessageBox.warning(self, name, f"{name}失败: {result.get('error') or '未知原因'}")
        orders = result.get("orders") if isinstance(result, dict) else None
        if isinstance(orders, dict) and orders.get("prepared"):
            self._log(f"已生成 {orders['prepared']} 笔订单，请到【模拟交易】页确认下单。")

    def _on_task_error(self, name: str, detail: str):
        self._log(f"任务失败: {name}\n{detail}")
        self._set_busy(False, "异常")
        QMessageBox.critical(self, "任务执行失败", f"{name} 执行失败，请查看日志。")

    def refresh_dashboard_async(self, force: bool = False):
        """异步刷新，避免 UI 主线程卡顿。限制最小间隔3秒（force=True 时跳过限速）。"""
        import time as _time
        now = _time.monotonic()
        if not force:
            # 速率限制：至少3秒间隔（仅对非强制刷新生效）
            if hasattr(self, "_last_refresh_time") and (now - self._last_refresh_time) < REFRESH_MIN_INTERVAL_SECONDS:
                self._pending_refresh = True
                return
            if self._busy:
                self._pending_refresh = True
                return
        if self._refresh_inflight:
            # 有刷新正在执行，标记为待刷新（完成后会自动再刷一次）
            self._pending_refresh = True
            return
        self._refresh_inflight = True
        self._last_refresh_time = now
        worker = WorkerTask(self.query.get_dashboard_snapshot)
        worker.signals.finished.connect(self._on_snapshot_ready)
        worker.signals.error.connect(self._on_snapshot_error)
        self.thread_pool.start(worker)

    def refresh_dashboard(self, force: bool = False):
        self.refresh_dashboard_async(force=force)
        self._refresh_trading()

    # ---------- 模拟交易 ----------
    def _refresh_trading(self):
        if self._trading_refresh_inflight:
            return
        self._trading_refresh_inflight = True
        worker = WorkerTask(self.pipeline.trading_snapshot)
        worker.signals.finished.connect(self._on_trading_snapshot)
        worker.signals.error.connect(self._on_trading_snapshot_error)
        self.thread_pool.start(worker)

    def _on_trading_snapshot_error(self, detail: str):
        self._trading_refresh_inflight = False
        self._log(f"模拟交易刷新异常: {detail}")

    def _on_trading_snapshot(self, snapshot: dict):
        self._trading_refresh_inflight = False
        account = snapshot.get("account", {})
        self.trading_account_label.setText(
            f"模拟盘账户  总资产 {account.get('total_assets', 0):,.2f}  |  "
            f"可用资金 {account.get('cash', 0):,.2f}  |  "
            f"持仓市值 {account.get('market_value', 0):,.2f}  |  "
            f"浮动盈亏 {account.get('unrealized_pnl', 0):+,.2f}"
        )

        self._trading_orders = snapshot.get("orders", [])
        self.table_orders.setRowCount(len(self._trading_orders))
        for i, o in enumerate(self._trading_orders):
            status_text, status_color = ORDER_STATUS_CN.get(o.get("status"), (o.get("status") or "", "#dbe7ff"))
            values = [
                o.get("created_at"),
                o.get("signal_date"),
                o.get("code"),
                o.get("name"),
                ORDER_SIDE_CN.get(o.get("side"), o.get("side")),
                f"{float(o.get('price') or 0):.2f}",
                o.get("quantity"),
                f"{float(o.get('amount') or 0):,.2f}",
                status_text,
                o.get("error_msg") or o.get("risk_note") or "",
            ]
            for col, value in enumerate(values):
                self.table_orders.setItem(i, col, _item(value))
            self.table_orders.item(i, 8).setForeground(QColor(status_color))

        positions = snapshot.get("positions", [])
        self.table_positions.setRowCount(len(positions))
        for i, p in enumerate(positions):
            pnl = float(p.get("unrealized_pnl") or 0)
            values = [
                p.get("code"),
                p.get("name"),
                p.get("quantity"),
                p.get("available_quantity"),
                f"{float(p.get('avg_cost') or 0):.3f}",
                f"{float(p.get('market_price') or 0):.2f}",
                f"{float(p.get('market_value') or 0):,.2f}",
                f"{pnl:+,.2f}",
            ]
            for col, value in enumerate(values):
                self.table_positions.setItem(i, col, _item(value))
            if pnl:
                self.table_positions.item(i, 7).setForeground(QColor("#ff5555" if pnl > 0 else "#50fa7b"))

    # ---------- 信号绩效 ----------
    def _on_main_tab_changed(self, index: int):
        if self.main_tabs.widget(index) is self._performance_tab:
            self._refresh_performance()

    def _refresh_performance(self):
        if self._performance_inflight:
            return
        self._performance_inflight = True
        self.performance_label.setText("信号绩效计算中…")
        worker = WorkerTask(self.pipeline.signal_performance)
        worker.signals.finished.connect(self._on_performance_ready)
        worker.signals.error.connect(self._on_performance_error)
        self.thread_pool.start(worker)

    def _on_performance_error(self, detail: str):
        self._performance_inflight = False
        self.performance_label.setText("信号绩效计算失败，详见日志")
        self._log(f"信号绩效计算异常: {detail}")

    @staticmethod
    def _pct_item(value: Any, signed: bool = True) -> QTableWidgetItem:
        """百分比单元格：signed=True 时带正负号并按红涨绿跌着色。"""
        if value is None:
            item = _item("--")
            item.setForeground(QColor("#8994b3"))
            return item
        item = _item(f"{value:+.2f}%" if signed else f"{value:.1f}%")
        if signed and value:
            item.setForeground(QColor("#ff5555" if value > 0 else "#50fa7b"))
        return item

    def _on_performance_ready(self, result: dict):
        self._performance_inflight = False
        self.performance_label.setText(
            f"近 {result.get('lookback_days')} 天信号绩效（截至 {result.get('as_of')}，鼠标悬停查看统计口径）"
        )

        summary = result.get("summary", [])
        self.table_performance.setRowCount(len(summary))
        for i, r in enumerate(summary):
            self.table_performance.setItem(i, 0, _item(r["dimension"]))
            self.table_performance.setItem(i, 1, _item(r["group"]))
            self.table_performance.setItem(i, 2, _item(r["total"]))
            self.table_performance.setItem(i, 3, _item(r["evaluated"]))
            cells = [
                (r["limit_up_rate"], False),
                (r["win_rate_1d"], False), (r["avg_return_1d"], True),
                (r["win_rate_3d"], False), (r["avg_return_3d"], True),
                (r["win_rate_5d"], False), (r["avg_return_5d"], True),
                (r["simulated_avg"], True),
                (r["stop_loss_rate"], False), (r["take_profit_rate"], False),
            ]
            for col, (value, signed) in enumerate(cells, start=4):
                self.table_performance.setItem(i, col, self._pct_item(value, signed))

        exit_cn = {"stop_loss": "止损", "ambiguous_stop_loss": "止损(同日触及)", "take_profit": "止盈", "window_end": "到期"}
        status_cn = {"pending": "待验证", "no_data": "无行情"}
        type_cn = {"premarket": "AI预测", "buy": "评分信号"}
        details = result.get("details", [])[:300]
        self.table_performance_details.setRowCount(len(details))
        for i, d in enumerate(details):
            returns = d.get("returns", {})
            entry = d.get("entry_price")
            entry_text = f"{entry:.2f}（{'开盘' if d.get('entry_at') == 'open' else '收盘'}）" if entry else status_cn.get(d["status"], "--")
            values = [d["signal_date"], d["eval_date"], d["code"], d["name"], type_cn.get(d["signal_type"], d["signal_type"]),
                      d["source"], d["verdict"], entry_text]
            for col, value in enumerate(values):
                self.table_performance_details.setItem(i, col, _item(value))
            for col, h in enumerate((1, 3, 5), start=8):
                self.table_performance_details.setItem(i, col, self._pct_item(returns.get(h)))
            limit_up = {True: "是", False: "否"}.get(d.get("hit_limit_up"), "--")
            self.table_performance_details.setItem(i, 11, _item(limit_up))
            self.table_performance_details.setItem(i, 12, _item(exit_cn.get(d.get("exit_reason"), "--")))
            self.table_performance_details.setItem(i, 13, self._pct_item(d.get("simulated_return")))

    def _selected_order(self) -> dict | None:
        row = self.table_orders.currentRow()
        if row < 0 or row >= len(self._trading_orders):
            QMessageBox.information(self, "模拟交易", "请先在订单表中选中一笔订单。")
            return None
        return self._trading_orders[row]

    def _confirm_selected_order(self):
        order = self._selected_order()
        if not order:
            return
        if order.get("status") != ORDER_STATUS_PENDING_CONFIRM:
            status_text = ORDER_STATUS_CN.get(order.get("status"), (order.get("status"), ""))[0]
            QMessageBox.information(self, "模拟交易", f"只能确认「待确认」的订单，该订单状态为「{status_text}」。")
            return
        side = ORDER_SIDE_CN.get(order.get("side"), order.get("side"))
        text = (
            f"以 {float(order.get('price') or 0):.2f} 元{side} {order.get('name')}({order.get('code')}) "
            f"{order.get('quantity')} 股，金额约 {float(order.get('amount') or 0):,.2f} 元。\n\n确认提交到模拟盘？"
        )
        if QMessageBox.question(self, "确认下单", text) != QMessageBox.StandardButton.Yes:
            return
        self._run_task("确认下单", partial(self.pipeline.confirm_order, order["id"]))

    def _cancel_selected_order(self):
        order = self._selected_order()
        if not order:
            return
        if order.get("status") not in ORDER_ACTIVE_STATUSES:
            QMessageBox.information(self, "模拟交易", "该订单已结束，无法撤销。")
            return
        self._run_task("撤销订单", partial(self.pipeline.cancel_order, order["id"]))

    def _on_snapshot_ready(self, snapshot: dict):
        try:
            self._fill_market_overview(snapshot.get("market_overview", {}))
            self._fill_trade_decision(
                snapshot.get("premarket_predictions", []),
                snapshot.get("trade_focus", []),
            )
            self._fill_news(snapshot.get("unified_news", []))
            now_str = datetime.now().strftime("%H:%M:%S")
            today_str = snapshot.get("today", "")
            score_date = snapshot.get("score_date", today_str)
            limit_up_date = snapshot.get("limit_up_date", today_str)
            date_hints = []
            if score_date and score_date != today_str:
                date_hints.append(f"评分:{score_date}")
            if limit_up_date and limit_up_date != today_str:
                date_hints.append(f"涨停:{limit_up_date}")
            if date_hints:
                self.last_refresh_label.setText(
                    f"最近刷新: {now_str}  |  数据日期: {', '.join(date_hints)}（点击【仅采集】更新今日数据）"
                )
            else:
                self.last_refresh_label.setText(f"最近刷新: {now_str}  |  数据日期: {today_str}")
            self._refresh_inflight = False
            if self._pending_refresh and not self._busy:
                self._pending_refresh = False
                # 延迟3秒再刷新，防止刷新风暴
                QTimer.singleShot(3000, lambda: self.refresh_dashboard_async(force=True))
        except Exception as e:
            logger.error(f"刷新界面失败: {e}")
            self._log(f"刷新界面失败: {e}")
            self._refresh_inflight = False

    def _on_snapshot_error(self, detail: str):
        self._refresh_inflight = False
        self._log(f"刷新任务异常: {detail}")

    def _fill_market_overview(self, overview: dict):
        """填充市场概况指标卡。"""
        if not overview:
            return

        def _color_pct(val: float) -> str:
            if val > 0:
                return f"<span style='color:#ff5555;font-weight:bold;'>+{val:.2f}%</span>"
            if val < 0:
                return f"<span style='color:#50fa7b;font-weight:bold;'>{val:.2f}%</span>"
            return f"<span style='color:#dbe7ff;'>{val:.2f}%</span>"

        # 成交额
        amount = overview.get("total_amount_yi", 0)
        amount_wan_yi = amount / 10000  # 转为万亿
        if amount > 0:
            color = "#ff5555" if amount_wan_yi < LOW_LIQUIDITY_THRESHOLD else "#50fa7b" if amount_wan_yi > STRONG_LIQUIDITY_THRESHOLD else "#f1fa8c"
            self._mkt_cards["amount"].setText(
                f"<span style='color:{color};'>{amount_wan_yi:.2f}万亿</span>"
            )
        else:
            self._mkt_cards["amount"].setText("--")

        # 缩量警告 + AI大盘点评
        ai_comment = overview.get("ai_market_comment", "")
        comment_text = f"  |  AI点评: {ai_comment}" if ai_comment else ""

        if 0 < amount_wan_yi < EXTREME_LOW_LIQUIDITY_THRESHOLD:
            self.volume_warning_label.setText(
                f"🚫 极度缩量({amount_wan_yi:.2f}万亿)，强烈建议空仓观望！{comment_text}"
            )
        elif 0 < amount_wan_yi < LOW_LIQUIDITY_THRESHOLD:
            self.volume_warning_label.setText(
                f"⚠ 两市成交额仅{amount_wan_yi:.2f}万亿，低于2万亿，不建议交易！{comment_text}"
            )
        elif ai_comment:
            self.volume_warning_label.setStyleSheet(
                "color: #8fd6ff; font-weight: bold; font-size: 12px; padding: 0 8px;"
            )
            self.volume_warning_label.setText(f"AI大盘点评: {ai_comment}")
        else:
            self.volume_warning_label.setText("")

        # 市场情绪
        emotion = overview.get("market_emotion", "")
        emo_colors = {
            "亢奋": "#ff5555", "偏多": "#ff7b7b",
            "震荡分化": "#f1fa8c", "偏空": "#8be9fd",
            "恐慌": "#50fa7b", "缩量谨慎": "#bd93f9",
            "极度缩量": "#bd93f9",
        }
        emo_color = emo_colors.get(emotion, "#dbe7ff")
        if emotion:
            self._mkt_cards["emotion"].setText(
                f"<span style='color:{emo_color};'>{emotion}</span>"
            )

        # 涨跌家数
        up_c = overview.get("up_count", 0)
        down_c = overview.get("down_count", 0)
        flat_c = overview.get("flat_count", 0)
        if up_c or down_c:
            self._mkt_cards["up_down"].setText(
                f"<span style='color:#ff5555;'>{up_c}</span>/"
                f"<span style='color:#50fa7b;'>{down_c}</span>/"
                f"<span style='color:#dbe7ff;'>{flat_c}</span>"
            )

        # 涨停/跌停
        lu = overview.get("limit_up_count", 0)
        ld = overview.get("limit_down_count", 0)
        if lu or ld:
            self._mkt_cards["limit"].setText(
                f"<span style='color:#ff5555;'>{lu}</span>/"
                f"<span style='color:#50fa7b;'>{ld}</span>"
            )

        # 三大指数（单行显示：点数 涨跌幅）
        for key, idx_field, pct_field in [
            ("sh", "sh_index", "sh_change_pct"),
            ("sz", "sz_index", "sz_change_pct"),
            ("cy", "cy_index", "cy_change_pct"),
        ]:
            idx_val = overview.get(idx_field, "")
            pct_val = overview.get(pct_field, 0.0)
            if idx_val:
                self._mkt_cards[key].setText(f"{idx_val} {_color_pct(pct_val)}")

        # 北向资金（含0也显示）
        nb = overview.get("northbound_net_yi")
        if nb is not None:
            if nb > 0:
                nb_color = "#ff5555"
            elif nb < 0:
                nb_color = "#50fa7b"
            else:
                nb_color = "#8994b3"
            self._mkt_cards["north"].setText(
                f"<span style='color:{nb_color};'>{nb:+.1f}亿</span>"
            )

        # 领涨板块
        top_secs = overview.get("top_sectors", [])
        if top_secs:
            parts = [f"{s['name']}{s['pct']:+.1f}%" for s in top_secs[:2]]
            self._mkt_cards["top_sec"].setText(
                "<span style='font-size:11px;'>" + " / ".join(parts) + "</span>"
            )

    def _fill_trade_decision(self, premarket: list[dict], trade_focus: list[dict]):
        """
        统一填充交易决策表。
        盘前：基于前一天数据预测今日。
        盘中：基于实时数据预测今日。
        盘后：基于今天数据预测明日。
        """
        # 决定数据源和标题
        has_premarket = bool(premarket)
        has_trade = bool(trade_focus)

        # 判断当前时段，用于在标题中标明预测目标日
        now = datetime.now()
        h, m = now.hour, now.minute
        t = h * 60 + m
        if t >= 15 * 60:
            # 盘后：基于今天数据预测明日
            target_label = "明日机会预测"
        elif t < 9 * 60 + 25:
            # 盘前：基于前一天数据预测今日
            target_label = "今日机会预测（盘前）"
        elif t <= 11 * 60 + 30:
            target_label = "今日实时预测（早盘）"
        elif t < 13 * 60:
            target_label = "今日实时预测（午间）"
        else:
            target_label = "今日实时预测（午盘）"

        if has_premarket:
            # 建立 trade_focus 的 code → row 映射，用于合并板块等信息
            tf_map = {r.get("code"): r for r in trade_focus} if has_trade else {}
            rows = premarket
            source = "premarket"
            title = f"交易决策（AI {target_label}）"
            if has_trade:
                title = f"交易决策（AI {target_label} + 综合评分）"
        elif has_trade:
            rows = trade_focus
            tf_map = {}
            source = "trade_focus"
            title = "交易决策（综合评分 + AI研判）"
        else:
            self.table_trade.setRowCount(0)
            self.trade_title_label.setText("交易决策（等待AI预测或评分数据...）")
            return

        self.trade_title_label.setText(title)
        self.table_trade.setUpdatesEnabled(False)
        try:
            self.table_trade.setRowCount(len(rows))
            for i, row in enumerate(rows):
                if source == "premarket":
                    self._fill_trade_row_premarket(i, row, tf_map.get(row.get("code"), {}))
                else:
                    self._fill_trade_row_trade_focus(i, row)
        finally:
            self.table_trade.setUpdatesEnabled(True)

    def _fill_trade_row_premarket(self, i: int, row: dict, tf: dict):
        """填充一行盘前预测数据。"""
        ai_verdict = str(row.get("ai_verdict", ""))
        confidence = row.get("confidence", 0)
        predict_type = str(row.get("predict_type", ""))
        target_time = str(row.get("target_time", ""))
        reason = str(row.get("reason", ""))
        ai_advice = str(row.get("ai_advice", ""))
        source = str(row.get("source", "涨停板"))

        # 兼容旧数据
        if not predict_type and ai_advice and ai_advice.startswith("[") and "]" in ai_advice:
            predict_type = ai_advice[1:ai_advice.index("]")].strip()

        # 合并盘中板块信息
        sector = str(tf.get("sector", "")) if tf else ""

        # col 0: 排名
        self.table_trade.setItem(i, 0, _item(row.get("rank")))
        # col 1: 代码
        self.table_trade.setItem(i, 1, _item(row.get("code")))
        # col 2: 名称
        self.table_trade.setItem(i, 2, _item(row.get("name")))

        # col 3: 涨跌幅
        chg = row.get("change_pct")
        if chg is not None:
            chg_text = f"{chg:+.2f}%"
            chg_item = _item(chg_text)
            if chg > 0:
                chg_item.setForeground(QColor("#ff5555"))  # 红涨
            elif chg < 0:
                chg_item.setForeground(QColor("#50fa7b"))  # 绿跌
            else:
                chg_item.setForeground(QColor("#8994b3"))
        else:
            chg_item = _item("--")
            chg_item.setForeground(QColor("#8994b3"))
        self.table_trade.setItem(i, 3, chg_item)

        # col 4: 来源
        source_item = _item(source)
        if source == "全市场":
            source_item.setForeground(QColor("#bd93f9"))  # 紫色
        elif source == "新闻驱动":
            source_item.setForeground(QColor("#ffb86c"))  # 橙色
        else:
            source_item.setForeground(QColor("#8be9fd"))  # 青色
        self.table_trade.setItem(i, 4, source_item)

        # col 5: AI研判
        verdict_item = _item(ai_verdict)
        if "买入" in ai_verdict:
            verdict_item.setForeground(QColor("#ff5555"))
        elif "回避" in ai_verdict:
            verdict_item.setForeground(QColor("#50fa7b"))
        else:
            verdict_item.setForeground(QColor("#f1fa8c"))
        self.table_trade.setItem(i, 5, verdict_item)

        # col 6: 信心
        conf_item = _item(f"{confidence}")
        if confidence >= HIGH_CONFIDENCE_THRESHOLD:
            conf_item.setForeground(QColor("#ff5555"))
        elif confidence >= MEDIUM_CONFIDENCE_THRESHOLD:
            conf_item.setForeground(QColor("#f1fa8c"))
        else:
            conf_item.setForeground(QColor("#8994b3"))
        self.table_trade.setItem(i, 6, conf_item)

        # col 7: 走势类型
        type_item = _item(predict_type)
        type_item.setForeground(QColor("#8be9fd"))
        self.table_trade.setItem(i, 7, type_item)

        # col 8: 买入时机
        time_item = _item(target_time)
        time_item.setForeground(QColor("#f1fa8c"))
        self.table_trade.setItem(i, 8, time_item)

        # col 9: 板块
        self.table_trade.setItem(i, 9, _item(sector))

        # col 10: AI分析逻辑
        display_parts = []
        if reason:
            display_parts.append(reason)
        if "风险:" in ai_advice:
            risk_part = ai_advice.split("风险:")[-1].split(" | ")[0].strip()
            if risk_part:
                display_parts.append(f"⚠{risk_part}")
        # 如果盘中有AI策略，追加
        tf_advice = str(tf.get("ai_advice", "")) if tf else ""
        if tf_advice:
            display_parts.append(f"盘中:{tf_advice[:60]}")
        display_text = " | ".join(display_parts) if display_parts else ai_advice
        advice_item = _item(display_text)
        if "买入" in ai_verdict:
            advice_item.setForeground(QColor("#ff5555"))
        elif "回避" in ai_verdict:
            advice_item.setForeground(QColor("#50fa7b"))
        self.table_trade.setItem(i, 10, advice_item)

    def _fill_trade_row_trade_focus(self, i: int, row: dict):
        """填充一行盘中评分数据（无盘前预测时的兜底）。"""
        ai_verdict = str(row.get("ai_verdict") or "")
        ai_advice = str(row.get("ai_advice") or "")
        score_val = row.get("composite_score")
        score_str = f"{score_val:.0f}" if isinstance(score_val, (int, float)) else ""
        rec_cn = self._recommendation_cn(row.get("recommendation"))

        # col 0: 排名
        self.table_trade.setItem(i, 0, _item(row.get("rank")))
        # col 1: 代码
        self.table_trade.setItem(i, 1, _item(row.get("code")))
        # col 2: 名称
        self.table_trade.setItem(i, 2, _item(row.get("name")))

        # col 3: 涨跌幅
        chg = row.get("change_pct")
        if chg is not None:
            chg_text = f"{chg:+.2f}%"
            chg_item = _item(chg_text)
            if chg > 0:
                chg_item.setForeground(QColor("#ff5555"))
            elif chg < 0:
                chg_item.setForeground(QColor("#50fa7b"))
            else:
                chg_item.setForeground(QColor("#8994b3"))
        else:
            chg_item = _item("--")
            chg_item.setForeground(QColor("#8994b3"))
        self.table_trade.setItem(i, 3, chg_item)

        # col 4: 来源 → 评分数据默认来源"涨停板"
        source_item = _item("综合评分")
        source_item.setForeground(QColor("#8be9fd"))
        self.table_trade.setItem(i, 4, source_item)

        # col 5: AI研判
        verdict_item = _item(ai_verdict or rec_cn)
        text = str(verdict_item.text())
        if "买入" in text:
            verdict_item.setForeground(QColor("#ff5555"))
        elif "回避" in text or "卖出" in text:
            verdict_item.setForeground(QColor("#50fa7b"))
        elif "观望" in text:
            verdict_item.setForeground(QColor("#f1fa8c"))
        self.table_trade.setItem(i, 5, verdict_item)

        # col 6: 信心 → 用综合分
        conf_item = _item(score_str)
        self.table_trade.setItem(i, 6, conf_item)

        # col 7: 走势类型 → 涨停原因
        limit_reason = str(row.get("limit_reason") or "")
        type_item = _item(limit_reason[:20])
        type_item.setForeground(QColor("#8be9fd"))
        self.table_trade.setItem(i, 7, type_item)

        # col 8: 买入时机 → 连板信息
        conti = row.get("continuous_days")
        time_str = f"{conti}连板" if conti and int(conti) > 1 else "首板" if conti else ""
        time_item = _item(time_str)
        time_item.setForeground(QColor("#f1fa8c"))
        self.table_trade.setItem(i, 8, time_item)

        # col 9: 板块
        self.table_trade.setItem(i, 9, _item(row.get("sector")))

        # col 10: AI分析逻辑
        advice_item = _item(ai_advice)
        if "买入" in ai_verdict:
            advice_item.setForeground(QColor("#ff5555"))
        elif "回避" in ai_verdict or "卖出" in ai_verdict:
            advice_item.setForeground(QColor("#50fa7b"))
        elif "观望" in ai_verdict:
            advice_item.setForeground(QColor("#f1fa8c"))
        self.table_trade.setItem(i, 10, advice_item)

    def _fill_news(self, rows: list[dict]):
        self.table_news.setUpdatesEnabled(False)
        try:
            self._fill_news_inner(rows)
        finally:
            self.table_news.setUpdatesEnabled(True)

    def _fill_news_inner(self, rows: list[dict]):
        self.table_news.setRowCount(len(rows))
        self._news_row_urls = {}
        for i, row in enumerate(rows):
            source = str(row.get("source") or "")
            level = str(row.get("level") or "")

            # 判断置顶类型
            is_cls_important = source == "财联社" and ("红色" in level or "重要" in level)
            is_head_earnings = "头部财报" in level
            is_major_global = "重大国际" in level
            is_pin_red = is_cls_important or is_head_earnings  # 红色置顶

            values = [
                row.get("time"),
                source,
                level,
                row.get("title"),
                row.get("tags"),
            ]
            for j, v in enumerate(values):
                table_item = _item(v)

                # --- 等级列 (j==2) 颜色 ---
                if j == NEWS_LEVEL_COLUMN_INDEX:
                    if "头部财报" in level:
                        table_item.setForeground(QColor("#ff5555"))
                    elif "红色" in level or "重大" in level:
                        table_item.setForeground(Qt.GlobalColor.red)
                    elif "重要" in level:
                        table_item.setForeground(
                            Qt.GlobalColor.red if source in ("财联社", "财联社国际") else QColor("#ffb86c")
                        )
                    elif level in ("美联储", "经济数据", "地缘政治"):
                        table_item.setForeground(QColor("#ffb86c"))  # 橙色
                    elif level in ("美股", "中概股", "科技产业"):
                        table_item.setForeground(QColor("#bd93f9"))  # 紫色
                    elif level in ("大宗商品", "央行政策"):
                        table_item.setForeground(QColor("#f1fa8c"))  # 黄色

                # --- 标题列 (j==3) ---
                if j == NEWS_TITLE_COLUMN_INDEX:
                    if row.get("url"):
                        font = table_item.font()
                        font.setUnderline(True)
                        table_item.setFont(font)
                        self._news_row_urls[i] = row.get("url")

                    if is_pin_red:
                        table_item.setForeground(QColor("#ff5555"))
                    elif is_major_global:
                        table_item.setForeground(QColor("#ffb86c"))
                    elif source in ("华尔街见闻", "金十数据", "东财美股", "财联社国际"):
                        table_item.setForeground(QColor("#bd93f9"))  # 国际源紫色
                    elif row.get("url"):
                        table_item.setForeground(Qt.GlobalColor.cyan)

                # --- 红色置顶行的其他列也标红 ---
                if is_pin_red and j in (0, 1, 4):
                    table_item.setForeground(QColor("#ff5555"))

                # --- AI 决策标签特殊高亮 (j==4) ---
                if j == NEWS_TAG_COLUMN_INDEX:
                    tag_text = str(v or "")
                    if is_pin_red and "AI:" not in tag_text:
                        # 已经在上面标红了
                        pass
                    elif "AI:" in tag_text:
                        if "利好" in tag_text:
                            table_item.setForeground(QColor("#ff5555"))  # 红色=利好
                        elif "利空" in tag_text:
                            table_item.setForeground(QColor("#50fa7b"))  # 绿色=利空
                        else:
                            table_item.setForeground(QColor("#f1fa8c"))  # 黄色=中性
                        # AI决策加粗显示
                        font = table_item.font()
                        font.setBold(True)
                        table_item.setFont(font)
                    elif "头部企业" in tag_text:
                        table_item.setForeground(QColor("#ff5555"))

                self.table_news.setItem(i, j, table_item)

    def _open_stock_detail(self, row: int, _column: int):
        code_item = self.table_trade.item(row, 1)
        if not code_item:
            return
        code = (code_item.text() or "").strip()
        if not code:
            return

        name_item = self.table_trade.item(row, 2)
        name = (name_item.text() if name_item else "").strip()
        try:
            dialog = StockDetailDialog(self.query, code, name, self)
            dialog.exec()
        except Exception as e:
            self._log(f"打开股票详情失败: code={code}, err={e}")

    def _open_news_source(self, row: int, _column: int):
        url = self._news_row_urls.get(row)
        if not url:
            return
        try:
            webbrowser.open(url)
            self._log(f"已打开来源链接: {url}")
        except Exception as e:
            self._log(f"打开链接失败: {e}")

    def _log(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {text}")
        self._trim_log_lines()
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _trim_log_lines(self):
        doc = self.log_text.document()
        overflow = doc.blockCount() - self._max_log_lines
        if overflow <= 0:
            return
        # 批量删除多行（比逐行删除快 10~50 倍）
        cursor = self.log_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        cursor.movePosition(cursor.MoveOperation.Down, cursor.MoveMode.KeepAnchor, overflow)
        cursor.movePosition(cursor.MoveOperation.StartOfBlock, cursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.deleteChar()  # 删除残留换行

    @staticmethod
    def _signal_type_cn(signal_type: str | None) -> str:
        mapping = {
            "buy": "买入",
            "sell": "卖出",
            "hold": "观望",
        }
        return mapping.get((signal_type or "").lower(), signal_type or "")

    @staticmethod
    def _recommendation_cn(rec: str | None) -> str:
        mapping = {
            "strong_buy": "强烈买入",
            "buy": "买入",
            "hold": "观望",
            "avoid": "回避",
        }
        return mapping.get((rec or "").lower(), rec or "")

    # ---------- 生命周期清理 ----------
    def closeEvent(self, event):
        """关闭窗口时正确释放所有资源，防止内存泄漏和僵尸线程。"""
        # 停止所有定时器
        for timer_attr in (
            "timer", "_news_refresh_timer", "collect_timer",
            "_mkt_overview_timer", "_clock_timer",
            "_predict_timer",
        ):
            t = getattr(self, timer_attr, None)
            if t is not None:
                t.stop()

        # 移除 loguru sink
        sink_id = getattr(self, "_loguru_sink_id", None)
        if sink_id is not None:
            with suppress(Exception):
                logger.remove(sink_id)

        # 等待线程池完成（最多2秒）
        try:
            self.thread_pool.clear()
            self.thread_pool.waitForDone(2000)
        except Exception:
            pass

        super().closeEvent(event)

    def _humanize_result(self, result: Any) -> str:
        """将任务结果尽量转成中文可读文本。"""
        if not isinstance(result, dict):
            return str(result)
        key_map = {
            "status": "状态",
            "message": "信息",
            "theme_count": "题材数",
            "direction": "方向",
            "top_count": "Top数量",
            "signal_count": "信号数量",
            "collect": "采集",
            "analyze": "分析",
            "score": "评分",
            "sentiment": "舆情",
            "topic": "题材",
            "global_impact": "国际影响",
            "news": "新闻",
            "market": "行情",
            "cailianshe": "财联社",
            "xueqiu": "雪球",
            "jiuyan": "韭研",
            "hot_topics": "多源热点",
            "weibo": "微博热搜",
            "douyin": "抖音热搜",
            "toutiao": "头条热搜",
            "global_news": "国际新闻",
            "us_earnings": "美股数据",
            "realtime_quotes": "实时行情",
            "limit_up_pool": "涨停池",
            "dragon_tiger": "龙虎榜",
            "northbound_flow": "北向资金",
            "all_sources_ok": "全源稳定",
            "missing_sources": "缺失源",
            "collect_attempts": "重试轮次",
            "prediction_count": "预测数",
            "orders": "订单",
            "prepared": "新建订单",
            "confirmed": "自动确认",
            "ok": "成功",
            "order_id": "订单号",
            "error": "错误",
        }

        def transform(obj: Any):
            if isinstance(obj, dict):
                out = {}
                for k, v in obj.items():
                    nk = key_map.get(k, k)
                    out[nk] = transform(v)
                return out
            return obj

        return json.dumps(transform(result), ensure_ascii=False)

