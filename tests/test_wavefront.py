"""Tests for wavefront scheduling."""
from pact.wavefront import WavefrontScheduler, ComponentPhase
from pact.schemas import DecompositionNode, DecompositionTree


def _make_tree(specs: list[tuple[str, str, list[str]]]) -> DecompositionTree:
    """Helper: specs = [(id, parent_id, [children])]"""
    nodes = {}
    root_id = specs[0][0]
    for nid, parent, children in specs:
        depth = 0
        p = parent
        while p:
            depth += 1
            pspec = next((s for s in specs if s[0] == p), None)
            p = pspec[1] if pspec else ""
        nodes[nid] = DecompositionNode(
            component_id=nid, name=nid, description=f"Component {nid}",
            depth=depth, parent_id=parent, children=children,
        )
    return DecompositionTree(root_id=root_id, nodes=nodes)


class TestWavefrontScheduler:
    def test_leaves_start_in_parallel(self):
        """Three independent leaves should all appear in first ready set."""
        tree = _make_tree([
            ("root", "", ["a", "b", "c"]),
            ("a", "root", []),
            ("b", "root", []),
            ("c", "root", []),
        ])
        ws = WavefrontScheduler(tree, max_concurrent=10)
        ready = ws.compute_ready_set()
        # All three leaves should be ready for CONTRACT
        assert len(ready) == 4  # 3 leaves + root (root can also contract)
        leaf_ready = [(cid, p) for cid, p in ready if ws.states[cid].is_leaf]
        assert len(leaf_ready) == 3
        assert all(phase == "contract" for _, phase in leaf_ready)
        component_ids = {cid for cid, _ in leaf_ready}
        assert component_ids == {"a", "b", "c"}

    def test_parent_not_ready_until_children_done(self):
        """Root can contract early but cannot integrate until children complete."""
        tree = _make_tree([
            ("root", "", ["a", "b"]),
            ("a", "root", []),
            ("b", "root", []),
        ])
        ws = WavefrontScheduler(tree, max_concurrent=10)

        # Root CAN contract in parallel with leaves
        ready = ws.compute_ready_set()
        component_ids = {cid for cid, _ in ready}
        assert "root" in component_ids

        # Advance root through to IMPLEMENT
        ws.advance("root", "contract")
        ws.advance("root", "test")
        ws.advance("root", "implement")

        # Root should NOT be ready for integrate (children not complete)
        ready = ws.compute_ready_set()
        root_ready = [(cid, p) for cid, p in ready if cid == "root"]
        assert not root_ready or root_ready[0][1] != "integrate"

    def test_dependent_waits_for_dependency(self):
        """D depends on B -> D can't implement until B implements."""
        tree = _make_tree([
            ("root", "", ["b", "d"]),
            ("b", "root", []),
            ("d", "root", []),
        ])
        ws = WavefrontScheduler(tree, max_concurrent=10)
        ws.set_dependencies("d", ["b"])

        # Both start contracting
        ws.advance("b", "contract")
        ws.advance("d", "contract")
        ws.advance("b", "test")
        ws.advance("d", "test")

        # Now B can implement, but D can't (depends on B)
        ready = ws.compute_ready_set()
        ready_ids = {cid for cid, _ in ready}
        assert "b" in ready_ids
        # D should NOT be ready for implement since B hasn't implemented yet
        d_ready = [(cid, p) for cid, p in ready if cid == "d"]
        if d_ready:
            assert d_ready[0][1] != "implement"

    def test_dependency_satisfied_allows_advance(self):
        """After B implements, D can implement."""
        tree = _make_tree([
            ("root", "", ["b", "d"]),
            ("b", "root", []),
            ("d", "root", []),
        ])
        ws = WavefrontScheduler(tree, max_concurrent=10)
        ws.set_dependencies("d", ["b"])

        # Complete B through implementation
        ws.advance("b", "contract")
        ws.advance("d", "contract")
        ws.advance("b", "test")
        ws.advance("d", "test")
        ws.advance("b", "implement")  # B is now COMPLETE (leaf auto-advances)

        ready = ws.compute_ready_set()
        ready_map = {cid: p for cid, p in ready}
        assert "d" in ready_map
        assert ready_map["d"] == "implement"

    def test_integration_waits_for_all_children(self):
        """Parent can't integrate until all children are complete."""
        tree = _make_tree([
            ("root", "", ["a", "b"]),
            ("a", "root", []),
            ("b", "root", []),
        ])
        ws = WavefrontScheduler(tree, max_concurrent=10)

        # Complete A but not B
        ws.advance("a", "contract")
        ws.advance("a", "test")
        ws.advance("a", "implement")  # A is COMPLETE (leaf)

        # Root: contract -> test -> implement done, but can't integrate
        ws.advance("root", "contract")
        ws.advance("root", "test")
        ws.advance("root", "implement")

        ready = ws.compute_ready_set()
        root_ready = [(cid, p) for cid, p in ready if cid == "root"]
        # Root should NOT be ready for integrate (B not complete)
        if root_ready:
            assert root_ready[0][1] != "integrate"

        # Now complete B
        ws.advance("b", "contract")
        ws.advance("b", "test")
        ws.advance("b", "implement")  # B is COMPLETE

        ready = ws.compute_ready_set()
        root_ready = [(cid, p) for cid, p in ready if cid == "root"]
        assert len(root_ready) == 1
        assert root_ready[0][1] == "integrate"

    def test_respects_max_concurrent(self):
        """Ready set never exceeds max_concurrent."""
        tree = _make_tree([
            ("root", "", ["a", "b", "c", "d", "e"]),
            ("a", "root", []),
            ("b", "root", []),
            ("c", "root", []),
            ("d", "root", []),
            ("e", "root", []),
        ])
        ws = WavefrontScheduler(tree, max_concurrent=2)
        ready = ws.compute_ready_set()
        assert len(ready) <= 2

    def test_is_complete(self):
        """Scheduler reports complete when all components done."""
        tree = _make_tree([
            ("root", "", ["a"]),
            ("a", "root", []),
        ])
        ws = WavefrontScheduler(tree, max_concurrent=10)
        assert not ws.is_complete()

        # Complete leaf
        ws.advance("a", "contract")
        ws.advance("a", "test")
        ws.advance("a", "implement")
        assert not ws.is_complete()

        # Complete root
        ws.advance("root", "contract")
        ws.advance("root", "test")
        ws.advance("root", "implement")
        ws.advance("root", "integrate")
        ws.advance("root", "complete")
        assert ws.is_complete()

    def test_leaf_auto_completes_after_implement(self):
        """Leaves skip integrate and go straight to complete."""
        tree = _make_tree([
            ("root", "", ["a"]),
            ("a", "root", []),
        ])
        ws = WavefrontScheduler(tree, max_concurrent=10)
        ws.advance("a", "contract")
        ws.advance("a", "test")
        ws.advance("a", "implement")
        assert ws.states["a"].current_phase == ComponentPhase.COMPLETE

    def test_empty_tree(self):
        tree = DecompositionTree(root_id="root", nodes={})
        ws = WavefrontScheduler(tree)
        assert ws.compute_ready_set() == []
        assert ws.is_complete()

    def test_single_component(self):
        """Single leaf component progresses through all phases."""
        tree = _make_tree([
            ("only", "", []),
        ])
        ws = WavefrontScheduler(tree, max_concurrent=10)

        ready = ws.compute_ready_set()
        assert ready == [("only", "contract")]

        ws.advance("only", "contract")
        ready = ws.compute_ready_set()
        assert ready == [("only", "test")]

        ws.advance("only", "test")
        ready = ws.compute_ready_set()
        assert ready == [("only", "implement")]

        ws.advance("only", "implement")
        assert ws.is_complete()

    def test_set_dependencies(self):
        """set_dependencies correctly records deps."""
        tree = _make_tree([
            ("root", "", ["a", "b"]),
            ("a", "root", []),
            ("b", "root", []),
        ])
        ws = WavefrontScheduler(tree)
        ws.set_dependencies("b", ["a"])
        assert ws.states["b"].dependencies == ["a"]

    def test_advance_unknown_component_noop(self):
        """Advancing unknown component doesn't crash."""
        tree = _make_tree([("root", "", [])])
        ws = WavefrontScheduler(tree)
        ws.advance("nonexistent", "contract")  # Should not raise
