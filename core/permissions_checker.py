#!/usr/bin/env python3
"""
macOS Accessibility & Permissions Checker
Author: Antigravity and junoonx
Description: Validates strict macOS Accessibility / UI Scripting permissions.
"""

import subprocess
import ctypes
import os

def is_accessibility_trusted():
    """
    Checks whether UI element scripting is active for this process.
    Tests live execution against System Events.
    """
    # 1. Native API check
    try:
        app_services = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        if not bool(app_services.AXIsProcessTrusted()):
            return False
    except Exception:
        pass

    # 2. Live UI Scripting test via System Events
    try:
        script = 'tell application "System Events" to tell process "Finder" to return count of windows'
        res = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
        if res.returncode == 0:
            return True
        return False
    except Exception:
        return False

def open_accessibility_preferences():
    """Opens macOS Privacy & Security -> Accessibility settings panel."""
    try:
        subprocess.run(['open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'], capture_output=True)
    except Exception:
        pass

def request_accessibility_permission():
    """Triggers macOS system prompt and opens Accessibility preferences."""
    try:
        script = 'tell application "System Events" to tell process "Finder" to return count of windows'
        subprocess.run(['osascript', '-e', script], capture_output=True)
    except Exception:
        pass
    open_accessibility_preferences()

def verify_permissions(prompt_user=False):
    """
    Verifies accessibility permissions.
    Returns (is_granted: bool, message: str)
    """
    trusted = is_accessibility_trusted()
    if trusted:
        return True, "macOS Accessibility permissions verified."
    
    msg = (
        "macOS Accessibility permission is not active for this Terminal session.\n"
        "Without this permission, the automated modal watchdog cannot auto-click 'OK' on alert popups.\n"
        "To enable: Open System Settings -> Privacy & Security -> Accessibility and toggle ON for Terminal."
    )
    if prompt_user:
        request_accessibility_permission()
    return False, msg

if __name__ == '__main__':
    granted, message = verify_permissions()
    print("Accessibility Granted:", granted)
    print("Message:", message)
