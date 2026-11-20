"""Notification Channels Package - re-exports the ChannelAdapter base and the get_adapter lookup.

Package for the push, WhatsApp, SMS and in-app adapters that wrap the existing delivery helpers behind one ChannelAdapter interface; get_adapter() lazily builds the singleton adapter the dispatcher routes each channel to.
"""

from app.etown.notifications.core.channels.base import ChannelAdapter, get_adapter

__all__ = ["ChannelAdapter", "get_adapter"]
