"""Show actual model deltas in the node the operator has opened."""
from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


_output = components.declare_component(
    "workflow_stream", path=str(Path(__file__).resolve().parents[2] / "components" / "workflow_stream"))
_FIELDS = ("id", "stage", "unit", "role", "status", "text", "reasoning", "updated_at",
           "started_at", "attempt", "run_attempt", "text_chars", "reasoning_chars", "truncated")


def stream_spec(rows: list[dict], run_id: str, stage: str, language: str, *,
                selected_id=None, ack_serial=None, delta=None, follow_latest=True,
                delta_enabled=False, reader_height=None) -> dict:
    """Send only this node's received output, never arbitrary journal metadata."""
    selected = [{key: row[key] for key in _FIELDS if key in row}
                for row in rows if row.get("stage") == stage][:32]
    selected.sort(key=lambda row: (row.get("started_at") or row.get("updated_at", ""), row["id"]))
    if delta is not None or delta_enabled:
        for row in selected:
            row["text"] = row["reasoning"] = ""
            if row["id"] == selected_id and delta is not None:
                row["truncated"] = delta.get("truncated", False)
    spec = {"context": f"{run_id}:{stage}", "rows": selected,
            "language": "en" if language == "en" else "zh", "labels": {},
            "selected_id": selected_id, "ack_serial": ack_serial, "delta": delta,
            "follow_latest": follow_latest}
    if type(reader_height) is int and 160 <= reader_height <= 600:
        spec["reader_height"] = reader_height
    return spec


def accept_selection(event, context, request_ids, previous_serial):
    """Treat iframe selections as untrusted; a late event cannot select another node."""
    if (not isinstance(event, dict) or event.get("context") != context
            or "receipt" in event
            or not isinstance(event.get("serial"), str) or not 1 <= len(event["serial"]) <= 128
            or event["serial"] == previous_serial or type(event.get("follow_latest")) is not bool
            or type(event.get("reset", False)) is not bool):
        return None
    request_id = event.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        return None
    if request_id not in request_ids and not (request_id is None and event["follow_latest"]):
        return None
    return {"id": request_id, "follow": event["follow_latest"], "serial": event["serial"],
            "reset": event.get("reset", False)}


def accept_receipt(event, context, selected_id, selection_serial, offer):
    """Advance only after this view has consumed the exact bounded page offered."""
    if (not isinstance(event, dict) or event.get("receipt") is not True
            or event.get("context") != context or event.get("request_id") != selected_id
            or event.get("selection_serial") != selection_serial
            or not isinstance(event.get("serial"), str) or not 1 <= len(event["serial"]) <= 128
            or not isinstance(offer, dict) or offer.get("id") != selected_id
            or type(event.get("next_offset")) is not int
            or event["next_offset"] != offer.get("next_offset")
            or event["next_offset"] <= offer.get("offset", 0)):
        return None
    return event["next_offset"]


@st.fragment(run_every=0.1)
def render_stream_output(application, run_id: str, stage: str, *, reader_height=None) -> None:
    # Only this small fragment refreshes rapidly. The component keeps its text
    # nodes and appends the received suffix; it never replays a finished answer.
    if not st.session_state.get(f"canvas-open:live-canvas:{run_id}"):
        return
    # A fast fragment may still have the previous node's arguments while its
    # parent commits a new selection. Do not read or acknowledge that old view.
    selected_stage = st.session_state.get(f"workflow-stage:{run_id}")
    if selected_stage is not None and selected_stage != stage:
        return
    reader = getattr(application, "read_streams", None)
    if reader is None:
        st.caption("该节点暂无模型输出，运行信息见节点日志。")
        return
    try:
        requests = reader(run_id, stage=stage)
        requests = [row for row in requests if row.get("stage") == stage]
        if not requests:
            # Local-only stages and old runs may have no model journal. Do not
            # mount an empty reader or discard any preserved reading cursor.
            st.caption("该节点暂无模型输出，运行信息见节点日志。")
            return
        context = f"{run_id}:{stage}"
        view_key = f"workflow-token-view:{context}"
        cursor_key = f"workflow-token-cursor:{context}"
        offer_key = f"workflow-token-offer:{context}"
        view = st.session_state.get(view_key, {"follow": True, "id": None, "serial": None})
        if view["follow"]:
            active = [row for row in requests if row.get("status") == "active"]
            selected = max(active or requests,
                           key=lambda row: (row.get("started_at") or row.get("updated_at", ""), row["id"]),
                           default=None)
        else:
            selected = next((row for row in requests if row["id"] == view["id"]), None)
        delta = None
        read_delta = getattr(application, "read_stream_delta", None)
        if selected is not None and read_delta:
            cursor = st.session_state.get(cursor_key, {})
            offset = cursor.get("offset", 0) if cursor.get("id") == selected["id"] else 0
            offer = st.session_state.get(offer_key)
            if offer and offer.get("id") == selected["id"] and offer.get("offset") == offset:
                delta = offer
            else:
                delta = read_delta(run_id, selected["id"], stage=stage, offset=offset)
            if delta.get("id") != selected["id"]:
                raise ValueError("invalid_stream_delta")
            # A late-mounted iframe can miss a render. Retain only one bounded
            # page, and resend it until the component confirms consumption.
            if delta["next_offset"] > offset:
                st.session_state[offer_key] = delta
            else:
                st.session_state.pop(offer_key, None)
        else:
            st.session_state.pop(offer_key, None)
    except (OSError, ValueError, KeyError, TypeError):
        st.caption("实时输出暂时无法读取；已完成的样本断点仍会保留。")
        return
    event = _output(spec=stream_spec(requests, run_id, stage, st.session_state.get("ui_language", "zh"),
                                    selected_id=selected["id"] if selected else None,
                                    ack_serial=view["serial"], delta=delta, follow_latest=view["follow"],
                                    delta_enabled=read_delta is not None, reader_height=reader_height),
                    key=f"workflow-token-output:{run_id}:{stage}", default=None)
    consumed = accept_receipt(event, context, selected["id"] if selected else None,
                              view["serial"], st.session_state.get(offer_key))
    if consumed is not None:
        st.session_state[cursor_key] = {"id": selected["id"], "offset": consumed}
        st.session_state.pop(offer_key, None)
    accepted = accept_selection(event, context, {row["id"] for row in requests}, view["serial"])
    if accepted:
        st.session_state[view_key] = accepted
        st.session_state.pop(cursor_key, None)
        st.session_state.pop(offer_key, None)
