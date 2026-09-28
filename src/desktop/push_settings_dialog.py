"""
推送设置对话框：企业微信、钉钉、飞书机器人和邮件的开关与地址，按消息类型的推送路由，免打扰时段；
可以检查配置、给单个渠道发测试消息，保存时把 notifier 段写回 config/settings.yaml（文件里的注释会丢失）。
"""

from __future__ import annotations

import copy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from src.notifier import CHANNEL_LABELS, MESSAGE_KINDS, diagnose, test_channel
from src.notifier.settings import parse_quiet_hours, save_notifier_settings

WEBHOOK_CHANNELS = ("wechat", "dingtalk", "feishu")
SECRET_CHANNELS = ("dingtalk", "feishu")


class PushSettingsDialog(QDialog):
    settings_saved = pyqtSignal(dict)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("推送设置")
        self.resize(760, 640)
        self.config = config
        notifier = copy.deepcopy(config.get("notifier") or {})
        layout = QVBoxLayout(self)
        self.fields: dict[str, dict] = {}

        for name in WEBHOOK_CHANNELS:
            cfg = notifier.get(name) or {}
            box = QGroupBox(CHANNEL_LABELS[name] + "机器人")
            form = QFormLayout(box)
            enabled = QCheckBox("启用")
            enabled.setChecked(bool(cfg.get("enabled")))
            url = QLineEdit(str(cfg.get("webhook_url") or ""))
            url.setPlaceholderText("在群设置 → 群机器人里复制 Webhook 地址")
            row = QHBoxLayout()
            row.addWidget(enabled)
            row.addWidget(self._test_button(name))
            form.addRow(row)
            form.addRow("Webhook", url)
            fields = {"enabled": enabled, "webhook_url": url}
            if name in SECRET_CHANNELS:
                secret = QLineEdit(str(cfg.get("secret") or ""))
                secret.setPlaceholderText("安全设置选了加签/签名校验时填写，否则留空")
                form.addRow("加签密钥", secret)
                fields["secret"] = secret
            self.fields[name] = fields
            layout.addWidget(box)

        mail = notifier.get("email") or {}
        box = QGroupBox("邮件（SMTP）")
        form = QFormLayout(box)
        enabled = QCheckBox("启用")
        enabled.setChecked(bool(mail.get("enabled")))
        row = QHBoxLayout()
        row.addWidget(enabled)
        row.addWidget(self._test_button("email"))
        form.addRow(row)
        host = QLineEdit(str(mail.get("smtp_host") or ""))
        port = QSpinBox()
        port.setRange(1, 65535)
        port.setValue(int(mail.get("smtp_port") or 465))
        ssl = QCheckBox("SSL（465 端口）；不勾选时使用 STARTTLS（587 端口）")
        ssl.setChecked(bool(mail.get("use_ssl", True)))
        user = QLineEdit(str(mail.get("username") or ""))
        password = QLineEdit(str(mail.get("password") or ""))
        password.setEchoMode(QLineEdit.EchoMode.Password)
        password.setPlaceholderText("QQ/163 邮箱填授权码")
        sender = QLineEdit(str(mail.get("sender") or ""))
        sender.setPlaceholderText("为空时使用账号")
        to = mail.get("to") or []
        to_edit = QLineEdit(", ".join(to) if isinstance(to, list) else str(to))
        to_edit.setPlaceholderText("多个收件人用逗号分隔")
        for label, widget in (("SMTP 服务器", host), ("端口", port), ("", ssl), ("账号", user), ("密码/授权码", password),
                              ("发件人", sender), ("收件人", to_edit)):
            form.addRow(label, widget)
        self.fields["email"] = {"enabled": enabled, "smtp_host": host, "smtp_port": port, "use_ssl": ssl, "username": user,
                                "password": password, "sender": sender, "to": to_edit}
        layout.addWidget(box)

        routes_box = QGroupBox("推送路由：每类消息推送到哪些渠道（都不勾选 = 推送到全部已启用渠道）")
        grid = QGridLayout(routes_box)
        routes = notifier.get("routes") or {}
        self.route_boxes: dict[str, dict[str, QCheckBox]] = {}
        for col, name in enumerate(CHANNEL_LABELS, start=1):
            grid.addWidget(QLabel(CHANNEL_LABELS[name]), 0, col, alignment=Qt.AlignmentFlag.AlignCenter)
        for row_idx, (kind, label) in enumerate(MESSAGE_KINDS.items(), start=1):
            grid.addWidget(QLabel(label), row_idx, 0)
            self.route_boxes[kind] = {}
            for col, name in enumerate(CHANNEL_LABELS, start=1):
                check = QCheckBox()
                check.setChecked(name in (routes.get(kind) or []))
                grid.addWidget(check, row_idx, col, alignment=Qt.AlignmentFlag.AlignCenter)
                self.route_boxes[kind][name] = check
        layout.addWidget(routes_box)

        quiet = notifier.get("quiet_hours") or []
        self.quiet_edit = QLineEdit("-".join(quiet) if len(quiet) == 2 else "")
        self.quiet_edit.setPlaceholderText("如 22:00-08:00，期间只推送紧急提醒；留空不启用")
        quiet_form = QFormLayout()
        quiet_form.addRow("免打扰时段", self.quiet_edit)
        layout.addLayout(quiet_form)

        self.check_label = QLabel("")
        self.check_label.setWordWrap(True)
        layout.addWidget(self.check_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        check_btn = buttons.addButton("检查配置", QDialogButtonBox.ButtonRole.ActionRole)
        check_btn.clicked.connect(self._check)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._check()

    def _test_button(self, name: str) -> QPushButton:
        btn = QPushButton("发送测试消息")
        btn.clicked.connect(lambda: self._test(name))
        return btn

    def notifier_values(self) -> dict:
        values: dict = {}
        for name in WEBHOOK_CHANNELS:
            f = self.fields[name]
            values[name] = {"enabled": f["enabled"].isChecked(), "webhook_url": f["webhook_url"].text().strip()}
            if "secret" in f:
                values[name]["secret"] = f["secret"].text().strip()
        f = self.fields["email"]
        values["email"] = {
            "enabled": f["enabled"].isChecked(), "smtp_host": f["smtp_host"].text().strip(), "smtp_port": f["smtp_port"].value(),
            "use_ssl": f["use_ssl"].isChecked(), "username": f["username"].text().strip(), "password": f["password"].text(),
            "sender": f["sender"].text().strip(),
            "to": [x.strip() for x in f["to"].text().replace("；", ",").replace(";", ",").split(",") if x.strip()],
        }
        values["routes"] = {kind: [name for name, check in boxes.items() if check.isChecked()] for kind, boxes in self.route_boxes.items()}
        values["quiet_hours"] = parse_quiet_hours(self.quiet_edit.text())
        return values

    def _current_config(self) -> dict:
        return {**self.config, "notifier": {**(self.config.get("notifier") or {}), **self.notifier_values()}}

    def _check(self):
        result = diagnose(self._current_config())
        lines = []
        for c in result["channels"]:
            state = "已启用" if c["enabled"] else "未启用"
            if c["enabled"] or (c["issues"] and c["configured"]):  # 已启用的，或填了地址但有问题的
                lines.append(f"{c['label']}（{state}）：" + ("；".join(c["issues"]) if c["issues"] else "配置正常"))
        lines += result["routes"]
        if self.quiet_edit.text().strip() and not parse_quiet_hours(self.quiet_edit.text()):
            lines.append("免打扰时段格式应为 22:00-08:00")
        self.check_label.setText("\n".join(lines) or "还没有启用任何推送渠道")

    def _test(self, name: str):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = test_channel(self._current_config(), name)
        finally:
            QApplication.restoreOverrideCursor()
        if result["ok"]:
            QMessageBox.information(self, "推送测试", f"{CHANNEL_LABELS[name]}测试消息已发送，请查收。")
        else:
            QMessageBox.warning(self, "推送测试", f"{CHANNEL_LABELS[name]}发送失败：{result['error']}")

    def _save(self):
        values = self.notifier_values()
        save_notifier_settings(values)
        self.settings_saved.emit(values)
        self.accept()
