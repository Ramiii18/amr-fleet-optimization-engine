"""Layout files and their compiler (D38): hand-checked grids, edge by edge."""

import json
from pathlib import Path
from typing import Any

import networkx as nx
import pytest

from amr_optimizer.layout import (
    Layout,
    LayoutFormatError,
    cell_node_id,
    compile_layout,
    layout_from_dict,
    layout_to_dict,
    load_layout,
    load_map_file,
    map_from_dict,
    save_layout,
)
from amr_optimizer.layout.model import ENDPOINTS, is_floor
from amr_optimizer.warehouse import (
    Edge,
    MapValidationError,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
    save_graph,
)
from amr_optimizer.warehouse.io import graph_to_dict

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "layouts" / "examples"

TWO_AISLES = (
    "########",
    "#C####P#",
    "#......#",
    "#^##v###",
    "#^S#vS##",
    "#^##v###",
    "#^S#vS##",
    "#^##v###",
    "#......#",
    "#T#T#?##",
    "########",
)


def node_at(layout: Layout, cell: tuple[int, int]) -> NodeId:
    char = layout.at(cell)
    kind = NodeKind.INTERSECTION if is_floor(char) else ENDPOINTS[char]
    return cell_node_id(kind, cell)


def edge_set(graph: WarehouseGraph) -> set[tuple[NodeId, NodeId]]:
    return {edge.key for edge in graph.edges}


def expected(
    layout: Layout,
    two_way: list[tuple[tuple[int, int], tuple[int, int]]],
    one_way: list[tuple[tuple[int, int], tuple[int, int]]],
) -> set[tuple[NodeId, NodeId]]:
    edges = set()
    for a, b in two_way:
        ida, idb = node_at(layout, a), node_at(layout, b)
        edges |= {(ida, idb), (idb, ida)}
    for a, b in one_way:
        edges.add((node_at(layout, a), node_at(layout, b)))
    return edges


# --- the hand-checked example -------------------------------------------------------------------


def test_the_two_aisle_example_compiles_edge_by_edge() -> None:
    layout = Layout(TWO_AISLES).assign({(9, 5): NodeKind.STATION})
    graph = compile_layout(layout)
    cross = [((r, c), (r, c + 1)) for r in (2, 8) for c in range(1, 6)]
    spurs = [
        ((1, 1), (2, 1)),  # charger
        ((1, 6), (2, 6)),  # parking
        ((4, 2), (4, 1)),
        ((6, 2), (6, 1)),
        ((4, 5), (4, 4)),
        ((6, 5), (6, 4)),  # storage beside the aisles
        ((9, 1), (8, 1)),
        ((9, 3), (8, 3)),
        ((9, 5), (8, 5)),  # stations, the last one the assigned slot
    ]
    up = [((r, 1), (r - 1, 1)) for r in range(8, 2, -1)]  # column 1, from row 8 to row 2
    down = [((r, 4), (r + 1, 4)) for r in range(2, 8)]  # column 4, from row 2 to row 8
    assert edge_set(graph) == expected(layout, cross + spurs, up + down)
    assert (len(graph), graph.num_edges) == (31, 50)
    assert all(edge.length_m == 1.0 for edge in graph.edges)
    counts = {kind: len(graph.nodes_of_kind(kind)) for kind in NodeKind}
    assert counts == {
        NodeKind.INTERSECTION: 22,
        NodeKind.STORAGE: 4,
        NodeKind.STATION: 3,
        NodeKind.CHARGER: 1,
        NodeKind.PARKING: 1,
    }


def test_an_unassigned_slot_is_not_in_the_map() -> None:
    graph = compile_layout(Layout(TWO_AISLES))
    assert (len(graph), graph.num_edges) == (30, 48)
    assert "ST-r009-c005" not in graph
    assert "X-r008-c005" in graph


def test_ids_and_coordinates_follow_the_cell() -> None:
    layout = Layout(("S..", ".#T"), cell_m=0.5)
    graph = compile_layout(layout)
    assert [(n.id, n.x, n.y, n.kind) for n in graph] == [
        ("S-r000-c000", 0.0, 0.5, NodeKind.STORAGE),
        ("X-r000-c001", 0.5, 0.5, NodeKind.INTERSECTION),
        ("X-r000-c002", 1.0, 0.5, NodeKind.INTERSECTION),
        ("X-r001-c000", 0.0, 0.0, NodeKind.INTERSECTION),
        ("ST-r001-c002", 1.0, 0.0, NodeKind.STATION),
    ]
    assert all(edge.length_m == 0.5 for edge in graph.edges)


