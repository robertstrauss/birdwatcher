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
import signal
import fcntl

# Set pipe buffer to 1MB (approx 20 seconds of audio)
# This prevents the audio thread from blocking too early
F_SETPIPE_SZ = 1031  # Linux constant

# --- Config ---
AUDIO_PIPE = 'audio.pipe'
VIDEO_PIPE = 'video.pipe'
OUTPUT_PATTERN = 'segment_%03d.mp4'
SEGMENT_TIME = 10 # seconds
FORMAT = pyaudio.paInt16
CHANNELS = 1
SAMPLE_RATE = 44100
FPS = 30
CHUNK = 4096 

def setup_pipes():
    """Creates named pipes (FIFOs) if they don't exist."""
    if not os.path.exists(VIDEO_PIPE):
        os.mkfifo(VIDEO_PIPE)
    if not os.path.exists(AUDIO_PIPE):
        os.mkfifo(AUDIO_PIPE)

def audio_worker(ready_event, start_event, stop_event):
    """
    This function runs in a completely separate process.
    It has its own memory, its own GIL, and can run on a separate CPU core.
    This is critical for the audio and video to record in true simultaneity, rather than interrputing each other
    """
    audio_pipe = open(AUDIO_PIPE, 'wb')
    fcntl.fcntl(audio_pipe.fileno(), F_SETPIPE_SZ, 1024 * 1024)
    print('successfully opened audio pipe')

    # Initialize PyAudio INSIDE thke process
    p = pyaudio.PyAudio()
    print('='*50) # delimit from all the ALSA warning nonsense
    
    stream = p.open(format=FORMAT,
                    channels=CHANNELS,
                    rate=SAMPLE_RATE,
                    input=True,
                    frames_per_buffer=CHUNK)

    ready_event.set()
    start_event.wait()
    stream.start_stream()
    
    try:
        while not stop_event.is_set():
            # exception_on_overflow=False is still safe insurance, 
            # but much less likely to trigger now.
            data = stream.read(CHUNK, exception_on_overflow=False)
            try:
                audio_pipe.write(data)
            except BrokenPipeError:
                print("BROKEN AUDIO PIPE")
                break
    except Exception as e:
        print(f"Audio Process Error: {e}")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()
        audio_pipe.close()

def main():
    # Setup Video
    camera = picamera2.Picamera2()
    encoder = H264Encoder()
    camera.framerate = FPS

    setup_pipes()
    
    # Setup Multiprocessing Sync
    ready_event = multiprocessing.Event()
    start_event = multiprocessing.Event()
    stop_event = multiprocessing.Event()
    #audio_process = multiprocessing.Process(target=audio_worker, args=(ready_event, start_event, stop_event))
    # Start audio first (it takes a split second to spin up)
    print("Starting audio process")
    #dummy = os.open(AUDIO_PIPE, os.O_RDWR)

    ffmpeg_process = start_ffmpeg_proc()
    #mic_proc = subprocess.Popen(f'ffmpeg -f alsa -i plughw:2,0 -c:a aac -f adts {str(AUDIO_PIPE)}'.split(' '))

    #audio_process.start()
    #ready_event.wait() # wait on audio


    print("Initiating recording")
    start_event.set()
    camera.start_recording(encoder, VIDEO_PIPE, quality=Quality.VERY_HIGH)

    try:
        while True:
            # Just keep the main thread alive.
            # The work is happening in the camera callback and the audio process.
            time.sleep(1)

            # Check if FFmpeg crashed
            if ffmpeg_process.poll() is not None:
                print("FFmpeg died unexpectedly!")
                break

    except KeyboardInterrupt:
        print("\nStopping...")

    finally:
        camera.stop_recording()

        stop_event.set()
        audio_process.join()

        # Gracefully kill FFmpeg
        ffmpeg_process.send_signal(signal.SIGINT)
        ffmpeg_process.wait()

        # Cleanup pipes
        if os.path.exists(VIDEO_PIPE): os.remove(VIDEO_PIPE)
        if os.path.exists(AUDIO_PIPE): os.remove(AUDIO_PIPE)
        print("Done.")

def start_ffmpeg_proc():
    command = [
        'ffmpeg',
        '-y',
        '-framerate', '30',
        '-use_wallclock_as_timestamps', '1',
        #'-i', VIDEO_PIPE,
        '-f', 'v4l2', '-input_format', 'yuyv422', '-video_size', '1280x720', '-framerate', '30',
        '-i', '/dev/video0',
        '-use_wallclock_as_timestamps', '1',
        #'-f', 's16le', '-ar', str(SAMPLE_RATE), '-ac', '1',
        #'-i', AUDIO_PIPE,
        '-f', 'alsa',
        '-i', 'plughw:2,0',
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-f', 'segment',
        '-segment_time', str(SEGMENT_TIME),
        '-reset_timestamps', '1',
        '-strftime', '0',
        OUTPUT_PATTERN
    ]
    print(' '.join(command))
    return subprocess.Popen(command)#, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

if __name__ == '__main__':
    main()
