import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Dict, Type

from ..core.base import BaseSensor
from ..core.logging import log

# Registry of sensor types -> Sensor classes
_SENSOR_REGISTRY: Dict[str, Type[BaseSensor]] = {}


def register_sensor(sensor_class: Type[BaseSensor]) -> None:
    """Decorator to manually register a sensor class (optional)."""
    if not hasattr(sensor_class, "SENSOR_TYPE") or sensor_class.SENSOR_TYPE == "base":
        raise ValueError(f"Sensor class {sensor_class.__name__} must define SENSOR_TYPE")
    
    _SENSOR_REGISTRY[sensor_class.SENSOR_TYPE] = sensor_class
    return sensor_class


def discover_plugins() -> None:
    """Auto-discover all sensor plugins in the advect_daq.plugins package."""
    import advect_daq.plugins

    package = advect_daq.plugins
    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        if module_name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f".{module_name}", package.__name__)
            # Scan for classes that inherit from BaseSensor
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, BaseSensor) and obj is not BaseSensor:
                    if hasattr(obj, "SENSOR_TYPE") and obj.SENSOR_TYPE != "base":
                        _SENSOR_REGISTRY[obj.SENSOR_TYPE] = obj
        except Exception as e:
            log.warning(f"Warning: Failed to load plugin module '{module_name}': {e}")


def get_sensor_class(sensor_type: str) -> Type[BaseSensor]:
    """Get sensor class by type. Raises KeyError if not found."""
    if not _SENSOR_REGISTRY:
        discover_plugins()
    
    if sensor_type not in _SENSOR_REGISTRY:
        raise ValueError(f"No sensor plugin found for type: '{sensor_type}'. "
                        f"Available: {list(_SENSOR_REGISTRY.keys())}")
    
    return _SENSOR_REGISTRY[sensor_type]


def list_available_sensors() -> list[str]:
    """Return list of registered sensor types."""
    if not _SENSOR_REGISTRY:
        discover_plugins()
    return list(_SENSOR_REGISTRY.keys())