def test_a_small_layout_equals_its_hand_built_graph() -> None:
    """Two aisles (up, down) between two cross aisles, a rack with one storage cell facing each
    aisle and blocked ends, two stations; 2 m cells. The graph is written out by hand."""
    layout = Layout(("....", "^##v", "^SSv", "^##v", "....", "T##T"), cell_m=2.0)

    def node(name: str, x: float, y: float, kind: NodeKind = NodeKind.INTERSECTION) -> Node:
        return Node(NodeId(name), x, y, kind)

    nodes = [
        *(node(f"X-r000-c00{c}", 2.0 * c, 10.0) for c in range(4)),
        *(node(f"X-r00{r}-c00{c}", 2.0 * c, 10.0 - 2.0 * r) for r in (1, 2, 3) for c in (0, 3)),
        node("S-r002-c001", 2.0, 6.0, NodeKind.STORAGE),
        node("S-r002-c002", 4.0, 6.0, NodeKind.STORAGE),
        *(node(f"X-r004-c00{c}", 2.0 * c, 2.0) for c in range(4)),
        node("ST-r005-c000", 0.0, 0.0, NodeKind.STATION),
        node("ST-r005-c003", 6.0, 0.0, NodeKind.STATION),
    ]

    def both(a: str, b: str) -> list[Edge]:
        return [Edge(NodeId(a), NodeId(b), 2.0), Edge(NodeId(b), NodeId(a), 2.0)]

    def one(a: str, b: str) -> list[Edge]:
        return [Edge(NodeId(a), NodeId(b), 2.0)]

    edges = [
        *(
            e
            for r in (0, 4)
            for c in range(3)
            for e in both(f"X-r00{r}-c00{c}", f"X-r00{r}-c00{c + 1}")
        ),
        *(e for r in range(4, 0, -1) for e in one(f"X-r00{r}-c000", f"X-r00{r - 1}-c000")),  # up
        *(e for r in range(4) for e in one(f"X-r00{r}-c003", f"X-r00{r + 1}-c003")),  # down
        *both("S-r002-c001", "X-r002-c000"),
        *both("S-r002-c002", "X-r002-c003"),
        *both("ST-r005-c000", "X-r004-c000"),
        *both("ST-r005-c003", "X-r004-c003"),
    ]
    graph, hand = compile_layout(layout), WarehouseGraph(nodes, edges)
    assert (len(hand), hand.num_edges) == (18, 28)
    assert {(n.id, n.x, n.y, n.kind) for n in graph} == {(n.id, n.x, n.y, n.kind) for n in hand}
    assert {(e.key, e.length_m) for e in graph.edges} == {(e.key, e.length_m) for e in hand.edges}


# --- the one-way rule ---------------------------------------------------------------------------


def test_an_arrow_constrains_its_own_axis_only() -> None:
    layout = Layout(("...", ".^.", "..."))
    centre = node_at(layout, (1, 1))
    edges = edge_set(compile_layout(layout))
    assert (node_at(layout, (2, 1)), centre) in edges  # up into the arrow
    assert (centre, node_at(layout, (2, 1))) not in edges
    assert (centre, node_at(layout, (0, 1))) in edges  # up out of it
    assert (node_at(layout, (0, 1)), centre) not in edges
    for side in ((1, 0), (1, 2)):  # across the arrow's axis: two-way
        assert {(centre, node_at(layout, side)), (node_at(layout, side), centre)} <= edges


@pytest.mark.parametrize(
    ("grid", "one_way"),
    [
        ((".>.",), [((0, 0), (0, 1)), ((0, 1), (0, 2))]),
        ((".<.",), [((0, 1), (0, 0)), ((0, 2), (0, 1))]),
        (("^", "^"), [((1, 0), (0, 0))]),
        (("v", "v"), [((0, 0), (1, 0))]),
        ((">", "^"), [((1, 0), (0, 0))]),  # only the arrow on the vertical axis counts
        ((">>",), [((0, 0), (0, 1))]),
    ],
)
def test_arrows_give_one_edge_in_their_direction(
    grid: tuple[str, ...], one_way: list[tuple[tuple[int, int], tuple[int, int]]]
) -> None:
    layout = Layout(grid)
    assert edge_set(compile_layout(layout)) == expected(layout, [], one_way)


