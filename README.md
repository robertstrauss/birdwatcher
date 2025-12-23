# Bird-Watcher - Raspberry Pi Motion-Activated Camera

A web-based interface for a Raspberry Pi camera that provides a live video feed and records clips when motion is detected.

## Features

- **Live Video Stream**: View a low-latency HLS live stream from the Raspberry Pi camera in your web browser.
- **Motion Detection**: Automatically records video clips when motion is detected in the camera's view.
- **Web Interface**: A clean, mobile-friendly web UI to view the live stream and browse recorded clips.
- **Clip Management**:
    - Clips are displayed in a gallery with automatically generated thumbnails.
    - Paginated gallery for browsing a large number of clips.
    - Download or "soft delete" clips directly from the interface (deleted clips are moved to the system's temporary directory).
- **Configuration**: Adjust motion sensitivity and clip duration from the web interface.
- **System Status**: A status page to confirm that the web server and camera streamer are running correctly.

## Requirements

- A Raspberry Pi (3B+ or later recommended) with a compatible camera module (CSI or USB).
- Raspberry Pi OS (Bullseye or later) with the `libcamera` stack enabled.
- Python 3.
- `pip` for installing Python packages.
- `ffmpeg` for video processing.

## Installation

1.  **Install `ffmpeg`**:
    ```bash
    sudo apt update
    sudo apt install ffmpeg
    ```

2.  **Install Python Dependencies**:
    Navigate to the project directory.
    ```bash
    # optinally create virtual env (On my Pi, libcamera and python3-picamera2 need to be installed externally, thus the need for system site packages.
    python -m venv --system-site-packages env
    source env/bin/activate
    ```
    Install the required Python packages using `requirements.txt`
    ```bash
    pip install -r requirements.txt
    ```

3.  **Camera Setup**:
    Ensure your camera is physically connected and has been enabled using the `raspi-config` utility.

## Usage

The application is designed to be run as a single process, which handles both the web server and the camera streaming thread.

1.  **Start the Application**:
    From the project's root directory, run the following command:
    ```bash
    python3 app.py
    ```

2.  **Access the Web Interface**:
    Open a web browser on any device on the same network and navigate to:
    ```
    http://<your-raspberry-pi-ip-address>:8080
    ```
    You should see the live video feed and a gallery of any recorded clips.

## Configuration

- Navigate to the **Settings** page by clicking the gear icon in the header.
- **Motion Detection Sensitivity**: Adjust the slider to control how much motion is required to trigger a recording. Higher values mean the camera is more sensitive to motion.
- **Clip Duration**: Set the desired length (in seconds) for each recorded clip.
- **Note**: Settings are applied automatically and do not require a restart.
