# Professional Simulated Thermal CCTV & AI Fire/Smoke Detection System

This Python application simulates high-resolution thermal imaging feeds from standard color CCTV feeds (using Contrast Limited Adaptive Histogram Equalization - CLAHE and custom colormapping) and layers rule-based algorithms to detect high-temperature hotspots, flickering fire sources, and diffusing smoke plumes in real time.

It is structured using clean, object-oriented principles, runs frame acquisition and alarm systems on background threads to prevent UI performance issues, and features a professional industrial surveillance dashboard HUD.

---

## Folder Structure

```text
thermalproject/
 ├── requirements.txt         # Project dependencies
 ├── README.md                # System documentation
 ├── camera_manager.py        # Threaded RTSP/Webcam camera manager
 ├── thermal_engine.py        # Simulated thermal colormapping engine
 ├── hotspot_detector.py      # Adaptive thresholding and hotspot tracker
 ├── fire_detector.py         # HSV, motion, and hotspot correlation detector
 ├── smoke_detector.py        # MOG2 background, gray and shape complexity detector
 ├── alert_manager.py         # Threaded media recorder and winsound buzzer
 ├── dashboard.py             # Sleek dashboard overlay engine
 ├── main.py                  # CLI argument parser and pipeline coordinator
 ├── snapshots/               # Directory for manual snapshots
 └── incidents/               # Auto-generated incident logs and videos
```

---

## Installation Requirements

This system runs on Python 3.8 or higher.

1. Clone or open this project folder in your command window.
2. Install the requirements via `pip`:
   ```bash
   pip install -r requirements.txt
   ```

---

## CCTV Stream Configuration

The default stream parameters are configured for your **CP Plus CCTV/NVR** system.



To customize this or connect to Dahua or Hikvision streams, you can pass command line arguments or modify the defaults directly in `main.py`.

---

## Running the Application

### 1. Default NVR Stream Execution
Runs the system trying to ingest from the default CP Plus RTSP IP configuration:
```bash
python main.py
```

### 2. Local Webcam Fallback
If you are testing in an environment without physical CCTV/NVR network access, you can run the system using your PC's built-in webcam as the source:
```bash
python main.py --webcam
```

### 3. Custom Stream or Video Source
Specify any alternative RTSP address or path to a static `.mp4` file:
```bash
python main.py --source "rtsp://user:pass@192.168.1.100:554/live"
```
Or for a different camera IP:
```bash
python main.py --ip "192.168.1.50"
```

---

## Keyboard Controls & Hotkeys

While the surveillance window is selected, use these keys to interact with the dashboard:

| Key | Action | Description |
|---|---|---|
| **`Q`** | Quit | Safely stops threads and exits the application |
| **`R`** | Reconnect | Manually triggers reconnection to the RTSP camera source |
| **`S`** | Save Snapshot | Captures a high-resolution snapshot in `snapshots/` folder |
| **`1`** | Inferno Mode | Switches simulated thermal feed to the **Inferno** colormap (Default) |
| **`2`** | Jet Mode | Switches simulated thermal feed to the **Jet** colormap |
| **`3`** | Turbo Mode | Switches simulated thermal feed to the **Turbo** colormap |
| **`H`** | Toggle Hotspots | Enables/disables adaptive hotspot detection overlay |
| **`F`** | Toggle Fire | Enables/disables composite fire detection and tracking overlay |
| **`M`** | Toggle Smoke | Enables/disables motion-gray smoke plume detection overlay |

---

## AI Upgrade Ready (YOLO Integration)

To replace the rule-based OpenCV detectors with YOLO-based models later, refer to the comments and structure inside:
* `fire_detector.py` -> `YOLOFireDetector(BaseAIModel)`
* `smoke_detector.py` -> `YOLOSmokeDetector(BaseAIModel)`

To activate YOLO:
1. Install Ultralytics: `pip install ultralytics`
2. Uncomment the import statements and the `load_model`/`predict` calls inside `YOLOFireDetector` and `YOLOSmokeDetector`.
3. In `main.py`, uncomment the YOLO instantiation blocks:
   ```python
   # yolo_fire = YOLOFireDetector("models/fire.pt")
   # fire_det.set_yolo_model(yolo_fire)
   ```

---

## Alert Recording Files

When fire alerts are confirmed, the system saves files within structured timestamp folders under `incidents/`:
```text
incidents/
 └── YYYY-MM-DD_HH-MM-SS/
      ├── snapshot.jpg      # The frame which triggered the alert
      ├── incident.mp4      # 13-second video clip (5s pre-buffer + 8s post-buffer)
      └── report.txt        # Text summary of date, time, and peak confidence ratings
```
The alert engine also plays an asynchronous warning siren (high/low alternating frequencies) utilizing the system audio until the fire hazard ceases or fire detection is disabled.
