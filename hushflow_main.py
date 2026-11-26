"""
hushflow_main.py - Unified Entry Point for Standalone HushFlow.exe
Routes execution cleanly without loading heavy GUI modules when launched headless:
- Default (no flags): Launches HushFlow Studio GUI
- --engine: Runs background voice dictation service (Tray + HUD + whisper-server)
- --quickfix: Opens Quick Correction popup dialog
- --stop: Stops active background Engine & whisper-server cleanly
"""

import sys


def main():
    args = set(a.lower() for a in sys.argv[1:])
    if "--engine" in args:
        import whisper_flow
        whisper_flow.main()
    elif "--quickfix" in args:
        import quick_fix
        quick_fix.main()
    elif "--stop" in args:
        import whisper_flow
        whisper_flow.stop_running_engine()
    else:
        import whisper_flow_studio
        whisper_flow_studio.main()


if __name__ == "__main__":
    main()
