"""Explicit domain errors for safe automation."""


class DrError(Exception):
    """Base error surfaced by drctl."""


class InvalidTransition(DrError):
    """The requested transition violates recovery policy."""


class ValidationFailed(DrError):
    """Recovered state did not pass every required check."""


class ApprovalRequired(DrError):
    """A material transition lacks independent approval evidence."""


class EvidenceIntegrityError(DrError):
    """Evidence inputs or integrity verification are invalid."""
