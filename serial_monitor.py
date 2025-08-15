import sys
import threading
import queue
import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QSize
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QPen
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QPlainTextEdit, QLineEdit,
    QCheckBox, QFileDialog, QStatusBar, QFrame, QSizePolicy
)

import serial
import serial.tools.list_ports


def resource_path(relative_path: str) -> str:
    """Return absolute path to resource for dev and PyInstaller executables.

    When packaged with PyInstaller (onefile), data is unpacked to sys._MEIPASS.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / relative_path)
    return str(Path(__file__).resolve().parent / relative_path)


def load_logo(path: str = "icon.jpg", size: int = 28) -> QPixmap:
    pm = QPixmap(path)
    if not pm.isNull():
        return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    # Fallback (old dummy)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#2563eb"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.setPen(QPen(Qt.white))
    f = QFont()
    f.setBold(True)
    f.setPointSizeF(size * 0.55)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "S")
    p.end()
    return pm


class SerialReader(threading.Thread):
    def __init__(self, ser, out_queue, stop_event):
        super().__init__(daemon=True)
        self.ser = ser
        self.out_queue = out_queue
        self.stop_event = stop_event

    def run(self):
        while not self.stop_event.is_set():
            try:
                line = self.ser.readline()
                if line:
                    try:
                        text = line.decode(errors="replace")
                    except Exception:
                        text = str(line)
                    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    self.out_queue.put((ts, text))
            except Exception as e:
                self.out_queue.put((None, f"[ERROR] {e}\n"))
                break


class SerialMonitor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gpro Serial Monitor")
        self.resize(980, 640)

        # Load external logo (bundled with PyInstaller via --add-data)
        self.logo_pm = load_logo(resource_path("icon.jpg"), 28)
        self.setWindowIcon(QIcon(self.logo_pm))

        self.ser = None
        self.reader = None
        self.queue = queue.Queue()
        self.stop_event = threading.Event()

        # ===== Root layout =====
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ===== Header with logo (top-left) =====
        header = QHBoxLayout()
        header.setSpacing(10)

        logo_lbl = QLabel()
        logo_lbl.setPixmap(self.logo_pm)
        logo_lbl.setFixedSize(QSize(28, 28))

        title_lbl = QLabel("Gpro Serial Monitor")
        title_font = title_lbl.font()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_lbl.setFont(title_font)

        header.addWidget(logo_lbl, 0, Qt.AlignLeft)
        header.addWidget(title_lbl, 0, Qt.AlignLeft)
        header.addStretch(1)

        # subtle divider
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)

        # ===== Controls row (ports/baud/actions) =====
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.port_box = QComboBox()
        self.port_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_ports)

        self.baud_box = QComboBox()
        for b in ["300","600","1200","2400","4800","9600","19200","38400","57600","115200","230400","460800","921600"]:
            self.baud_box.addItem(b)
        self.baud_box.setCurrentText("115200")

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connection)

        self.timestamps_chk = QCheckBox("Timestamps")
        self.timestamps_chk.setChecked(True)

        self.autoscroll_chk = QCheckBox("Autoscroll")
        self.autoscroll_chk.setChecked(True)

        self.keep_text_chk = QCheckBox("Keep after send")
        self.keep_text_chk.setChecked(False)

        self.save_btn = QPushButton("Save Log")
        self.save_btn.clicked.connect(self.save_log)

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear_log)

        controls.addWidget(QLabel("Port:"))
        controls.addWidget(self.port_box, 2)
        controls.addWidget(self.refresh_btn)
        controls.addSpacing(12)
        controls.addWidget(QLabel("Baud:"))
        controls.addWidget(self.baud_box)
        controls.addSpacing(12)
        controls.addWidget(self.connect_btn)
        controls.addStretch(1)
        controls.addWidget(self.timestamps_chk)
        controls.addWidget(self.autoscroll_chk)
        controls.addWidget(self.keep_text_chk)
        controls.addSpacing(12)
        controls.addWidget(self.save_btn)
        controls.addWidget(self.clear_btn)

        # ===== Console =====
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        # Light theme console: white bg, nearly-black text, monospaced
        self.console.setStyleSheet(
            "QPlainTextEdit { background-color: #ffffff; color: #111111; font-family: Menlo, Consolas, 'Courier New', monospace; font-size: 12px; }"
        )

        # ===== Send row =====
        send = QHBoxLayout()
        send.setSpacing(8)
        self.send_edit = QLineEdit()
        self.send_edit.setPlaceholderText("Type text to send… (Enter to send)")
        self.send_edit.returnPressed.connect(self.send_text)

        self.eol_box = QComboBox()
        self.eol_box.addItems(["None", "\\n", "\\r", "\\r\\n"])

        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send_text)

        send.addWidget(QLabel("Send:"))
        send.addWidget(self.send_edit, 3)
        send.addWidget(QLabel("EOL:"))
        send.addWidget(self.eol_box)
        send.addWidget(self.send_btn)

        # Assemble layout
        root.addLayout(header)
        root.addWidget(line)
        root.addLayout(controls)
        root.addWidget(self.console, 1)
        root.addLayout(send)

        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # Poll queue
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.flush_queue)
        self.timer.start()

        self.refresh_ports()

    # ---------- Serial helpers ----------
    def refresh_ports(self):
        self.port_box.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.port_box.addItem(f"{p.device} — {p.description}", userData=p.device)
        if self.port_box.count() == 0:
            self.port_box.addItem("(No ports found)")

    def toggle_connection(self):
        if self.ser and self.ser.is_open:
            self.disconnect_serial()
        else:
            self.connect_serial()

    def connect_serial(self):
        if self.port_box.count() == 0:
            self.append_text("[INFO] No ports to open.\n")
            return
        device = self.port_box.currentData()
        if not device or isinstance(device, str) and device.startswith("("):
            device = (self.port_box.currentText() or "").split(" ")[0]
        baud = int(self.baud_box.currentText())
        try:
            self.ser = serial.Serial(device, baudrate=baud, timeout=0.2)
        except Exception as e:
            self.append_text(f"[ERROR] Could not open {device} @ {baud}: {e}\n")
            self.status.showMessage(f"Failed to connect: {e}", 5000)
            return

        self.stop_event.clear()
        self.reader = SerialReader(self.ser, self.queue, self.stop_event)
        self.reader.start()
        self.connect_btn.setText("Disconnect")
        self.port_box.setEnabled(False)
        self.status.showMessage(f"Connected: {device} @ {baud}", 3000)

    def disconnect_serial(self):
        try:
            self.stop_event.set()
            if self.reader and self.reader.is_alive():
                self.reader.join(timeout=1.0)
        except Exception:
            pass
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.reader = None
        self.ser = None
        self.connect_btn.setText("Connect")
        self.port_box.setEnabled(True)
        self.status.showMessage("Disconnected", 2000)

    def flush_queue(self):
        appended = False
        while True:
            try:
                ts, text = self.queue.get_nowait()
            except queue.Empty:
                break
            if self.timestamps_chk.isChecked() and ts:
                self.console.appendPlainText(f"[{ts}] {text.rstrip()}")
            else:
                self.console.appendPlainText(text.rstrip())
            appended = True

        if appended and self.autoscroll_chk.isChecked():
            cursor = self.console.textCursor()
            cursor.movePosition(cursor.End)
            self.console.setTextCursor(cursor)

    def append_text(self, text):
        self.console.appendPlainText(text.rstrip())
        if self.autoscroll_chk.isChecked():
            cursor = self.console.textCursor()
            cursor.movePosition(cursor.End)
            self.console.setTextCursor(cursor)

    def send_text(self):
        if not (self.ser and self.ser.is_open):
            self.status.showMessage("Not connected", 2000)
            return
        data = self.send_edit.text()
        eol = self.eol_box.currentText()
        if eol == "\\n":
            data += "\n"
        elif eol == "\\r":
            data += "\r"
        elif eol == "\\r\\n":
            data += "\r\n"

        try:
            self.ser.write(data.encode(errors="replace"))
            if not self.keep_text_chk.isChecked():
                self.send_edit.clear()
        except Exception as e:
            self.append_text(f"[ERROR] send failed: {e}\n")

    def clear_log(self):
        self.console.clear()

    def save_log(self):
        default = str(Path.home() / "serial_log.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Save Log", default, "Text Files (*.txt);;All Files (*.*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.console.toPlainText())
            self.status.showMessage(f"Saved: {path}", 3000)
        except Exception as e:
            self.status.showMessage(f"Save failed: {e}", 5000)


def main():
    app = QApplication(sys.argv)
    win = SerialMonitor()
    win.show()
    ret = app.exec()
    if win.ser and win.ser.is_open:
        win.disconnect_serial()
    sys.exit(ret)


if __name__ == "__main__":
    main()
