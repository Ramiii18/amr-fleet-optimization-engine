"""Optimal assignment of idle robots to the oldest pending orders, in each decision."""

from collections.abc import Sequence

from amr_optimizer.dispatch.matching import min_cost_matching
from amr_optimizer.sim import Assignment, DispatchSnapshot


class HungarianDispatch:
    """Match the R idle robots to the oldest R serviceable orders at minimum total travel.

    The cost of pairing a robot with an order is the robot's travel time to the pickup, in
    ticks. The rest of a job (loading, the loaded trip, unloading) does not depend on which
    robot does it, so it cannot change which matching is best.

    Considering only the oldest orders (as many as there are idle robots) serves the same
    orders as `GreedyDispatch` would; only who takes which differs. It also prevents
    starvation: a matching over all pending orders would keep choosing nearby orders and
    could leave a distant one waiting indefinitely.

    The problem is the linear assignment problem, classically solved by the Hungarian
    algorithm; the solver is scipy's (see `min_cost_matching`). The matching is optimal for
    each decision on its own, not over the whole run: it knows nothing about future orders.
    """

    name = "hungarian"

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        planner = snapshot.planner
        robots = snapshot.idle_robots
        orders = [
            order
            for order in snapshot.pending_orders
            if planner.ticks(order.pickup, order.dropoff) is not None
        ][: len(robots)]
        costs = [[planner.ticks(robot.node, order.pickup) for order in orders] for robot in robots]
        pairs = min_cost_matching(costs)
        return [
            Assignment(robots[row].robot_id, orders[column].id)
            for row, column in sorted(pairs, key=lambda pair: pair[1])
        ]