@pytest.mark.parametrize(
    ("grid", "where"),
    [
        (("v", "^"), "layout.grid[0][0] 'v' and layout.grid[1][0] '^'"),
        (("^", "v"), "layout.grid[0][0] '^' and layout.grid[1][0] 'v'"),
        (("#><#",), "layout.grid[0][1] '>' and layout.grid[0][2] '<'"),
        (("<>",), "layout.grid[0][0] '<' and layout.grid[0][1] '>'"),
    ],
)
def test_opposing_arrows_are_refused_with_their_cells(grid: tuple[str, ...], where: str) -> None:
    with pytest.raises(LayoutFormatError, match="point against each other") as raised:
        compile_layout(Layout(grid))
    assert where in str(raised.value)


# --- endpoints ----------------------------------------------------------------------------------


def test_endpoints_join_the_floor_beside_them_but_never_each_other() -> None:
    layout = Layout(("SS", ".."))
    assert edge_set(compile_layout(layout)) == expected(
        layout, [((0, 0), (1, 0)), ((0, 1), (1, 1)), ((1, 0), (1, 1))], []
    )


def test_a_spur_is_two_way_even_beside_an_arrow_on_its_axis() -> None:
    layout = Layout(("S", "^", "."))
    assert edge_set(compile_layout(layout)) == expected(
        layout, [((0, 0), (1, 0))], [((2, 0), (1, 0))]
    )


def test_the_compiler_does_not_judge_fitness_for_traffic() -> None:
    """An endpoint between two floor cells is compiled as drawn; the checker reports it (L2)."""
    layout = Layout((".S.",))
    assert edge_set(compile_layout(layout)) == expected(
        layout, [((0, 0), (0, 1)), ((0, 1), (0, 2))], []
    )


# --- slots --------------------------------------------------------------------------------------


def test_slots_are_listed_in_reading_order_and_assigned_by_kind() -> None:
    layout = Layout(("?.?", "#.#", "?.#"))
    assert layout.slots() == ((0, 0), (0, 2), (2, 0))
    assigned = layout.assign({(0, 2): NodeKind.CHARGER, (2, 0): NodeKind.PARKING})
    assert assigned.grid == ("?.C", "#.#", "P.#")
    graph = compile_layout(assigned)
    assert {n.id for n in graph if n.kind is not NodeKind.INTERSECTION} == {
        "CH-r000-c002",
        "PK-r002-c000",
    }
    assert {n.id for n in graph if n.kind is NodeKind.INTERSECTION} == {
        n.id for n in compile_layout(layout) if n.kind is NodeKind.INTERSECTION
    }, "floor ids do not depend on the slots"


@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        ({(0, 1): NodeKind.STATION}, "not a candidate slot"),
        ({(5, 5): NodeKind.STATION}, "not a candidate slot"),
        ({(0, 0): NodeKind.STORAGE}, "not a storage"),
        ({(0, 0): NodeKind.INTERSECTION}, "not a intersection"),
    ],
)
def test_a_bad_assignment_is_refused(assignment: dict[Any, NodeKind], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Layout(("?.",)).assign(assignment)


# --- the file format ----------------------------------------------------------------------------


def valid() -> dict[str, Any]:
    return {"format": "amr-layout", "version": 1, "cell_m": 1.0, "grid": ["S.", "^T"]}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.pop("grid"), r"layout: missing field\(s\) \['grid'\]"),
        (lambda d: d.update(extra=1), r"layout: unknown field\(s\) \['extra'\]"),
        (lambda d: d.update(format="amr-warehouse-map"), "layout.format: must be 'amr-layout'"),
        (lambda d: d.update(version=2), "layout.version: unsupported version 2"),
        (lambda d: d.update(version=True), "layout.version: unsupported version True"),
        (lambda d: d.update(cell_m="1"), "layout.cell_m: expected a number"),
        (lambda d: d.update(cell_m=True), "layout.cell_m: expected a number"),
        (lambda d: d.update(cell_m=0), "layout.cell_m: must be a positive number"),
        (lambda d: d.update(cell_m=-1.0), "layout.cell_m: must be a positive number"),
        (lambda d: d.update(cell_m=float("inf")), "layout.cell_m: must be a positive number"),
        (lambda d: d.update(description=3), "layout.description: expected a string"),
        (lambda d: d.update(grid="S."), "layout.grid: expected a list of strings"),
        (lambda d: d.update(grid=[]), "layout.grid: needs at least one row"),
        (lambda d: d.update(grid=["S.", 7]), r"layout.grid\[1\]: expected a string"),
        (lambda d: d.update(grid=["S.", ""]), r"layout.grid\[1\]: a row needs at least one cell"),
        (lambda d: d.update(grid=["S.", "^T."]), r"layout.grid\[1\]: has 3 cells, but row 0 has 2"),
        (lambda d: d.update(grid=["S.", "^x"]), r"layout.grid\[1\]\[1\]: unknown cell 'x'"),
    ],
)
def test_a_malformed_file_is_refused_with_its_location(change: Any, message: str) -> None:
    data = valid()
    change(data)
    with pytest.raises(LayoutFormatError, match=message):
        layout_from_dict(data)


