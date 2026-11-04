"""Greedy dispatch: the oldest order first, to the nearest idle robot."""

from collections.abc import Sequence

from amr_optimizer.sim import Assignment, DispatchSnapshot


class GreedyDispatch:
    """For each pending order, oldest first: the idle robot that reaches its pickup soonest.

    Ties go to the robot listed first in the scenario. Orders whose drop-off cannot be
    reached from the pickup, and robots that cannot reach a pickup, are skipped for that
    order; skipped orders stay pending and are reported by the simulator.

    Each decision costs O(P + R * min(R, P)) cached travel-time lookups for P pending
    orders and R idle robots. The rule is myopic: giving the oldest order its nearest robot
    can leave the next order with a robot that is far away (see `HungarianDispatch`).
    """

    name = "greedy"

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        planner = snapshot.planner
        free = list(snapshot.idle_robots)
        assignments: list[Assignment] = []
        for order in snapshot.pending_orders:
            if not free:
                break
            if planner.ticks(order.pickup, order.dropoff) is None:
                continue
            reachable = [
                (ticks, index)
                for index, robot in enumerate(free)
                if (ticks := planner.ticks(robot.node, order.pickup)) is not None
            ]
            if reachable:
                _, index = min(reachable)
                assignments.append(Assignment(free.pop(index).robot_id, order.id))
        return assignments
