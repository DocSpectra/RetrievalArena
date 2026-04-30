class RetrievalArenaError(Exception):
    """Base class for expected RetrievalArena failures."""


class ValidationError(RetrievalArenaError):
    """Raised when a config, dataset, or test output violates the contract."""