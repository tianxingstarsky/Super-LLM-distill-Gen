"""Compose command-event monitoring with the workspace's event file."""
from lib.application.monitor_service import MonitorApplication
from lib.infrastructure.event_history import FilesystemEventHistory


def monitor_application(path):
    return MonitorApplication(FilesystemEventHistory(path))
