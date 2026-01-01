#!/usr/bin/env python3
import subprocess
import time
import os
import glob
from datetime import datetime

# --- Configuration ---
VIDEO_SEG_DIR = "video_segments"
AUDIO_SEG_DIR = "audio_segments"
OUTPUT_DIR = "muxed_segments"
VIDEO_SEG_PREFIX = "video_"
AUDIO_SEG_PREFIX = "audio_"
VIDEO_SEG_FORMAT = "mp4"
AUDIO_SEG_FORMAT = "aac"
SEG_DURATION = 10  # seconds
KEEP_LAST_N = 3    # keep only last N muxed segments
VIDEO_DELAY = 1.0 # seconds to delay video to synchronize

os.makedirs(VIDEO_SEG_DIR, exist_ok=True)
os.makedirs(AUDIO_SEG_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Launch video capture ---
video_proc = subprocess.Popen([
    "rpicam-vid",
    "-n",               # no preview
    "-t", "0",          # run indefinitely
    "-o", "-"
    #"-o", f"{VIDEO_SEG_DIR}/{VIDEO_SEG_PREFIX}%03d.h264",
    #"--segment", str(SEG_DURATION * 1000)  # milliseconds
    ], stdout = subprocess.PIPE
)
convert_proc = subprocess.Popen([
    "ffmpeg",
    "-y",                     # overwrite output
    "-f", "h264",             # input format is raw h264
    "-fflags", "+genpts",     # generate timestamps
    "-i", "-",                # read from stdin
    "-c:v", "copy",           # copy video stream without re-encoding
    "-f", "segment",          # optional: segmenting
    "-segment_time", str(SEG_DURATION),    # segment length in seconds
    f"{VIDEO_SEG_DIR}/{VIDEO_SEG_PREFIX}%03d.{VIDEO_SEG_FORMAT}"
    ], stdin = video_proc.stdout
)


# --- Launch audio capture ---
audio_proc = subprocess.Popen([
    "ffmpeg",
    "-y",
    "-f", "alsa",
    "-i", "plughw:2,0",  # change to your audio device
    "-f", "segment",
    "-segment_time", str(SEG_DURATION),
    "-c:a", "aac",
    "-b:a", "128k",
    f"{AUDIO_SEG_DIR}/{AUDIO_SEG_PREFIX}%03d.{AUDIO_SEG_FORMAT}"
])

# --- Keep track of muxed files ---
#muxed_index = 0

try:
    while True:
        # Get sorted lists of segments
        video_files = sorted(glob.glob(f"{VIDEO_SEG_DIR}/{VIDEO_SEG_PREFIX}*.{VIDEO_SEG_FORMAT}"))
        audio_files = sorted(glob.glob(f"{AUDIO_SEG_DIR}/{AUDIO_SEG_PREFIX}*.{AUDIO_SEG_FORMAT}"))

        # Only mux pairs that exist and haven't been muxed yet
        print('vids:', video_files)
        print('auds:', audio_files)
        #print('mux ind:', muxed_index)
        if len(video_files) >= 1 and len(audio_files) > 1: # video files created once ready, audio files created at beginning of recording
        #while muxed_index < min(len(video_files), len(audio_files)):
            vfile = video_files[0]
            afile = audio_files[0]
            outfile = f"{OUTPUT_DIR}/segment_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.mp4"

            print('muxing ', vfile, 'and', afile, 'into', outfile)
            # Run ffmpeg to mux video + audio
            subprocess.run([
                "ffmpeg",
                "-y",             # overwrite if exists
                "-itsoffset", str(VIDEO_DELAY),
                "-i", vfile,
                "-i", afile,
                "-c:v", "copy",
                "-c:a", "aac",
                outfile
            ])

            print(f"Muxed: {outfile}")

            # Optionally delete old raw segments
            #if muxed_index >= KEEP_LAST_N:
            os.remove(video_files[0])#muxed_index - KEEP_LAST_N])
            os.remove(audio_files[0])#muxed_index - KEEP_LAST_N])

            #muxed_index += 1
        else:
            time.sleep(SEG_DURATION)

        # Wait a bit before checking again
        time.sleep(1)

except KeyboardInterrupt:
    pass
finally:
    print("Stopping capture...")
    video_proc.terminate()
    audio_proc.terminate()
    video_proc.wait()
    audio_proc.wait()

