"""Groups Package - domain logic and Dataverse mirror for social groups and their members.

Package for the domain layer behind /api/groups: groups_utils finds a user's groups, reads group and member details, and creates, updates and deletes groups and member lists, while groups_syncing mirrors those writes to Dataverse; it re-exports nothing.
"""