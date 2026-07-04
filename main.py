import sys
import os
import time
import socket
import traceback
import logging
import queue
import psutil
import cv2
import numpy as np
from datetime import datetime

from PyQt6.QtCore import QTimer, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QGroupBox, QListWidget,
    QFormLayout, QProgressBar, QSplitter, QCheckBox
)

# Setup logging infrastructure
from logger_config import setup_logging
setup_logging()

logger = logging.getLogger("SurveillanceSystem")
err_logger = logging.getLogger("errors")

from camera_manager import CameraManager

# Thread-safe log queue for UI console
log_queue = queue.Queue()

class QueueLogHandler(logging.Handler):
    """Logs entries into a queue so the GUI thread can pull and display them safely."""
    def __init__(self, msg_queue):
        super().__init__()
        self.msg_queue = msg_queue
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    def emit(self, record):
        try:
            msg = self.format(record)
            self.msg_queue.put(msg)
        except Exception:
            pass

# Add this handler to root logger
logging.getLogger().addHandler(QueueLogHandler(log_queue))


class DiagnosticWorker(QThread):
    """Runs diagnostic checks at startup in background."""
    progress = pyqtSignal(str, str, bool)
    finished = pyqtSignal(list)

    def __init__(self, nvr_ip="192.168.1.245", rtsp_port=554):
        super().__init__()
        self.nvr_ip = nvr_ip
        self.rtsp_port = rtsp_port

    def run(self):
        logger.info("Startup diagnostics thread started.")
        
        # 1. Check OpenCV
        try:
            self.progress.emit("Checking OpenCV...", f"Version {cv2.__version__}", True)
        except Exception as e:
            self.progress.emit("Checking OpenCV...", f"Failed: {str(e)}", False)

        # 2. Check FFMPEG support
        try:
            backends = cv2.videoio_registry.getCameraBackends()
            has_ffmpeg = any("FFMPEG" in cv2.videoio_registry.getBackendName(b).upper() for b in backends)
            self.progress.emit("Checking FFMPEG...", "FFMPEG backend available" if has_ffmpeg else "FFMPEG missing", has_ffmpeg)
        except Exception as e:
            self.progress.emit("Checking FFMPEG...", f"Failed: {str(e)}", False)

        # 3. Check Camera Devices
        self.progress.emit("Checking Camera Devices...", "Scanning webcams (0-5)...", True)
        available_cameras = []
        for i in range(6):
            try:
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW if os.name == 'nt' else cv2.CAP_ANY)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        available_cameras.append(i)
                    cap.release()
            except Exception:
                pass
        
        cam_desc = f"Found webcams: {available_cameras}" if available_cameras else "No local webcams found"
        self.progress.emit("Checking Camera Devices...", cam_desc, len(available_cameras) > 0)

        # 4. Check socket connect to NVR
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            result = sock.connect_ex((self.nvr_ip, self.rtsp_port))
            sock.close()
            
            if result == 0:
                self.progress.emit("Checking RTSP Connectivity...", f"NVR reachable on port {self.rtsp_port}", True)
            else:
                self.progress.emit("Checking RTSP Connectivity...", f"NVR unreachable on port {self.rtsp_port}", False)
        except Exception as e:
            self.progress.emit("Checking RTSP Connectivity...", f"Network error: {str(e)}", False)

        logger.info("Startup diagnostics thread completed.")
        self.finished.emit(available_cameras)


