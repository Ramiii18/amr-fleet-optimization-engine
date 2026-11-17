"""Strongly connected components: can every place be reached from every other? (D39)

**Tarjan's algorithm, iterative.** A depth-first search numbers nodes in the order it reaches them
(``index``) and tracks, for each node, the smallest number reachable from its search subtree
through at most one edge back into the current search path (``low``). A node whose ``low`` equals
its own number is the root of a component: everything above it on the stack belongs to it.

The search keeps its own stack of (node, remaining out-edges) instead of recursing, so a long
aisle of thousands of cells cannot hit Python's recursion limit.

**Cost:** O(V + E) time and O(V) memory: every node is pushed and popped once, every edge looked
at once. Components come out in reverse topological order of the condensation: if an edge leads
from component A to component B, B comes out before A. The order is deterministic, because the
search follows the graph's own node and edge order (D7).
"""

from collections.abc import Iterator

from amr_optimizer.warehouse import Edge, NodeId, WarehouseGraph


def strongly_connected_components(graph: WarehouseGraph) -> tuple[tuple[NodeId, ...], ...]:
    """The strongly connected components of ``graph``, in reverse topological order."""
    index: dict[NodeId, int] = {}
    low: dict[NodeId, int] = {}
    stack: list[NodeId] = []
    on_stack: set[NodeId] = set()
    components: list[tuple[NodeId, ...]] = []

    def visit(node: NodeId) -> tuple[NodeId, Iterator[Edge]]:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        return node, iter(graph.out_edges(node))

    for root in graph:
        if root.id in index:
            continue
        work = [visit(root.id)]
        while work:
            node, edges = work[-1]
            for edge in edges:
                target = edge.target
                if target not in index:
                    work.append(visit(target))
                    break
                if target in on_stack:
                    low[node] = min(low[node], index[target])
            else:  # every edge of ``node`` is done
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component: list[NodeId] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    components.append(tuple(component))
    return tuple(components)


def is_strongly_connected(graph: WarehouseGraph) -> bool:
    """True if every node can reach every other (an empty graph counts as connected)."""
    return len(strongly_connected_components(graph)) <= 1
