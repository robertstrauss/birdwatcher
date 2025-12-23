#!/usr/bin/env python
import os
import json
import re
import time
import logging
import threading
import shutil
import tempfile
import math
from flask import Flask, render_template, request, redirect, url_for, jsonify

# Import the main function from the streamer script
from streamer import streamer_main

# --- App Setup ---
app = Flask(__name__)

# --- Shared State for Threads ---
# This dictionary is shared between the Flask app and the streamer thread for status reporting
app_status = {
    "app_server": "green",
    "streamer": "yellow"
}
# A thread-safe way to signal shutdown
app_running_flag = threading.Event()
app_running_flag.set() # Set the flag to True by default

# --- File Paths ---
clips_dir = "clips"
thumbnails_dir = "static/thumbnails"
settings_file = 'settings.json'

# --- Default Settings ---
DEFAULT_UI_SENSITIVITY = 80
DEFAULT_DURATION = 10

# --- Helper Functions ---

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

def human_readable_date(filename):
    """Converts the clip filename to a more friendly format."""
    match = re.match(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})\.mp4', filename)
    if match:
        year, month, day, hour, minute, second = match.groups()
        return f"{hour}:{minute} on {month}/{day}/{year}"
    return filename

# --- Flask Routes ---

@app.route('/')
def index():
    """Renders the main page."""
    clip_data = []
    try:
        filenames = sorted(
            [f for f in os.listdir(clips_dir) if f.endswith('.mp4')],
            reverse=True
        )[:12] # Limit to the 12 most recent clips
        
        for filename in filenames:
            base_name, _ = os.path.splitext(filename)
            thumbnail_rel_path = os.path.join(thumbnails_dir, f"{base_name}.jpg")
            thumbnail_url = thumbnail_rel_path.replace('static/', '') if os.path.exists(thumbnail_rel_path) else None
            clip_data.append({
                'filename': filename,
                'human_label': human_readable_date(filename),
                'thumbnail': thumbnail_url
            })
    except FileNotFoundError:
        pass
    return render_template('index.html', clips=clip_data)

@app.route('/settings', methods=['GET', 'POST'])
def settings_route():
    """Handles the settings page."""
    if request.method == 'POST':
        try:
            new_sensitivity = int(request.form['sensitivity'])
            new_duration = int(request.form['duration'])
            with open(settings_file, 'w') as f:
                json.dump({'sensitivity': new_sensitivity, 'duration': new_duration}, f)
            return redirect(url_for('index'))
        except (ValueError, KeyError):
            return "Invalid input", 400
    
    ui_sensitivity, clip_duration = load_settings()
    return render_template('settings.html', sensitivity=ui_sensitivity, duration=clip_duration, subtitle="Settings")

@app.route('/play/<filename>')
def play_clip(filename):
    """Renders the video player page."""
    human_label = human_readable_date(filename)
    return render_template('player.html', filename=filename, human_label=human_label, subtitle=human_label)

@app.route('/gallery')
def gallery():
    """Renders the paginated gallery of all clips."""
    page = request.args.get('page', 1, type=int)
    per_page = 48

    clip_data = []
    try:
        all_filenames = sorted(
            [f for f in os.listdir(clips_dir) if f.endswith('.mp4')],
            reverse=True
        )
        
        total_clips = len(all_filenames)
        total_pages = math.ceil(total_clips / per_page)
        
        start = (page - 1) * per_page
        end = start + per_page
        paginated_filenames = all_filenames[start:end]

        for filename in paginated_filenames:
            base_name, _ = os.path.splitext(filename)
            thumbnail_rel_path = os.path.join(thumbnails_dir, f"{base_name}.jpg")
            thumbnail_url = thumbnail_rel_path.replace('static/', '') if os.path.exists(thumbnail_rel_path) else None
            clip_data.append({
                'filename': filename,
                'human_label': human_readable_date(filename),
                'thumbnail': thumbnail_url
            })
    except FileNotFoundError:
        total_pages = 0
        pass

    return render_template('gallery.html', clips=clip_data, page=page, total_pages=total_pages, subtitle="All Clips")


@app.route('/status')
def status():
    """Returns the status of application components from shared memory."""
    return jsonify(app_status)

@app.route('/delete/<string:filename>', methods=['POST'])
def delete_file(filename):
    """Moves a clip and its thumbnail to the system's temp directory."""
    if '..' in filename or filename.startswith('/'):
        # Basic security check
        return jsonify({'success': False, 'error': 'Invalid filename.'}), 400

    try:
        # Get system temp dir
        temp_dir = tempfile.gettempdir()

        # Paths for source files
        clip_path = os.path.join(clips_dir, filename)
        base_name, _ = os.path.splitext(filename)
        thumbnail_path = os.path.join(thumbnails_dir, f"{base_name}.jpg")

        # Move clip if it exists
        if os.path.exists(clip_path):
            shutil.move(clip_path, temp_dir)
            logging.info(f"Moved {clip_path} to {temp_dir}")
        else:
            logging.warning(f"Delete requested, but {clip_path} not found.")

        # Move thumbnail if it exists
        if os.path.exists(thumbnail_path):
            shutil.move(thumbnail_path, temp_dir)
            logging.info(f"Moved {thumbnail_path} to {temp_dir}")

        return jsonify({'success': True, 'filename': filename})

    except Exception as e:
        logging.error(f"Error deleting {filename}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Ensure directories exist at startup
    os.makedirs(clips_dir, exist_ok=True)
    os.makedirs(thumbnails_dir, exist_ok=True)
    os.makedirs('static/hls', exist_ok=True)

    # Start the streamer background thread
    logging.info("Starting camera streamer thread...")
    streamer_thread = threading.Thread(
        target=streamer_main,
        args=(app_status, app_running_flag.is_set) # Pass status dict and running flag
    )
    streamer_thread.daemon = True
    streamer_thread.start()
    
    # Run the Flask app
    logging.info("Starting Flask web server...")
    try:
        app.run(host='0.0.0.0', port=8080, threaded=True)
    finally:
        logging.info("Shutdown requested. Signaling streamer thread to stop.")
        app_running_flag.clear() # Set the flag to False
        if streamer_thread.is_alive():
            streamer_thread.join(timeout=5.0) # Wait for thread to finish
        logging.info("Application shut down.")