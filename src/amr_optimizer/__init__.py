"""AMR Fleet Optimization Engine.

A simulation-based engine for dispatching transport orders to a fleet of autonomous
mobile robots (AMRs), planning their routes on a warehouse graph, and managing traffic
between them.

It works on synthetic maps and synthetic orders only. It does not control physical robots.
"""

from importlib.metadata import version

# The version is written once, in pyproject.toml. pip records it as package metadata
# when the project is installed, and we read it back here: one source of truth.
__version__ = version("amr-fleet-optimization-engine")
