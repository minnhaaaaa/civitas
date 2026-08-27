"""Narrow export boundary for the required LangGraph dependency."""

from langgraph.graph import END as END
from langgraph.graph import START as START
from langgraph.graph import StateGraph as StateGraph

__all__ = ["END", "START", "StateGraph"]
