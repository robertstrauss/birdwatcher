#!/bin/bash

# This script sets up cron jobs to run the Bird-Watcher application on boot
# and to check for updates daily.

echo "--- Bird-Watcher Cron Setup ---"

# --- Configuration ---
# Get the absolute path to the directory where this script is located
# This makes the script runnable from anywhere
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
PROJECT_DIR="$SCRIPT_DIR"
PYTHON_PATH=$(which python3)
LOG_FILE="$PROJECT_DIR/cron.log"
UPDATER_SCRIPT_PATH="$PROJECT_DIR/update_and_restart.sh"

# Check if python3 is found
if [ -z "$PYTHON_PATH" ]; then
    echo "Error: python3 executable not found in PATH. Please install Python 3."
    exit 1
fi

echo "Project directory: $PROJECT_DIR"
echo "Python executable: $PYTHON_PATH"

# --- Create the Updater Script ---
echo "Creating the update_and_restart.sh script..."
cat > "$UPDATER_SCRIPT_PATH" << EOL
#!/bin/bash
# This script is called by cron to update the application

echo "--- Running daily update check on \$(date) ---"

# Navigate to the project directory
cd "$PROJECT_DIR" || { echo "Failed to cd into $PROJECT_DIR"; exit 1; }

# Fetch the latest changes from the remote repository
git fetch

# Check if the local branch is behind the remote
# The "git status -uno" command is a reliable way to check this
if git status -uno | grep -q "Your branch is behind"; then
    echo "Changes detected. Pulling and restarting application..."
    git pull
    
    # Find and kill the running application process
    # The pattern is specific to avoid killing other python processes
    pkill -f "$PYTHON_PATH $PROJECT_DIR/app.py"
    
    # Wait a moment for the process to die gracefully
    sleep 5
    
    # Restart the application in the background
    "$PYTHON_PATH" "$PROJECT_DIR/app.py" &
    
    echo "Application restarted successfully."
else
    echo "No changes detected. Application is up to date."
fi
EOL

# Make the updater script executable
chmod +x "$UPDATER_SCRIPT_PATH"
echo "Made updater script executable."

# --- Setup Cron Jobs ---
echo "Setting up cron jobs..."

# Cron job lines to be added
# Using "flock" to prevent the update job from running multiple times if it's slow
REBOOT_JOB="@reboot cd $PROJECT_DIR && $PYTHON_PATH app.py >> $LOG_FILE 2>&1"
UPDATE_JOB="0 0 * * * /usr/bin/flock -n /tmp/birdwatcher_update.lock $UPDATER_SCRIPT_PATH >> $LOG_FILE 2>&1"

# Use a temporary file to safely edit the crontab
# Get current crontab, filter out old jobs for this project, then add the new ones
(crontab -l 2>/dev/null | grep -v -F "$PROJECT_DIR") | cat - <(echo "$REBOOT_JOB") <(echo "$UPDATE_JOB") | crontab -

echo "Cron jobs installed successfully."
echo ""
echo "--- Setup Complete ---"
echo "The application will now start on boot."
echo "It will check for updates every day at midnight."
echo "Log output will be saved to: $LOG_FILE"
