"""LangGraph compatibility boundary for workflow compilation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Generic, TypeVar

StateT = TypeVar("StateT")
NodeFunc = Callable[[StateT], Awaitable[StateT]]
RouterFunc = Callable[[StateT], str]

try:
    from langgraph.graph import END, START, StateGraph  # type: ignore
except ImportError:  # pragma: no cover
    START = "__start__"
    END = "__end__"

    class CompiledStateGraph(Generic[StateT]):
        def __init__(
            self,
            *,
            nodes: Mapping[str, NodeFunc[StateT]],
            edges: Mapping[str, str],
            conditional_edges: Mapping[str, tuple[RouterFunc[StateT], Mapping[str, str]]],
        ) -> None:
            self._nodes = dict(nodes)
            self._edges = dict(edges)
            self._conditional_edges = dict(conditional_edges)

        async def ainvoke(self, state: StateT) -> StateT:
            current = self._edges.get(START)
            result = state
            while current is not None and current != END:
                result = await self._nodes[current](result)
                if current in self._conditional_edges:
                    router, mapping = self._conditional_edges[current]
                    current = mapping[router(result)]
                else:
                    current = self._edges.get(current)
            return result

    class StateGraph(Generic[StateT]):
        def __init__(self, _state_type: type[StateT]) -> None:
            self._nodes: dict[str, NodeFunc[StateT]] = {}
            self._edges: dict[str, str] = {}
            self._conditional_edges: dict[str, tuple[RouterFunc[StateT], Mapping[str, str]]] = {}

        def add_node(self, name: str, func: NodeFunc[StateT]) -> None:
            self._nodes[name] = func

        def add_edge(self, start: str, end: str) -> None:
            self._edges[start] = end

        def add_conditional_edges(
            self,
            start: str,
            router: RouterFunc[StateT],
            mapping: Mapping[str, str],
        ) -> None:
            self._conditional_edges[start] = (router, mapping)

        def compile(self, **_: Any) -> CompiledStateGraph[StateT]:
            return CompiledStateGraph(
                nodes=self._nodes,
                edges=self._edges,
                conditional_edges=self._conditional_edges,
            )
