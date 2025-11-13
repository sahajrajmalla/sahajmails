# sahajmails/cli.py
import os
import sys
import subprocess

def run():
    """Launch SahajMails Streamlit app correctly when installed."""
    # Get the absolute path to app.py inside the package
    app_path = os.path.join(os.path.dirname(__file__), "app.py")

    # Forward all arguments and run via streamlit CLI
    cmd = [sys.executable, "-m", "streamlit", "run", app_path] + sys.argv[1:]
    os.execvp(sys.executable, cmd)  # This replaces the process (clean exit)

def main():
    """Allow both: sahajmails  and  sahajmails run"""
    if len(sys.argv) > 1 and sys.argv[1] in ("run", "--help", "-h"):
        run()
    else:
        run()  # Just run by default

if __name__ == "__main__":
    main()