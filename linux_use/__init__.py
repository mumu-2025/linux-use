"""linux-use: Linux desktop GUI agent driven by AT-SPI."""

from .agent import Agent, AgentResult
from .llm import OpenAICompatLLM
from .tree import TreeSnapshot, UIElement
from .api import act, get_location_map, inspect_desktop, scripted
from .locmap import LocationMap, LocEntry

__all__ = [
    "Agent",
    "AgentResult",
    "OpenAICompatLLM",
    "TreeSnapshot",
    "UIElement",
    "LocationMap",
    "LocEntry",
    "act",
    "inspect_desktop",
    "get_location_map",
    "scripted",
    "__version__",
]

__version__ = "0.1.0"
