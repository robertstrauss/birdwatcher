#!/bin/bash
# This script is called by cron to update the application

echo "--- Running daily update check on $(date) ---"

# Navigate to the project directory
cd "/home/rusty/Desktop/birdwatcher" || { echo "Failed to cd into /home/rusty/Desktop/birdwatcher"; exit 1; }

# Fetch the latest changes from the remote repository
git fetch

# Check if the local branch is behind the remote
# The "git status -uno" command is a reliable way to check this
if git status -uno | grep -q "Your branch is behind"; then
    echo "Changes detected. Pulling and restarting application..."
    git pull
    
    # Find and kill the running application process
    # The pattern is specific to avoid killing other python processes
    pkill -f "/home/rusty/Desktop/birdwatcher/venv/bin/python3 /home/rusty/Desktop/birdwatcher/app.py"
    
    # Wait a moment for the process to die gracefully
    sleep 5
    
    # Restart the application in the background
    "/home/rusty/Desktop/birdwatcher/venv/bin/python3" "/home/rusty/Desktop/birdwatcher/app.py" &
    
    echo "Application restarted successfully."
else
    echo "No changes detected. Application is up to date."
fi
