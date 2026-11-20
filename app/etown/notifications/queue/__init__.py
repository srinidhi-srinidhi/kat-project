"""Notification Queue Package - priority-queue layer for deferred notification delivery.

Package for the queue that service.enqueue() fills: notifications land on critical, transactional, social or marketing lanes in notifications.etown_notification_queue, and runner.py workers claim them with FOR UPDATE SKIP LOCKED and pass each to service._send. The layer is inert unless NOTIFICATIONS_QUEUE_ENABLED is true.

Classes:
    No classes defined (package docstring only; see backend.py and runner.py for the implementation)

Functions:
    No functions defined (package docstring only; see backend.py and runner.py for the implementation)
"""
