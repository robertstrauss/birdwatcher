#!/usr/bin/env python
import os
import time
import json
import logging
import threading
import subprocess
from picamera2 import Picamera2
from picamera2.outputs import FfmpegOutput
from picamera2.encoders import H264Encoder
from libcamera import controls
import numpy as np

# --- File/Directory Paths ---
clips_dir = "clips"
hls_dir = "static/hls"
thumbnails_dir = "static/thumbnails"
settings_file = 'settings.json'

# --- Default Settings ---
DEFAULT_UI_SENSITIVITY = 80
DEFAULT_DURATION = 10

# --- Module-level State ---
# These are managed by the main streamer_main function
picam2 = None
video_config = None
is_recording = False
motion_detected_time = None
app_running = True

def load_settings():
    """Loads settings from file, returns UI sensitivity and duration."""
    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)
            ui_sensitivity = settings.get('sensitivity', DEFAULT_UI_SENSITIVITY)
            duration = settings.get('duration', DEFAULT_DURATION)
            return ui_sensitivity, duration
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_UI_SENSITIVITY, DEFAULT_DURATION

def get_raw_sensitivity(ui_sensitivity):
    """Converts UI sensitivity (1-100, higher is more sensitive) to raw MSE threshold."""
    return 105 - int(ui_sensitivity)

def get_segments_from_playlist():
    """
    Reads the HLS playlist file to get the list of current, valid segments.
    This is the source of truth and avoids including stale segments.
    """
    playlist_path = os.path.join(hls_dir, 'stream.m3u8')
    if not os.path.exists(playlist_path):
        return []
    
    segments = []
    with open(playlist_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and line.endswith('.ts'):
                segments.append(os.path.join(hls_dir, line))
    return segments

def record_clip():
    """Copies HLS segments to create a motion clip."""
    global is_recording
    if is_recording: return
    
    is_recording = True
    logging.info("Starting clip recording...")
    
    _, clip_duration = load_settings()
    # Wait for the desired clip length to be buffered.
    # This is a simple approach; a more advanced one could monitor segment creation.
    time.sleep(clip_duration)

    segments_to_record = get_segments_from_playlist()
    if not segments_to_record:
        logging.warning("No HLS segments found in playlist to record.")
        is_recording = False
        return

    concat_file_path = os.path.join(hls_dir, 'concat.txt')
    with open(concat_file_path, 'w') as f:
        for segment in segments_to_record:
            f.write(f"file '{os.path.basename(segment)}'\n")

    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    output_filename = os.path.join(clips_dir, f"{timestamp}.mp4")
    
    ffmpeg_cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_file_path, '-c', 'copy', output_filename]
    
    try:
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"ffmpeg clip creation failed! stderr: {e.stderr}")
        os.remove(concat_file_path)
        is_recording = False
        return
        
    os.remove(concat_file_path)

    thumbnail_filename = os.path.join(thumbnails_dir, f"{timestamp}.jpg")
    ffmpeg_thumb_cmd = ['ffmpeg', '-i', output_filename, '-ss', '00:00:01', '-vframes', '1', thumbnail_filename]
    
    try:
        subprocess.run(ffmpeg_thumb_cmd, check=True, capture_output=True, text=True)
        logging.info(f"Thumbnail saved: {thumbnail_filename}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Thumbnail generation failed! stderr: {e.stderr}")
        
    logging.info(f"Clip saved: {output_filename}")
    is_recording = False

def motion_detection_loop():
    """Continuously checks for motion using the lores stream."""
    global motion_detected_time, prev_frame
    prev_frame = None
    
    while app_running:
        if is_recording:
            time.sleep(1)
            continue

        current_frame_yuv = picam2.capture_array("lores")
        
        w, h = video_config['lores']['size']
        yuv_2d = current_frame_yuv.reshape(h * 3 // 2, w)
        current_frame_y = yuv_2d[:h, :]

        if prev_frame is not None:
            ui_sensitivity, _ = load_settings()
            raw_sensitivity = get_raw_sensitivity(ui_sensitivity)
            
            mse = np.mean(np.abs(current_frame_y.astype(np.float32) - prev_frame.astype(np.float32)))
            
            if mse > raw_sensitivity:
                if motion_detected_time is None:
                    logging.info(f"Motion detected (MSE: {mse:.2f})")
                    motion_detected_time = time.time()
                    threading.Thread(target=record_clip).start()
            else:
                _, clip_duration = load_settings()
                if motion_detected_time and time.time() - motion_detected_time > clip_duration:
                    motion_detected_time = None
        
        prev_frame = current_frame_y
        time.sleep(0.1)

def streamer_main(status_dict, running_flag):
    """
    The main entry point for the streamer logic.
    Accepts a status dictionary to update and a running flag to check for shutdown.
    """
    # --- Streamer Constants ---
    HLS_SEGMENT_TIME = 2
    HLS_LIST_SIZE = 5
    FPS = 15

    global picam2, video_config, app_running
    app_running = running_flag # Use the shared running flag
    
    status_dict['streamer'] = 'yellow'
    
    try:
        picam2 = Picamera2()
        video_config = picam2.create_video_configuration(
            main={"size": (1280, 720), "format": "RGB888"},
            lores={"size": (640, 480), "format": "YUV420"},
            controls={"FrameRate": FPS}
        )
        picam2.configure(video_config)

        # This command tells ffmpeg to expect a raw h264 stream (-f h264)
        # and to copy it without re-encoding (-c:v copy)
        ffmpeg_command = [
            '-f', 'h264',
            '-c:v', 'copy',
            '-f', 'hls',
            '-hls_time', str(HLS_SEGMENT_TIME),
            '-hls_list_size', str(HLS_LIST_SIZE),
            '-hls_flags', 'delete_segments',
            '-hls_allow_cache', '0',
            os.path.join(hls_dir, 'stream.m3u8')
        ]
        
        hls_output = FfmpegOutput(' '.join(ffmpeg_command))
        encoder = H264Encoder(bitrate=5000000, repeat=True, iperiod=FPS)
        picam2.start_recording(encoder, hls_output)
        
        logging.info("Camera started and HLS streaming is active.")
        status_dict['streamer'] = 'green'

        motion_detection_loop()

    except Exception as e:
        logging.error(f"Streamer crashed: {e}")
        status_dict['streamer'] = 'red'
    finally:
        if picam2 and picam2.is_open:
            picam2.stop_recording()
        status_dict['streamer'] = 'red'
        logging.info("Streamer shut down.")

if __name__ == '__main__':
    # This allows the script to be run standalone for debugging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Create a dummy status dict and running flag for standalone mode
    status = {'streamer': 'yellow'}
    
    # A little lambda to simulate the threading.Event.is_set method
    class RunningFlag:
        def __init__(self):
            self._running = True
        def is_set(self):
            return self._running
        def clear(self):
            self._running = False

    running_flag = RunningFlag()

    try:
        streamer_main(status, running_flag.is_set)
    except KeyboardInterrupt:
        running_flag.clear()
        # In a real scenario, the main app would join the thread. Here we just exit.
        time.sleep(1)
