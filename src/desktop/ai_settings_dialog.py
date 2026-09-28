"""
AI 平台配置对话框 — 支持国内多平台自由切换。
"""

from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from src.analyzers.llm_platforms import AI_PLATFORMS  # noqa: E402

# 平台 key 列表（保持界面顺序）
PLATFORM_KEYS = list(AI_PLATFORMS.keys())

# ── 通用暗色样式片段 ─────────────────────────────────────────────
_DARK_SS = """
QDialog { background: #101828; color: #dbe7ff; }
QGroupBox { border: 1px solid #26385f; border-radius: 8px;
            padding-top: 18px; margin-top: 6px;
            color: #9bd8ff; font-weight: 700; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
QLabel { color: #c8dafe; font-size: 12px; }
QLineEdit, QComboBox, QSpinBox {
    background: #151f38; border: 1px solid #2a4ea1; border-radius: 6px;
    padding: 5px 8px; color: #dbe7ff; font-size: 12px; min-height: 24px;
}
QLineEdit:focus, QComboBox:focus { border-color: #8fd6ff; }
/* 下拉框弹出列表 — 必须显式设置背景和文字颜色，否则继承系统白底 */
QComboBox QAbstractItemView {
    background: #151f38; color: #dbe7ff; border: 1px solid #2a4ea1;
    selection-background-color: #245599; selection-color: #ffffff;
    padding: 4px; outline: none;
}
QComboBox QAbstractItemView::item {
    padding: 4px 8px; min-height: 22px;
}
QComboBox QAbstractItemView::item:hover {
    background: #1a3b6e; color: #ffffff;
}
QComboBox::drop-down {
    border: none; width: 24px;
}
QComboBox::down-arrow {
    image: none; border-left: 5px solid transparent; border-right: 5px solid transparent;
    border-top: 6px solid #8fd6ff; margin-right: 6px;
}
QPushButton {
    background: #1a3b6e; color: #dbe7ff; border-radius: 6px;
    padding: 6px 16px; font-size: 12px; font-weight: 600;
}
QPushButton:hover { background: #245599; }
QPushButton#btnSave { background: #1e6b3a; }
QPushButton#btnSave:hover { background: #28874d; }
QPushButton#btnTest { background: #6a4c11; }
QPushButton#btnTest:hover { background: #8a6318; }
QSlider::groove:horizontal { background: #26385f; height: 6px; border-radius: 3px; }
QSlider::handle:horizontal {
    background: #4da6ff; width: 14px; height: 14px;
    margin: -4px 0; border-radius: 7px;
}
"""


