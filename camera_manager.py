import os
import time
import threading
import logging
import queue
import traceback
import cv2

# Set OpenCV FFMPEG transport
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

logger = logging.getLogger("CameraManager")
err_logger = logging.getLogger("errors")

class CameraManager:
    """
    Thread 1: RTSP and local camera capture thread.
    Pushes BGR frames to the raw_frame_queue using non-blocking, bounded operations.
    Handles TCP transport, auto-reconnection countdowns, and socket test connections.
    """
    def __init__(self, source_url: str, raw_frame_queue: queue.Queue = None, name: str = "CP PLUS NVR"):
        self.source_url = source_url
        self.name = name
        self.raw_frame_queue = raw_frame_queue
        
        # State indicators
        self.is_connected = False
        self.is_running = False
        self.fps = 0.0
        self.frame_width = 0
        self.frame_height = 0
        self.reconnect_count = 0
        self.latest_frame = None
        
        # Reconnect parameters
        self.reconnect_interval = 5.0
        self.countdown_remaining = 5.0
        
        # Diagnostics
        self.last_frame_time = 0.0
        self.active_exception = ""
        
        # Thread objects
        self.lock = threading.Lock()
        self.thread = None
        self.cap = None

    def start(self):
        """Starts the capture ingestion thread."""
        with self.lock:
            if self.is_running:
                logger.warning("Camera Manager thread is already active.")
                return
            self.is_running = True
            
        self.thread = threading.Thread(target=self._capture_loop, name="CameraCaptureThread", daemon=True)
        self.thread.start()
        logger.info(f"Ingestion capture thread successfully started for source: {self.source_url}")

    def stop(self):
        """Stops the capture ingestion thread and releases resources."""
        logger.info("Stopping capture ingestion thread...")
        with self.lock:
            self.is_running = False
        if self.thread:
            self.thread.join(timeout=3.0)
        self._release_capture()
        logger.info("Capture ingestion thread stopped cleanly.")

    def _release_capture(self):
        """Releases the VideoCapture resource safely."""
        with self.lock:
            if self.cap is not None:
                try:
                    self.cap.release()
                except Exception as e:
                    logger.error(f"Error releasing VideoCapture: {e}")
                self.cap = None
            self.is_connected = False
            self.latest_frame = None

    def _connect(self) -> bool:
        """Attempts to open VideoCapture and verify connection."""
        self._release_capture()
        
        try:
            source = int(self.source_url)
        except ValueError:
            source = self.source_url

        try:
            logger.info(f"Opening connection call to source: {source}")
            if isinstance(source, int):
                # Standard DirectShow for webcams on Windows, CAP_ANY on Linux
                cap = cv2.VideoCapture(source, cv2.CAP_DSHOW if os.name == 'nt' else cv2.CAP_ANY)
            else:
                cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
            
            if cap and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    with self.lock:
                        self.cap = cap
                        self.is_connected = True
                        self.frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        self.frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        self.last_frame_time = time.time()
                        self.active_exception = ""
                    logger.info(f"Handshake successful. Stream resolution: {self.frame_width}x{self.frame_height}")
                    return True
                else:
                    cap.release()
                    logger.error("Failed to read initial handshake frame.")
            else:
                if cap:
                    cap.release()
                logger.error("Failed to open camera descriptor.")
        except Exception as e:
            tb = traceback.format_exc()
            self.active_exception = f"{str(e)}\n{tb}"
            err_logger.error(f"Exception during capture connection: {e}\n{tb}")
            logger.error(f"Failed to connect to stream: {e}")
            
        return False

    def trigger_reconnect(self):
        """Requests immediate reconnection."""
        logger.info("Forced reconnection triggered.")
        with self.lock:
            self.is_connected = False
            self.countdown_remaining = 0.0

    def _capture_loop(self):
        """Background thread loop to retrieve frames and manage queue capacity."""
        frame_count = 0
        fps_start_time = time.time()
        
        while True:
            with self.lock:
                if not self.is_running:
                    break
                    
            if not self.is_connected:
                with self.lock:
                    self.countdown_remaining = self.reconnect_interval
                
                logger.info(f"Stream offline. Waiting {self.reconnect_interval}s before retry...")
                
                # Countdown wait
                while self.countdown_remaining > 0:
                    with self.lock:
                        if not self.is_running:
                            return
                        if self.is_connected:
                            break
                    time.sleep(0.1)
                    with self.lock:
                        self.countdown_remaining = max(0.0, self.countdown_remaining - 0.1)
                
                with self.lock:
                    if not self.is_running:
                        break
                    self.reconnect_count += 1
                
                success = self._connect()
                if not success:
                    logger.warning("Reconnection attempt failed.")
                continue

            # Connected: read frame
            ret = False
            frame = None
            cap_temp = None
            with self.lock:
                cap_temp = self.cap
            
            if cap_temp is not None:
                try:
                    ret, frame = cap_temp.read()
                except Exception as e:
                    logger.debug(f"Exception during cap.read(): {e}")
                    ret = False
                
            if not ret or frame is None:
                logger.warning("Capture read returned false. Stream disconnected.")
                with self.lock:
                    self.is_connected = False
                continue
            
            # Store the latest frame and update statistics
            with self.lock:
                self.latest_frame = frame
                self.last_frame_time = time.time()
            
            # Push frame to queue with frame skipping if queue is active
            if self.raw_frame_queue is not None:
                if self.raw_frame_queue.full():
                    try:
                        self.raw_frame_queue.get_nowait() # Discard oldest
                    except queue.Empty:
                        pass
                try:
                    self.raw_frame_queue.put_nowait(frame)
                except queue.Full:
                    pass
            
            try:
                frame_count += 1
                elapsed = time.time() - fps_start_time
                if elapsed >= 5.0: # recalculate every 5 seconds (optimized)
                    with self.lock:
                        self.fps = frame_count / elapsed
                    frame_count = 0
                    fps_start_time = time.time()
                    
            except Exception as e:
                tb = traceback.format_exc()
                with self.lock:
                    self.active_exception = f"{str(e)}\n{tb}"
                    self.is_connected = False
                err_logger.error(f"Exception in capture loop: {e}\n{tb}")
                logger.error(f"Ingestion read exception: {e}")
                
            # Yield CPU briefly
            time.sleep(0.002)

    def get_frame(self) -> tuple:
        """Retrieves the latest captured frame."""
        with self.lock:
            if self.latest_frame is not None:
                return True, self.latest_frame
            return False, None

    def get_status(self) -> dict:
        """Returns snapshot of current stream properties."""
        with self.lock:
            return {
                "name": self.name,
                "connected": self.is_connected,
                "fps": round(self.fps, 1),
                "width": self.frame_width,
                "height": self.frame_height,
                "reconnect_count": self.reconnect_count,
                "last_frame_time": self.last_frame_time,
                "countdown_remaining": round(self.countdown_remaining, 1),
                "active_exception": self.active_exception,
                "source": self.source_url
            }