class MainWindow(QMainWindow):
    """Minimal stable RTSP diagnostics window client."""
    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("AI CCTV Thermal Surveillance - Stable RTSP Diagnostics Client")
        self.resize(1300, 750)
        
        self.camera_manager = None
        
        # State logic
        self.is_streaming = False
        self.thermal_unlocked = False
        self.thermal_active = False
        self.developer_mode = False
        self.active_camera_source = ""
        
        # 5-minute survival timer
        self.stream_start_time = 0.0
        self.continuous_survival_seconds = 0.0
        self.min_survival_needed = 0.0 # Unlock immediately
        
        # Timers
        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self._process_pipeline_frame)
        
        self.stats_timer = QTimer()
        self.stats_timer.timeout.connect(self._update_system_stats)
        self.stats_timer.start(1000)
        
        self.console_timer = QTimer()
        self.console_timer.timeout.connect(self._drain_log_queue)
        self.console_timer.start(100)
        
        self._init_ui()
        self._run_diagnostics()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)
        
        # Left Panel Layout
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 5, 0)
        
        # Diagnostics
        self.diag_group = QGroupBox("Startup Diagnostics")
        diag_layout = QVBoxLayout(self.diag_group)
        self.diag_labels = {}
        for check_name in [
            "Checking OpenCV...", "Checking FFMPEG...", "Checking Camera Devices...", "Checking RTSP Connectivity..."
        ]:
            lbl = QLabel(f"● {check_name} : PENDING")
            lbl.setStyleSheet("color: gray;")
            diag_layout.addWidget(lbl)
            self.diag_labels[check_name] = lbl
        left_layout.addWidget(self.diag_group)
        
        # Switcher
        self.switcher_group = QGroupBox("Camera Selection Panel")
        switcher_layout = QVBoxLayout(self.switcher_group)
        
        self.camera_list_widget = QListWidget()
        self.camera_list_widget.itemClicked.connect(self._on_camera_selected)
        switcher_layout.addWidget(self.camera_list_widget)
        
        self.scan_cameras_btn = QPushButton("Scan Local Cameras")
        self.scan_cameras_btn.clicked.connect(self._run_diagnostics)
        switcher_layout.addWidget(self.scan_cameras_btn)
        left_layout.addWidget(self.switcher_group)
        
        # Setup form
        cctv_group = QGroupBox("CCTV Setup")
        cctv_layout = QFormLayout(cctv_group)
        self.input_ip = QLineEdit("192.168.1.245")
        self.input_port = QLineEdit("554")
        self.input_user = QLineEdit("admin")
        self.input_pass = QLineEdit("admin123")
        self.input_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_chan = QLineEdit("1")
        self.input_sub = QLineEdit("0")
        
        cctv_layout.addRow("IP Address:", self.input_ip)
        cctv_layout.addRow("Port:", self.input_port)
        cctv_layout.addRow("Username:", self.input_user)
        cctv_layout.addRow("Password:", self.input_pass)
        cctv_layout.addRow("Channel:", self.input_chan)
        cctv_layout.addRow("Subtype:", self.input_sub)
        
        self.btn_add_cctv = QPushButton("ADD CAMERA")
        self.btn_add_cctv.clicked.connect(self._add_cctv_camera)
        cctv_layout.addRow(self.btn_add_cctv)
        left_layout.addWidget(cctv_group)
        
        # Developer Mode
        dev_group = QGroupBox("Developer Mode")
        dev_layout = QVBoxLayout(dev_group)
        self.chk_dev_mode = QCheckBox("Enable Live Diagnostics Info")
        self.chk_dev_mode.stateChanged.connect(self._on_dev_mode_toggled)
        dev_layout.addWidget(self.chk_dev_mode)
        left_layout.addWidget(dev_group)
        
        splitter.addWidget(left_widget)
        
        # Center Video Panel
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(5, 0, 0, 0)
        
        self.feed_label = QLabel()
        self.feed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.feed_label.setText("RTSP Surveillance Feed Off\nSelect a camera source and click 'TEST STREAM'")
        self.feed_label.setFont(QFont("Consolas", 12))
        self.feed_label.setStyleSheet("background-color: black; color: green; border: 1px solid #333;")
        self.feed_label.setMinimumSize(640, 400)
        center_layout.addWidget(self.feed_label, stretch=4)
        
        self.survival_bar = QProgressBar()
        self.survival_bar.setRange(0, 100)
        self.survival_bar.setValue(0)
        self.survival_bar.setFormat("Stream Verification: Pending Connection")
        center_layout.addWidget(self.survival_bar)
        
        controls_group = QGroupBox("Stream Controls")
        controls_layout = QHBoxLayout(controls_group)
        
        self.btn_test_stream = QPushButton("TEST STREAM")
        self.btn_test_stream.clicked.connect(self._test_stream_clicked)
        self.btn_test_stream.setStyleSheet("background-color: #2b5c8f; color: white; font-weight: bold;")
        controls_layout.addWidget(self.btn_test_stream)
        
        self.btn_thermal_mode = QPushButton("Thermal Mode")
        self.btn_thermal_mode.setEnabled(False)
        self.btn_thermal_mode.setCheckable(True)
        self.btn_thermal_mode.toggled.connect(self._on_thermal_mode_toggled)
        self.btn_thermal_mode.setStyleSheet("QPushButton:disabled { color: gray; } QPushButton:enabled { background-color: #7d3f8c; color: white; font-weight: bold; }")
        controls_layout.addWidget(self.btn_thermal_mode)
        
        self.btn_manual_snap = QPushButton("Save Manual Snapshot")
        self.btn_manual_snap.clicked.connect(self._save_manual_snapshot)
        controls_layout.addWidget(self.btn_manual_snap)
        
        self.btn_reconnect = QPushButton("Manual Reconnect")
        self.btn_reconnect.clicked.connect(self._manual_reconnect)
        controls_layout.addWidget(self.btn_reconnect)
        
        center_layout.addWidget(controls_group)
        
        # Splitter between Feed and Console
        feed_log_splitter = QSplitter(Qt.Orientation.Vertical)
        feed_log_splitter.addWidget(center_widget)
        
        console_widget = QWidget()
        console_layout = QVBoxLayout(console_widget)
        console_layout.setContentsMargins(0, 5, 0, 0)
        
        console_label = QLabel("Surveillance System Logs Console")
        console_label.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        console_layout.addWidget(console_label)
        
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setFont(QFont("Consolas", 8))
        self.log_console.setStyleSheet("background-color: #111; color: #a6e22e; border: 1px solid #444;")
        console_layout.addWidget(self.log_console)
        
        self.lbl_stats = QLabel("FPS: -- | Res: -- | CPU: -- | Memory: -- | Reconnects: 0")
        self.lbl_stats.setStyleSheet("color: cyan; background-color: #000; padding: 4px; font-weight: bold;")
        self.lbl_stats.setFont(QFont("Consolas", 9))
        console_layout.addWidget(self.lbl_stats)
        
        feed_log_splitter.addWidget(console_widget)
        feed_log_splitter.setSizes([450, 180])
        
        splitter.addWidget(feed_log_splitter)
        splitter.setSizes([320, 980])

    def _run_diagnostics(self):
        self.scan_cameras_btn.setEnabled(False)
        self.btn_add_cctv.setEnabled(False)
        self.diag_worker = DiagnosticWorker(
            nvr_ip=self.input_ip.text(),
            rtsp_port=int(self.input_port.text())
        )
        self.diag_worker.progress.connect(self._update_diagnostic_ui)
        self.diag_worker.finished.connect(self._diagnostics_finished)
        self.diag_worker.start()

    def _update_diagnostic_ui(self, check_name: str, desc: str, success: bool):
        lbl = self.diag_labels.get(check_name)
        if lbl:
            status = "PASS" if success else "FAIL"
            color = "#00ff64" if success else "#ff0000"
            lbl.setText(f"● {check_name} {status} ({desc})")
            lbl.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _diagnostics_finished(self, available_cameras: list):
        self.scan_cameras_btn.setEnabled(True)
        self.btn_add_cctv.setEnabled(True)
        
        self.camera_list_widget.clear()
        for idx in available_cameras:
            self.camera_list_widget.addItem(f"Local Webcam (Index {idx})")
        self.camera_list_widget.addItem("CP Plus CCTV Camera 1")
        
        if self.camera_list_widget.count() > 0:
            self.camera_list_widget.setCurrentRow(0)
            self._on_camera_selected(self.camera_list_widget.item(0))

    def _add_cctv_camera(self):
        ip = self.input_ip.text().strip()
        port = self.input_port.text().strip()
        user = self.input_user.text().strip()
        password = self.input_pass.text().strip()
        chan = self.input_chan.text().strip()
        sub = self.input_sub.text().strip()
        
        if not ip or not port or not user or not password:
            return
        
        cctv_name = f"CP Plus NVR - IP: {ip} (CH:{chan})"
        self.camera_list_widget.addItem(cctv_name)
        logger.info(f"CCTV stream configurations added: {cctv_name}")

    def _on_camera_selected(self, item):
        text = item.text()
        if "Local Webcam" in text:
            idx = text.split("Index ")[1].replace(")", "")
            self.active_camera_source = str(idx)
        else:
            ip = self.input_ip.text().strip()
            port = self.input_port.text().strip()
            user = self.input_user.text().strip()
            password = self.input_pass.text().strip()
            chan = self.input_chan.text().strip()
            sub = self.input_sub.text().strip()
            
            if "IP:" in text:
                ip = text.split("IP: ")[1].split(" ")[0]
                chan = text.split("CH:")[1].replace(")", "")
                
            self.active_camera_source = f"rtsp://{user}:{password}@{ip}:{port}/cam/realmonitor?channel={chan}&subtype={sub}"
        logger.info(f"Camera source changed: {self.active_camera_source}")

    def _test_stream_clicked(self):
        if self.is_streaming:
            self._stop_stream()
        else:
            self._start_stream()

    def _start_stream(self):
        if not self.active_camera_source:
            return
        self._stop_stream()
        
        self.stream_start_time = time.time()
        self.continuous_survival_seconds = 0.0
        self.survival_bar.setValue(0)
        self.thermal_unlocked = False
        self.btn_thermal_mode.setEnabled(False)
        self.btn_thermal_mode.setChecked(False)
        self.thermal_active = False

        self.camera_manager = CameraManager(source_url=self.active_camera_source)
        self.camera_manager.start()

        self.is_streaming = True
        self.btn_test_stream.setText("STOP STREAM")
        self.btn_test_stream.setStyleSheet("background-color: #8f2b2b; color: white; font-weight: bold;")
        self.poll_timer.start(30)

    def _stop_stream(self):
        self.poll_timer.stop()
        self.is_streaming = False
        if self.camera_manager:
            self.camera_manager.stop()
            self.camera_manager = None
            
        self.btn_test_stream.setText("TEST STREAM")
        self.btn_test_stream.setStyleSheet("background-color: #2b5c8f; color: white; font-weight: bold;")
        self.feed_label.setText("RTSP Surveillance Feed Off\nSelect a camera source and click 'TEST STREAM'")
        self.feed_label.setStyleSheet("background-color: black; color: green; border: 1px solid #333;")
        
        self.survival_bar.setValue(0)
        self.survival_bar.setFormat("Stream Verification: Pending Connection")
        self.btn_thermal_mode.setEnabled(False)
        self.btn_thermal_mode.setChecked(False)
        self.thermal_unlocked = False
        self.thermal_active = False

    def _manual_reconnect(self):
        if self.camera_manager:
            self.camera_manager.trigger_reconnect()

    def _on_thermal_mode_toggled(self, checked):
        self.thermal_active = checked

    def _on_dev_mode_toggled(self, state):
        self.developer_mode = (state == 2)

    def _save_manual_snapshot(self):
        if not self.is_streaming or not self.camera_manager:
            return
        ret, frame = self.camera_manager.get_frame()
        if ret and frame is not None:
            try:
                os.makedirs("snapshots", exist_ok=True)
                snap_path = os.path.join("snapshots", f"manual_snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
                cv2.imwrite(snap_path, frame)
                logger.info(f"Snapshot saved to: {snap_path}")
            except Exception as e:
                err_logger.error(f"Failed to save snapshot: {e}")

    def _drain_log_queue(self):
        while not log_queue.empty():
            try:
                msg = log_queue.get_nowait()
                self.log_console.append(msg)
                text = self.log_console.toPlainText()
                lines = text.split("\n")
                if len(lines) > 200:
                    self.log_console.setPlainText("\n".join(lines[-200:]))
            except queue.Empty:
                break

    def _update_system_stats(self):
        try:
            cpu = psutil.cpu_percent()
            process = psutil.Process(os.getpid())
            mem_mb = process.memory_info().rss / (1024 * 1024)
            
            if self.camera_manager:
                status = self.camera_manager.get_status()
                stats_text = f"FPS: {status['fps']} | Res: {status['width']}x{status['height']} | CPU: {cpu}% | Memory: {round(mem_mb, 1)}MB | Reconnects: {status['reconnect_count']}"
                if status["active_exception"]:
                    self.lbl_stats.setStyleSheet("color: red; background-color: #000; padding: 4px; font-weight: bold;")
                else:
                    self.lbl_stats.setStyleSheet("color: cyan; background-color: #000; padding: 4px; font-weight: bold;")
            else:
                stats_text = f"FPS: -- | Res: -- | CPU: {cpu}% | Memory: {round(mem_mb, 1)}MB | Reconnects: 0"
                self.lbl_stats.setStyleSheet("color: cyan; background-color: #000; padding: 4px; font-weight: bold;")
            self.lbl_stats.setText(stats_text)
        except Exception as e:
            err_logger.error(f"Failed to retrieve system stats: {e}")

    def _process_pipeline_frame(self):
        if not self.is_streaming or not self.camera_manager:
            return

        try:
            status = self.camera_manager.get_status()
            
            if not status["connected"]:
                self.stream_start_time = time.time()
                self.continuous_survival_seconds = 0.0
                self.survival_bar.setValue(0)
                countdown_val = status["countdown_remaining"]
                recon_attempt = status["reconnect_count"]
                self.survival_bar.setFormat(f"SIGNAL LOSS - Reconnecting in {countdown_val}s... (Attempt {recon_attempt})")
                if self.thermal_unlocked:
                    self.thermal_unlocked = False
                    self.btn_thermal_mode.setEnabled(False)
                    self.btn_thermal_mode.setChecked(False)
                    self.thermal_active = False
                msg = f"SIGNAL LOST - Reconnecting in {countdown_val}s... (Attempt {recon_attempt})"
                
                h_layout, w_layout = 400, 1000
                disconnected_frame = np.zeros((h_layout, w_layout, 3), dtype=np.uint8)
                for y in range(0, h_layout, 6):
                    cv2.line(disconnected_frame, (0, y), (w_layout, y), (12, 12, 12), 1)
                cv2.rectangle(disconnected_frame, (5, 5), (w_layout - 5, h_layout - 5), (0, 0, 120), 1)
                cv2.putText(disconnected_frame, "!!! SIGNAL LOSS !!!", (w_layout // 2 - 120, h_layout // 2 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)
                cv2.putText(disconnected_frame, msg, (w_layout // 2 - 170, h_layout // 2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)
                self._draw_frame_to_label(disconnected_frame)
                return

            ret, frame = self.camera_manager.get_frame()
            if not ret or frame is None:
                return

            self.continuous_survival_seconds = time.time() - self.stream_start_time
            
            if not self.thermal_unlocked and self.continuous_survival_seconds >= self.min_survival_needed:
                self.thermal_unlocked = True
                self.btn_thermal_mode.setEnabled(True)
                logger.info(">>> Stream Stability Verified: Thermal Mode Unlocked <<<")

            if self.thermal_unlocked:
                self.survival_bar.setValue(100)
                self.survival_bar.setFormat("Stream Status: Verified & Unlocked")
            else:
                self.survival_bar.setValue(0)
                self.survival_bar.setFormat("Stream Verification: Pending Connection")

            h_layout, w_layout = 400, 1000
            sub_w, sub_h = w_layout // 2, h_layout
            
            logger.debug("Calling cv2.resize")
            orig_processed = cv2.resize(frame, (sub_w, sub_h), interpolation=cv2.INTER_LINEAR)
            logger.debug("Finished cv2.resize")

            if self.thermal_active and self.thermal_unlocked:
                logger.debug("Processing local thermal colormapping")
                gray = cv2.cvtColor(orig_processed, cv2.COLOR_BGR2GRAY)
                equalized = cv2.equalizeHist(gray)
                thermal_frame = cv2.applyColorMap(equalized, cv2.COLORMAP_INFERNO)
                side_by_side = cv2.hconcat([orig_processed, thermal_frame])
            else:
                side_by_side = cv2.hconcat([orig_processed, orig_processed])

            # Draw basic stats text overlay on side-by-side feed
            conn_txt = "FEED ACTIVE"
            cv2.putText(side_by_side, f"{conn_txt} | FPS: {status['fps']} | Verification: {int(self.continuous_survival_seconds)}s/300s", 
                        (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 100), 1, cv2.LINE_AA)

            if self.developer_mode:
                self._draw_dev_overlay(side_by_side, status)

            self._draw_frame_to_label(side_by_side)

        except Exception as e:
            tb = traceback.format_exc()
            err_logger.error(f"GUI Thread loop error: {e}\n{tb}")

    def _draw_dev_overlay(self, image: np.ndarray, status: dict):
        overlay = image.copy()
        h, w = image.shape[:2]
        box_w, box_h = 300, 130
        box_x, box_y = w - box_w - 15, h - box_h - 15
        
        cv2.rectangle(overlay, (box_x, box_y), (box_x + box_w, box_y + box_h), (5, 5, 5), -1)
        cv2.addWeighted(overlay, 0.7, image, 0.3, 0, image)
        cv2.rectangle(image, (box_x, box_y), (box_x + box_w, box_y + box_h), (255, 255, 100), 1)
        
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.4
        color = (255, 255, 100)
        
        y = box_y + 20
        cv2.putText(image, ">>> DEV MODE LIVE DIAGNOSTICS <<<", (box_x + 10, y), font, font_scale, color, 1, cv2.LINE_AA)
        y += 20
        cv2.putText(image, f"OPENCV : {cv2.__version__}", (box_x + 10, y), font, font_scale, (250, 250, 250), 1, cv2.LINE_AA)
        y += 20
        cv2.putText(image, f"SOURCE : {status['source'][:35]}...", (box_x + 10, y), font, font_scale, (250, 250, 250), 1, cv2.LINE_AA)
        y += 20
        cv2.putText(image, f"FPS    : {status['fps']}", (box_x + 10, y), font, font_scale, (250, 250, 250), 1, cv2.LINE_AA)
        y += 20
        cv2.putText(image, f"SURVIV : {round(self.continuous_survival_seconds, 1)}s/300s", (box_x + 10, y), font, font_scale, (250, 250, 250), 1, cv2.LINE_AA)

    def _draw_frame_to_label(self, frame: np.ndarray):
        try:
            logger.debug("Calling cv2.cvtColor BGR2RGB")
            rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_image.shape
            bytes_per_line = ch * w
            q_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
            pix = QPixmap.fromImage(q_img)
            scaled_pix = pix.scaled(self.feed_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.feed_label.setPixmap(scaled_pix)
        except Exception as e:
            err_logger.error(f"Error drawing frame: {e}")

    def closeEvent(self, event):
        self._stop_stream()
        self.stats_timer.stop()
        self.console_timer.stop()
        event.accept()


def main():
    try:
        app = QApplication(sys.argv)
        app.setStyleSheet("""
            QMainWindow { background-color: #222222; }
            QGroupBox {
                border: 1px solid #444444;
                border-radius: 4px;
                margin-top: 1ex;
                font-weight: bold;
                color: #ffffff;
                background-color: #2d2d2d;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 3px;
                color: #00ff64;
            }
            QLabel { color: #ffffff; }
            QPushButton {
                background-color: #444444;
                color: #ffffff;
                border: 1px solid #555555;
                border-radius: 3px;
                padding: 4px;
            }
            QPushButton:hover { background-color: #555; border: 1px solid #00ff64; }
            QLineEdit {
                background-color: #333333;
                color: #ffffff;
                border: 1px solid #555555;
                border-radius: 2px;
                padding: 2px;
            }
            QListWidget { background-color: #333; color: #fff; border: 1px solid #555; }
            QListWidget::item:selected { background-color: #00ff64; color: #000; font-weight: bold; }
        """)
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        tb = traceback.format_exc()
        print(f"CRITICAL STARTUP ERROR: {e}\n{tb}", file=sys.stderr)

if __name__ == "__main__":
    main()
