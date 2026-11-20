"""Message Package - direct one-to-one messaging between Etown users.

Package marker for the messaging domain behind /api/message; the conversation, message, receipt and inbox logic lives in message_utils.

Features:
    - Conversation lifecycle: Create and fetch direct conversations
    - Message lifecycle: Send, edit, list and per-side delete conversation messages
    - Receipt updates: Mark receiver-side messages as delivered or read
    - Inbox: List a user's conversations with unread counts

Dependencies:
    No imports; the implementation lives in app.etown.message.message_utils

Updates:
    07-Apr-2026: Initial creation of message module package
"""
