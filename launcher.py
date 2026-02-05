#!/usr/bin/env python3
"""
Firm Website Finder - Simple Launcher
"""
import sys
import os
import subprocess
import webbrowser

def main():
    """Launch Streamlit app directly."""
    
    # Add current directory to Python path
    if getattr(sys, 'frozen', False):
        # Running as compiled exe
        application_path = os.path.dirname(sys.executable)
    else:
        # Running as script
        application_path = os.path.dirname(os.path.abspath(__file__))
    
    os.chdir(application_path)
    
    print("Starting Firm Website Finder...")
    print("This will open in your default web browser.")
    print("The application will run in this console window.")
    print("Close this window or press Ctrl+C to stop the application.")
    print("Loading http://localhost:8501 in your browser...")
    print("-" * 60)
    
    try:
        # Open browser immediately
        webbrowser.open("http://localhost:8501")
        
        # Launch Streamlit directly
        print("Launching Streamlit on http://localhost:8501...")
        subprocess.run([
            sys.executable, "-m", "streamlit", "run", 
            "name_to_URL_fixed.py",
            "--server.port", "8501",
            "--server.headless", "false"
        ], cwd=application_path)
        
    except KeyboardInterrupt:
        print("\nStopping application...")
    except Exception as e:
        print(f"Error: {e}")
        input("Press Enter to exit...")

if __name__ == "__main__":
    main()