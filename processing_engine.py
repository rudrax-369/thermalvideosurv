import cv2
import numpy as np
import threading
import logging
import queue
import time
import traceback

logger = logging.getLogger("ProcessingEngine")
err_logger = logging.getLogger("errors")

class ProcessingEngine:
    """
    Thread 2: Retrieves high-resolution raw frames, downsizes them to 
    display (1280x720) and analytics (640x360) resolutions, calculates 
    motion masks, and handles downstream queue routing.
    """
    def __init__(self, raw_frame_queue: queue.Queue, display_queue: queue.Queue, analytics_queue: queue.Queue):
        self.raw_frame_queue = raw_frame_queue
        self.display_queue = display_queue
        self.analytics_queue = analytics_queue
        
        self.is_running = False
        self.thread = None
        
        # State variables for motion analysis (calculated at 640x360)
        self.prev_gray = None
        
        # Diagnostics
        self.latency_ms = 0.0
        self.dropped_frames = 0
        self.processed_count = 0

    def start(self):
        """Starts the frame processing thread."""
        self.is_running = True
        self.thread = threading.Thread(target=self._processing_loop, name="FrameProcessingThread", daemon=True)
        self.thread.start()
        logger.info("Frame processing engine thread successfully started.")

    def stop(self):
        """Stops the frame processing thread."""
        logger.info("Stopping frame processing engine...")
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=3.0)
        logger.info("Frame processing engine stopped cleanly.")

    def _processing_loop(self):
        """Pulls raw frames, resizes them, calculates motion differences, and feeds display/analytics queues."""
        while self.is_running:
            try:
                # Retrieve raw frame
                try:
                    raw_frame = self.raw_frame_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                
                t_start = time.time()
                
                # 1. Downscale to Display Resolution (1280x720)
                display_frame = cv2.resize(raw_frame, (1280, 720), interpolation=cv2.INTER_AREA)
                
                # 2. Downscale to Analytics Resolution (640x360)
                analytics_frame = cv2.resize(raw_frame, (640, 360), interpolation=cv2.INTER_AREA)
                
                # 3. Calculate motion mask at 640x360 (highly optimized)
                gray_analytics = cv2.cvtColor(analytics_frame, cv2.COLOR_BGR2GRAY)
                motion_mask = np.zeros_like(gray_analytics)
                
                if self.prev_gray is not None and self.prev_gray.shape == gray_analytics.shape:
                    # Calculate frame difference
                    diff = cv2.absdiff(self.prev_gray, gray_analytics)
                    _, motion_mask = cv2.threshold(diff, 15, 255, cv2.THRESH_BINARY)
                    
                    # Morphological cleanup
                    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
                    motion_mask = cv2.morphologyEx(motion_mask, cv2.MORPH_OPEN, kernel)
                
                self.prev_gray = gray_analytics
                
                # 4. Push to Display Queue (with frame skipping logic)
                if self.display_queue.full():
                    try:
                        self.display_queue.get_nowait()
                        self.dropped_frames += 1
                    except queue.Empty:
                        pass
                self.display_queue.put_nowait(display_frame)
                
                # 5. Push to Analytics Queue (with frame skipping logic)
                analytics_packet = {
                    "frame": analytics_frame,
                    "motion_mask": motion_mask,
                    "timestamp": t_start
                }
                
                if self.analytics_queue.full():
                    try:
                        self.analytics_queue.get_nowait()
                        self.dropped_frames += 1
                    except queue.Empty:
                        pass
                self.analytics_queue.put_nowait(analytics_packet)
                
                # Update diagnostics
                self.processed_count += 1
                self.latency_ms = (time.time() - t_start) * 1000.0
                
            except Exception as e:
                tb = traceback.format_exc()
                err_logger.error(f"Exception in processing engine: {e}\n{tb}")
                logger.error(f"Processing frame error: {e}")
                time.sleep(0.01)