class AISettingsDialog(QDialog):
    """AI 平台配置对话框。"""

    # 保存成功后发射，携带新的 llm 配置 dict
    settings_saved = pyqtSignal(dict)

    def __init__(self, current_llm_cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 平台配置")
        self.setMinimumSize(520, 480)
        self.setStyleSheet(_DARK_SS)

        self._current_cfg = current_llm_cfg or {}
        self._building = True  # 防止初始化时触发 slot
        self._build_ui()
        self._load_current()
        self._building = False

    # ── 构建界面 ───────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── 主力模型 ──
        grp_primary = QGroupBox("主力 AI 模型")
        form1 = QFormLayout(grp_primary)
        form1.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_platform = QComboBox()
        for k in PLATFORM_KEYS:
            self.combo_platform.addItem(AI_PLATFORMS[k]["name"], k)
        self.combo_platform.currentIndexChanged.connect(self._on_platform_changed)
        form1.addRow("AI 平台:", self.combo_platform)

        self.edit_api_key = QLineEdit()
        self.edit_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_api_key.setPlaceholderText("输入该平台的 API Key")
        form1.addRow("API Key:", self.edit_api_key)

        self.edit_base_url = QLineEdit()
        self.edit_base_url.setPlaceholderText("API 地址（选择平台后自动填充）")
        form1.addRow("Base URL:", self.edit_base_url)

        self.combo_model = QComboBox()
        self.combo_model.setEditable(True)
        form1.addRow("模型:", self.combo_model)

        # temperature
        temp_row = QHBoxLayout()
        self.slider_temp = QSlider(Qt.Orientation.Horizontal)
        self.slider_temp.setRange(0, 20)  # 0.0 ~ 2.0, step=0.1
        self.slider_temp.setValue(3)
        self.label_temp = QLabel("0.3")
        self.slider_temp.valueChanged.connect(
            lambda v: self.label_temp.setText(f"{v / 10:.1f}")
        )
        temp_row.addWidget(self.slider_temp)
        temp_row.addWidget(self.label_temp)
        form1.addRow("Temperature:", temp_row)

        # max_tokens
        self.spin_tokens = QSpinBox()
        self.spin_tokens.setRange(512, 32768)
        self.spin_tokens.setSingleStep(512)
        self.spin_tokens.setValue(4096)
        form1.addRow("Max Tokens:", self.spin_tokens)

        layout.addWidget(grp_primary)

        # ── 备用模型 ──
        grp_backup = QGroupBox("备用 AI 模型（可选，主力失败时自动切换）")
        form2 = QFormLayout(grp_backup)
        form2.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_backup_platform = QComboBox()
        self.combo_backup_platform.addItem("不使用备用模型", "")
        for k in PLATFORM_KEYS:
            self.combo_backup_platform.addItem(AI_PLATFORMS[k]["name"], k)
        self.combo_backup_platform.currentIndexChanged.connect(
            self._on_backup_platform_changed
        )
        form2.addRow("备用平台:", self.combo_backup_platform)

        self.edit_backup_key = QLineEdit()
        self.edit_backup_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_backup_key.setPlaceholderText("备用平台 API Key（留空则不启用）")
        form2.addRow("API Key:", self.edit_backup_key)

        self.edit_backup_url = QLineEdit()
        form2.addRow("Base URL:", self.edit_backup_url)

        self.combo_backup_model = QComboBox()
        self.combo_backup_model.setEditable(True)
        form2.addRow("模型:", self.combo_backup_model)

        layout.addWidget(grp_backup)

        # ── 按钮区 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_test = QPushButton("测试连接")
        self.btn_test.setObjectName("btnTest")
        self.btn_test.clicked.connect(self._test_connection)
        btn_row.addWidget(self.btn_test)

        self.btn_save = QPushButton("保存并应用")
        self.btn_save.setObjectName("btnSave")
        self.btn_save.clicked.connect(self._save)
        btn_row.addWidget(self.btn_save)

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        # ── 状态 ──
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #8994b3; font-size: 11px; padding: 4px;")
        layout.addWidget(self.status_label)

    # ── 从当前配置回填界面 ─────────────────────────────────────
    def _load_current(self):
        pri = self._current_cfg.get("primary", {})
        bak = self._current_cfg.get("backup", {})

        # 根据 base_url 或 provider 匹配平台
        self._select_platform(
            self.combo_platform, pri.get("provider", ""), pri.get("base_url", "")
        )
        self.edit_api_key.setText(pri.get("api_key", ""))
        self.edit_base_url.setText(pri.get("base_url", ""))
        model = pri.get("model", "")
        idx = self.combo_model.findText(model)
        if idx >= 0:
            self.combo_model.setCurrentIndex(idx)
        else:
            self.combo_model.setEditText(model)
        self.slider_temp.setValue(int(float(pri.get("temperature", 0.3)) * 10))
        self.spin_tokens.setValue(int(pri.get("max_tokens", 4096)))

        # 备用
        if bak.get("api_key") and not bak["api_key"].startswith("your-"):
            self._select_platform(
                self.combo_backup_platform,
                bak.get("provider", ""),
                bak.get("base_url", ""),
                has_empty=True,
            )
            self.edit_backup_key.setText(bak.get("api_key", ""))
            self.edit_backup_url.setText(bak.get("base_url", ""))
            bm = bak.get("model", "")
            bi = self.combo_backup_model.findText(bm)
            if bi >= 0:
                self.combo_backup_model.setCurrentIndex(bi)
            else:
                self.combo_backup_model.setEditText(bm)

    @staticmethod
    def _select_platform(combo: QComboBox, provider: str, base_url: str, has_empty: bool = False):
        """根据 provider 或 base_url 匹配下拉选项。"""
        for i in range(combo.count()):
            key = combo.itemData(i)
            if not key:
                continue
            if key == provider:
                combo.setCurrentIndex(i)
                return
            plat = AI_PLATFORMS.get(key, {})
            if plat.get("base_url") and plat["base_url"] in (base_url or ""):
                combo.setCurrentIndex(i)
                return

    # ── 平台切换 → 自动填充 URL & 模型 ─────────────────────────
    def _on_platform_changed(self, idx: int):
        if self._building:
            return
        key = self.combo_platform.itemData(idx) or ""
        plat = AI_PLATFORMS.get(key, {})
        if plat.get("base_url"):
            self.edit_base_url.setText(plat["base_url"])
        self.combo_model.clear()
        for m in plat.get("models", []):
            self.combo_model.addItem(m)
        default = plat.get("default_model", "")
        if default:
            self.combo_model.setEditText(default)

    def _on_backup_platform_changed(self, idx: int):
        if self._building:
            return
        key = self.combo_backup_platform.itemData(idx) or ""
        if not key:
            self.edit_backup_url.clear()
            self.combo_backup_model.clear()
            return
        plat = AI_PLATFORMS.get(key, {})
        if plat.get("base_url"):
            self.edit_backup_url.setText(plat["base_url"])
        self.combo_backup_model.clear()
        for m in plat.get("models", []):
            self.combo_backup_model.addItem(m)
        default = plat.get("default_model", "")
        if default:
            self.combo_backup_model.setEditText(default)

    # ── 测试连接 ──────────────────────────────────────────────
    def _test_connection(self):
        api_key = self.edit_api_key.text().strip()
        base_url = self.edit_base_url.text().strip()
        model = self.combo_model.currentText().strip()
        if not api_key or not base_url or not model:
            self.status_label.setText("请填写完整的 API Key、Base URL 和模型名称")
            self.status_label.setStyleSheet("color: #ff5555; font-size: 11px;")
            return

        self.status_label.setText("正在测试连接…")
        self.status_label.setStyleSheet("color: #f8d56a; font-size: 11px;")
        self.btn_test.setEnabled(False)

        # 在线程中执行以避免阻塞 UI
        from PyQt6.QtCore import QRunnable, QThreadPool

        class TestRunner(QRunnable):
            def __init__(inner_self):
                super().__init__()
                inner_self.ok = False
                inner_self.msg = ""

            def run(inner_self):
                try:
                    from openai import OpenAI
                    client = OpenAI(api_key=api_key, base_url=base_url)
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": "你好，请回复OK"}],
                        max_tokens=20,
                        timeout=15,
                    )
                    text = resp.choices[0].message.content or ""
                    tokens = resp.usage.total_tokens if resp.usage else 0
                    inner_self.ok = True
                    inner_self.msg = f"连接成功! 回复: {text[:30]}  (tokens: {tokens})"
                except Exception as e:
                    inner_self.msg = f"连接失败: {e}"

        runner = TestRunner()

        def on_done():
            self.btn_test.setEnabled(True)
            if runner.ok:
                self.status_label.setText(runner.msg)
                self.status_label.setStyleSheet("color: #50fa7b; font-size: 11px;")
            else:
                self.status_label.setText(runner.msg)
                self.status_label.setStyleSheet("color: #ff5555; font-size: 11px;")

        runner.setAutoDelete(False)
        from PyQt6.QtCore import QTimer
        # Poll for completion
        QThreadPool.globalInstance().start(runner)
        self._test_runner = runner

        def _poll():
            if QThreadPool.globalInstance().activeThreadCount() == 0 or not self._test_runner:
                on_done()
            else:
                QTimer.singleShot(300, _poll)

        QTimer.singleShot(300, _poll)

    # ── 保存 ──────────────────────────────────────────────────
    def _save(self):
        api_key = self.edit_api_key.text().strip()
        base_url = self.edit_base_url.text().strip()
        model = self.combo_model.currentText().strip()

        if not api_key:
            QMessageBox.warning(self, "提示", "请输入主力模型的 API Key")
            return
        if not base_url:
            QMessageBox.warning(self, "提示", "请输入 Base URL")
            return
        if not model:
            QMessageBox.warning(self, "提示", "请输入模型名称")
            return

        provider_key = self.combo_platform.currentData() or "custom"
        temp = self.slider_temp.value() / 10.0
        max_tokens = self.spin_tokens.value()

        new_llm = {
            "primary": {
                "provider": provider_key,
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temp,
            },
            "backup": self._current_cfg.get("backup", {}),
            # 保留其他性能参数
            "timeout_seconds": self._current_cfg.get("timeout_seconds", 45),
            "max_retries": self._current_cfg.get("max_retries", 2),
            "retry_backoff_seconds": self._current_cfg.get("retry_backoff_seconds", 1.5),
            "batch_workers": self._current_cfg.get("batch_workers", 3),
            "analysis_workers": self._current_cfg.get("analysis_workers", 3),
            "cache_enabled": self._current_cfg.get("cache_enabled", True),
            "cache_ttl_seconds": self._current_cfg.get("cache_ttl_seconds", 3600),
            "cache_path": self._current_cfg.get("cache_path", "data/llm_cache.sqlite3"),
        }

        # 备用模型
        bak_key = self.combo_backup_platform.currentData() or ""
        bak_api = self.edit_backup_key.text().strip()
        if bak_key and bak_api:
            new_llm["backup"] = {
                "provider": bak_key,
                "api_key": bak_api,
                "base_url": self.edit_backup_url.text().strip(),
                "model": self.combo_backup_model.currentText().strip(),
                "max_tokens": max_tokens,
                "temperature": temp,
            }
        elif not bak_api:
            new_llm["backup"] = {
                "provider": "",
                "api_key": "",
                "base_url": "",
                "model": "",
            }

        # 写入 settings.yaml
        try:
            self._save_to_yaml(new_llm)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法写入配置文件:\n{e}")
            return

        self.settings_saved.emit(new_llm)
        self.status_label.setText("已保存并应用!")
        self.status_label.setStyleSheet("color: #50fa7b; font-size: 11px;")
        logger.info(
            f"[AI设置] 已切换到 {AI_PLATFORMS.get(provider_key, {}).get('name', provider_key)} "
            f"model={model}"
        )
        self.accept()

    @staticmethod
    def _save_to_yaml(new_llm: dict):
        """将新的 llm 配置写回 settings.yaml。"""
        from src.settings_store import save_section

        save_section("llm", new_llm)
