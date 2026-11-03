"""A* heuristics: estimates of the remaining route length from a node to the goal, in metres.

Two properties matter:

- **Admissible**: never overestimates the true remaining length. A* is then optimal *if* it
  may reopen nodes.
- **Consistent** (monotone): ``h(u) <= length(u -> v) + h(v)`` for every edge ``u -> v``.
  Then the first expansion of a node is final, so A* never has to reopen it. Consistent
  implies admissible (when ``h(goal) == 0``).

Our `astar` does not reopen nodes, so it needs a **consistent** heuristic to be optimal.
"""

from collections.abc import Callable

from amr_optimizer.warehouse import Node

Heuristic = Callable[[Node, Node], float]
"""``heuristic(node, goal)``: a non-negative estimate of the remaining length in metres."""


def zero(node: Node, goal: Node) -> float:
    """No guidance at all. A* with this heuristic expands exactly like Dijkstra."""
    return 0.0


def euclidean(node: Node, goal: Node) -> float:
    """Straight-line distance. Consistent on every valid `WarehouseGraph`.

    Proof: for an edge u -> v, by the triangle inequality and D5 (length >= straight-line
    distance), ``|u - goal| <= |u - v| + |v - goal| <= length(u -> v) + |v - goal|``.
    """
    return node.distance_to(goal)


def manhattan(node: Node, goal: Node) -> float:
    """``|dx| + |dy|``. Tighter than Euclidean, but only safe on axis-aligned graphs.

    It is consistent when every edge is at least as long as the Manhattan distance between
    its endpoints, which holds when all edges run along the x or y axis (grid warehouses).
    With diagonal edges it can overestimate, and `astar` may then return a longer route.
    """
    return abs(goal.x - node.x) + abs(goal.y - node.y)
