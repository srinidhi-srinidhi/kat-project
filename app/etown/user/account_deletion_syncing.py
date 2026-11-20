"""Account Deletion Dataverse Sync - withdrawn, docstring-only module superseded by account_lifecycle_syncing.py.

Holds no executable code and nothing imports it; account deletion is a soft delete, and account_lifecycle_syncing.py stamps the ets_ lifecycle flags on the contact's Dataverse records instead of deleting any of them.

Updates:
    06-Jul-2026: Initial creation for account deletion endpoint migration
    06-Aug-2026: Entity count 20 -> 22 (bookings both ways)
    13-Aug-2026: Withdrawn - deletion is soft and no longer writes to Dataverse.
    19-Aug-2026: Superseded by account_lifecycle_syncing.py.
"""
