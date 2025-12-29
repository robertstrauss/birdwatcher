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
import pyaudio

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
audio_device_index = None

# --- Audio Constants ---
AUDIO_RATE = 44100
AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHANNELS = 1

def find_audio_device(p, target_name="USB"):
    """Finds the index of an audio device containing target_name."""
    info = p.get_host_api_info_by_index(0)
    num_devices = info.get('deviceCount')
    for i in range(num_devices):
        device_info = p.get_device_info_by_host_api_device_index(0, i)
        if target_name in device_info.get('name', ''):
            logging.info(f"Found audio device '{device_info['name']}' at index {i}")
            return i
    logging.warning(f"Audio device containing '{target_name}' not found.")
    return None

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
    """Records a video clip with audio directly to a file."""
    global is_recording
    if is_recording: return
    
    is_recording = True
    logging.info("Starting clip recording...")
    
    _, clip_duration = load_settings()
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    output_filename = os.path.join(clips_dir, f"{timestamp}.mp4")

    # --- FFmpeg Command for Recording ---
    # This command will take raw H264 video and raw audio from stdin,
    # and mux them into an MP4 file.
    record_ffmpeg_cmd = [
        'ffmpeg', '-y',
        '-f', 'h264', '-i', '-',                  # Video from stdin
        '-f', 's16le', '-ar', str(AUDIO_RATE), '-ac', str(AUDIO_CHANNELS), '-i', '-', # Audio from stdin
        '-c:v', 'copy',                           # Copy video stream
        '-c:a', 'aac', '-b:a', '128k',             # Encode audio to AAC
        output_filename
    ]

    # We need a new encoder for recording because the main one is for HLS
    clip_encoder = H264Encoder(bitrate=5000000)
    
    # Start a new PyAudio stream for the recording
    p = pyaudio.PyAudio()
    audio_stream = p.open(
        format=AUDIO_FORMAT,
        channels=AUDIO_CHANNELS,
        rate=AUDIO_RATE,
        input=True,
        input_device_index=audio_device_index,
        frames_per_buffer=1024
    )

    # Start the ffmpeg process
    ffmpeg_proc = subprocess.Popen(record_ffmpeg_cmd, stdin=subprocess.PIPE)

    # Create a thread to pipe audio to ffmpeg
    audio_pipe_thread = threading.Thread(
        target=pipe_audio_to_ffmpeg,
        args=(audio_stream, ffmpeg_proc)
    )
    audio_pipe_thread.daemon = True
    audio_pipe_thread.start()

    # Start recording video to the ffmpeg process
    picam2.start_encoder(clip_encoder, ffmpeg_proc.stdin)
    
    time.sleep(clip_duration) # Record for the specified duration

    # Stop everything
    picam2.stop_encoder(clip_encoder)
    
    # The audio thread will stop automatically when the pipe is closed
    if audio_stream.is_active():
        audio_stream.stop_stream()
        audio_stream.close()
    p.terminate()
    
    ffmpeg_proc.stdin.close()
    ffmpeg_proc.wait()

    logging.info(f"Clip saved: {output_filename}")

    # --- Thumbnail Generation ---
    thumbnail_filename = os.path.join(thumbnails_dir, f"{timestamp}.jpg")
    ffmpeg_thumb_cmd = ['ffmpeg', '-i', output_filename, '-ss', '00:00:01', '-vframes', '1', thumbnail_filename]
    
    try:
        subprocess.run(ffmpeg_thumb_cmd, check=True, capture_output=True, text=True)
        logging.info(f"Thumbnail saved: {thumbnail_filename}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Thumbnail generation failed! stderr: {e.stderr}")
        
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

def pipe_audio_to_ffmpeg(audio_stream, ffmpeg_process):
    """Reads from audio stream and writes to ffmpeg's stdin."""
    while app_running:
        try:
            data = audio_stream.read(1024)
            ffmpeg_process.stdin.write(data)
        except IOError as e:
            # This can happen if the ffmpeg process closes stdin
            logging.warning(f"IOError in audio pipe: {e}")
            break
        except Exception as e:
            logging.error(f"Error in audio pipe: {e}")
            break
    logging.info("Audio pipe thread finished.")

def streamer_main(status_dict, running_flag):
    """
    The main entry point for the streamer logic.
    Accepts a status dictionary to update and a running flag to check for shutdown.
    """
    # --- Streamer Constants ---
    HLS_SEGMENT_TIME = 2
    HLS_LIST_SIZE = 5
    FPS = 15
    AUDIO_RATE = 44100
    AUDIO_FORMAT = pyaudio.paInt16
    AUDIO_CHANNELS = 1

    global picam2, video_config, app_running, audio_device_index
    app_running = running_flag # Use the shared running flag
    
    status_dict['streamer'] = 'yellow'
    
    p = pyaudio.PyAudio()
    audio_device_index = find_audio_device(p)
    
    try:
        picam2 = Picamera2()
        video_config = picam2.create_video_configuration(
            main={"size": (1280, 720), "format": "RGB888"},
            lores={"size": (640, 480), "format": "YUV420"},
            controls={"FrameRate": FPS}
        )
        picam2.configure(video_config)

        # This command tells ffmpeg to expect a raw h264 stream from the camera
        # and a raw audio stream from stdin, then mux them into an HLS stream.
        ffmpeg_command = [
            '-f', 'h264', '-i', '-',                  # Video input from stdin
            '-f', 's16le', '-ar', str(AUDIO_RATE), '-ac', str(AUDIO_CHANNELS), '-i', '-', # Audio input from stdin
            '-c:v', 'copy',                           # Copy video without re-encoding
            '-c:a', 'aac', '-b:a', '128k',             # Encode audio to AAC
            '-f', 'hls',
            '-hls_time', str(HLS_SEGMENT_TIME),
            '-hls_list_size', str(HLS_LIST_SIZE),
            '-hls_flags', 'delete_segments',
            '-hls_allow_cache', '0',
            os.path.join(hls_dir, 'stream.m3u8')
        ]
        
        hls_output = FfmpegOutput(' '.join(ffmpeg_command))
        encoder = H264Encoder(bitrate=5000000, repeat=True, iperiod=FPS)
        
        # Start audio stream
        audio_stream = None
        if audio_device_index is not None:
            audio_stream = p.open(
                format=AUDIO_FORMAT,
                channels=AUDIO_CHANNELS,
                rate=AUDIO_RATE,
                input=True,
                input_device_index=audio_device_index,
                frames_per_buffer=1024
            )
            logging.info("Audio stream opened.")

        picam2.start_recording(encoder, hls_output)
        
        # Start audio piping thread if audio is available
        if audio_stream:
            audio_thread = threading.Thread(
                target=pipe_audio_to_ffmpeg,
                args=(audio_stream, hls_output.proc)
            )
            audio_thread.daemon = True
            audio_thread.start()

        logging.info("Camera started and HLS streaming is active.")
        status_dict['streamer'] = 'green'

        motion_detection_loop()

    except Exception as e:
        logging.error(f"Streamer crashed: {e}")
        status_dict['streamer'] = 'red'
    finally:
        if 'audio_stream' in locals() and audio_stream and audio_stream.is_active():
            audio_stream.stop_stream()
            audio_stream.close()
        p.terminate()
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
