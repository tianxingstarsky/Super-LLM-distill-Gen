"""Compose request policies with clients; construction performs no request."""
from lib.contexts.models.application.execute_request import ExecuteModelRequest
from lib.contexts.models.application.request_json import RequestJSONResponse
from lib.contexts.models.application.execute_tool_turn import ExecuteToolTurn
from lib.contexts.models.infrastructure.request_adapter import SDKModelRequestAdapter, ClientJSONResponseAdapter
from lib.contexts.models.infrastructure.tool_request_adapter import SDKToolRequestAdapter


def model_request(client) -> ExecuteModelRequest:
    return ExecuteModelRequest(SDKModelRequestAdapter(client))


def json_response_request(client) -> RequestJSONResponse:
    return RequestJSONResponse(ClientJSONResponseAdapter(client))


def tool_turn_request(client) -> ExecuteToolTurn:
    return ExecuteToolTurn(SDKToolRequestAdapter(client))
