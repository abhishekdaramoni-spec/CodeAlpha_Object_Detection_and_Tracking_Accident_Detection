import os

# Bind to the port provided by Render (or default to 5000)
port = os.environ.get("PORT", "5000")
bind = f"0.0.0.0:{port}"

# Limit worker count to 1 and thread count to 1 to fit under Render's 512 MB memory limit
workers = 1
threads = 1

# Increase timeout for model initialization on startup
timeout = 120

# Disable preloading to avoid loading heavy packages twice
preload_app = False