def test_a_file_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(LayoutFormatError, match="layout: expected an object"):
        layout_from_dict([])


def test_writing_and_reading_round_trip_with_the_same_bytes(tmp_path: Path) -> None:
    layout = Layout(TWO_AISLES, 1.0, "Synthetic test hall")
    first, second = tmp_path / "a.layout.json", tmp_path / "b.layout.json"
    save_layout(layout, first)
    save_layout(load_layout(first), second)
    assert load_layout(first) == layout
    assert first.read_bytes() == second.read_bytes()
    assert b"\r" not in first.read_bytes()
    text = first.read_text(encoding="utf-8")
    assert '    "#^S#vS##",\n' in text, "one grid row per line"
    assert json.loads(text) == layout_to_dict(layout)


def test_compiling_twice_writes_the_same_map_bytes(tmp_path: Path) -> None:
    layout = load_layout(EXAMPLES / "l_hall.layout.json")
    save_graph(compile_layout(layout), tmp_path / "a.json")
    save_graph(compile_layout(load_layout(EXAMPLES / "l_hall.layout.json")), tmp_path / "b.json")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_one_loader_reads_map_files_and_layout_files(tmp_path: Path) -> None:
    graph = compile_layout(Layout(TWO_AISLES))
    save_graph(graph, tmp_path / "map.json")
    save_layout(Layout(TWO_AISLES), tmp_path / "plan.layout.json")
    for path in (tmp_path / "map.json", tmp_path / "plan.layout.json"):
        assert graph_to_dict(load_map_file(path)) == graph_to_dict(graph)
    assert graph_to_dict(map_from_dict(layout_to_dict(Layout(TWO_AISLES)))) == graph_to_dict(graph)


def test_the_loader_names_both_formats_when_neither_matches(tmp_path: Path) -> None:
    with pytest.raises(MapValidationError, match="'amr-warehouse-map' or 'amr-layout'"):
        map_from_dict({"format": "amr-scenario"})
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(MapValidationError, match="not valid JSON"):
        load_map_file(tmp_path / "broken.json")


# --- the committed examples ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "size"),
    [("two_aisles", (30, 48)), ("small_hall", (101, 170)), ("l_hall", (273, 494))],
)
def test_the_examples_compile_strongly_connected_with_leaf_endpoints(
    name: str, size: tuple[int, int], tmp_path: Path
) -> None:
    """NetworkX is the oracle here; the engine's own checker arrives in L2."""
    path = EXAMPLES / f"{name}.layout.json"
    layout = load_layout(path)
    assert "Synthetic" in layout.description
    graph = load_map_file(path)
    assert (len(graph), graph.num_edges) == size
    digraph: nx.DiGraph[NodeId] = nx.DiGraph()
    digraph.add_nodes_from(node.id for node in graph)
    digraph.add_edges_from(edge.key for edge in graph.edges)
    assert nx.is_strongly_connected(digraph)
    for node in graph:
        if node.kind is not NodeKind.INTERSECTION:
            (neighbour,) = digraph.successors(node.id)
            assert list(digraph.predecessors(node.id)) == [neighbour]
            assert graph.node(neighbour).kind is NodeKind.INTERSECTION
    save_layout(layout, tmp_path / "rewritten.layout.json")
    assert (tmp_path / "rewritten.layout.json").read_bytes() == path.read_bytes(), (
        "the committed file is in the writer's canonical form"
    )
