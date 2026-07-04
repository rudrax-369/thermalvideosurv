import os
import logging
import sys

def setup_logging():
    """
    Sets up the logging system, creating the logs/ directory if it doesn't exist.
    Routes logs to:
      - logs/application.log (App state, general info)
      - logs/camera.log (OpenCV calls, frame timing, connection events)
      - logs/alerts.log (Alert trigger state, siren events)
      - logs/errors.log (Detailed traceback strings for exceptions, ERROR and CRITICAL)
    Also logs everything to stdout.
    """
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    # Standard formatter
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] (%(name)s) %(message)s")

    # Define Log Files
    app_log_path = os.path.join(log_dir, "application.log")
    cam_log_path = os.path.join(log_dir, "camera.log")
    alt_log_path = os.path.join(log_dir, "alerts.log")
    err_log_path = os.path.join(log_dir, "errors.log")

    # Clear root handlers if already configured
    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
    root.setLevel(logging.DEBUG)

    # 1. Console Handler (Streams to Stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # 2. General Application Log File Handler (INFO and above)
    app_handler = logging.FileHandler(app_log_path, mode="a", encoding="utf-8")
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(formatter)
    
    # Filter out verbose camera frame polling logs from application.log to keep it clean
    class ApplicationFilter(logging.Filter):
        def filter(self, record):
            # Do not write camera frame-grab logs to application.log
            return record.name not in ("Camera", "CameraManager")
    app_handler.addFilter(ApplicationFilter())
    root.addHandler(app_handler)

    # 3. Camera Operations Log File Handler (DEBUG and above)
    cam_handler = logging.FileHandler(cam_log_path, mode="a", encoding="utf-8")
    cam_handler.setLevel(logging.DEBUG)
    cam_handler.setFormatter(formatter)
    
    # Only allow logs from Camera loggers
    class CameraFilter(logging.Filter):
        def filter(self, record):
            return record.name in ("Camera", "CameraManager")
    cam_handler.addFilter(CameraFilter())
    root.addHandler(cam_handler)

    # 4. Alerts Log File Handler (INFO and above)
    alt_handler = logging.FileHandler(alt_log_path, mode="a", encoding="utf-8")
    alt_handler.setLevel(logging.INFO)
    alt_handler.setFormatter(formatter)
    
    class AlertFilter(logging.Filter):
        def filter(self, record):
            return record.name == "Alerts"
    alt_handler.addFilter(AlertFilter())
    root.addHandler(alt_handler)

    # 5. Errors and Exceptions Log File (ERROR and above)
    err_handler = logging.FileHandler(err_log_path, mode="a", encoding="utf-8")
    err_handler.setLevel(logging.ERROR)
    err_handler.setFormatter(formatter)
    root.addHandler(err_handler)

    logging.info("Logging infrastructure successfully initialized.")
