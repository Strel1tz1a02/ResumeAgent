"""Registry for stateless business AI Adapters."""

from app.ai_chat.adapters.base import BaseAdapter
from app.ai_chat.errors import AdapterNotRegisteredError, AdapterRegistrationError


class AdapterRegistry:
    """Register and resolve one Adapter instance per stable name."""

    def __init__(self) -> None:
        """Create an initially empty production registry."""
        self._adapters: dict[str, BaseAdapter] = {}

    def register(self, adapter: BaseAdapter) -> None:
        """Register an Adapter and reject blank or duplicate names."""
        name = adapter.adapter_name().strip()
        if not name:
            raise AdapterRegistrationError("Adapter name cannot be blank")
        if name in self._adapters:
            raise AdapterRegistrationError(f"Adapter already registered: {name}")
        self._adapters[name] = adapter

    def get(self, name: str) -> BaseAdapter:
        """Return a registered Adapter by its persisted name."""
        try:
            return self._adapters[name]
        except KeyError as error:
            raise AdapterNotRegisteredError(f"Adapter is not registered: {name}") from error

    def clear(self) -> None:
        """Remove all registrations during application shutdown or reset."""
        self._adapters.clear()

    def names(self) -> tuple[str, ...]:
        """Return registered names for diagnostics without exposing the mapping."""
        return tuple(self._adapters)
