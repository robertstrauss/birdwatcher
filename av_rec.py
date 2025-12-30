#!/usr/bin/env python
#!/usr/bin/env python
import multiprocessing
import pyaudio
import wave
import time
import picamera2
from picamera2.encoders import H264Encoder, Quality
import subprocess
import os

# --- Config ---
AUDIO_FILENAME = 'audio.wav'
VIDEO_FILENAME = 'video.h264'
OUTPUT_FILENAME = 'output.mp4'
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
# We can keep a tighter chunk size now because we have a dedicated core
CHUNK = 4096 


def audio_worker(ready_event, start_event, stop_event, filename):
    """
    This function runs in a completely separate process.
    It has its own memory, its own GIL, and can run on a separate CPU core.
    """
    # Initialize PyAudio INSIDE the process (crucial)
    p = pyaudio.PyAudio()
    
    stream = p.open(format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK)

    frames = []

    ready_event.set()
    start_event.wait()
    stream.start_stream()
    
    try:
        while not stop_event.is_set():
            # exception_on_overflow=False is still safe insurance, 
            # but much less likely to trigger now.
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)
    except Exception as e:
        print(f"Audio Process Error: {e}")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

        # Write file
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
        print("Audio Saved")

def main():
    # Setup Video
    camera = picamera2.Picamera2()
    encoder = H264Encoder()
    camera.framerate = 30
    
    # Setup Multiprocessing Sync
    ready_event = multiprocessing.Event()
    start_event = multiprocessing.Event()
    stop_event = multiprocessing.Event()
    audio_process = multiprocessing.Process(target=audio_worker, args=(ready_event, start_event, stop_event, AUDIO_FILENAME))
    # Start audio first (it takes a split second to spin up)
    audio_process.start()
    print("Waiting for audio ready signal")
    ready_event.wait() # wait on audio

    try:
        print("Initiating recording")
        start_event.set()
        camera.start_recording(encoder, VIDEO_FILENAME, quality=Quality.VERY_HIGH)

        # --- RECORDING DURATION ---
        time.sleep(10) 
        # --------------------------

    except KeyboardInterrupt:
        print("Stopping...")

    finally:
        # Stop Recording
        camera.stop_recording()
        # Signal audio to stop and wait for it to finish writing to disk
        stop_event.set()
        audio_process.join()
        
        # Merge
        print("Merging...")
        combine_video_audio(VIDEO_FILENAME, AUDIO_FILENAME, OUTPUT_FILENAME)

def combine_video_audio(video_in, audio_in, video_out):
    command = [
        'ffmpeg',
        '-y',
        '-framerate', '30',
        '-i', video_in,
        '-i', audio_in,
        '-c:v', 'copy',
        '-c:a', 'aac',
        video_out
    ]
    print(' '.join(command))
    subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    print(f"Done! Saved to {video_out}")
    
    # Clean up
    if os.path.exists(video_in): os.remove(video_in)
    if os.path.exists(audio_in): os.remove(audio_in)

if __name__ == '__main__':
    main()
