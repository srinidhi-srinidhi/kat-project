"""Testing Endpoints - HTTP route that drives the automation agent for UI tests.

HTTP layer for /api/testing: POST /test_agent seeds the Flutter UI state into the test context, runs the automation agent on the query, and returns the last tool output parsed as JSON; the route is unauthenticated.

Features:
    - Agent testing: Runs the automation agent on the user query per request
    - Flutter integration: Seeds the Flutter UI state into the test context for tools
    - Structured responses: Returns the last ToolMessage content parsed as JSON
    - Error handling: Error payload when no tool ran or output is not JSON; 500 on failure
    - Unauthenticated: /test_agent does not apply the bearer token

Endpoints:
    POST /test_agent: Execute LLM-driven automation testing agent

Classes:
    No classes defined (module exposes a single FastAPI router endpoint)

Functions:
    run_test_agent(): Process test requests through automation agent

Dependencies:
    - app.core.security.verify_token: Imported but not applied to /test_agent
    - fastapi: API framework and HTTP exception handling
    - langchain_core: ToolMessage handling to extract the agent's structured tool output
    - app.schemas.api: TestAgentRequest validation model
    - app.agent_infrastructure.agents.etown_agent: etown_automation_agent — the LangGraph automation agent invoked per request
    - app.agent_infrastructure.tools.testing_context_manager: set_test_context — seeds the Flutter UI state the agent's tools read

Updates:
    23-Feb-2026: Enhanced documentation with automation testing features
    19-Aug-2026: /test_agent requires the bearer token.
    20-Aug-2026: Reverted the 19-Aug bearer-token requirement on /test_agent.
    26-Aug-2026: Silent exception handlers now logged with tracebacks; vague log messages rewritten.

Notes:
    Designed for Flutter mobile application UI testing automation.
    Uses specialized automation agent for intelligent UI interaction.
    Supports complex test scenarios including authentication workflows.
"""
import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import ToolMessage
from app.core.security import verify_token
from app.schemas.api import TestAgentRequest
from app.agent_infrastructure.agents.etown_agent import etown_automation_agent
from app.agent_infrastructure.tools.testing_context_manager import set_test_context

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/test_agent")
async def run_test_agent(request: TestAgentRequest):
    """Handle POST /api/testing/test_agent: run the automation agent on a query and return its last tool output as JSON."""
    try:
        logger.info(
            "test_agent: invoked | query=%r flutter_context=%s",
            request.user_query, request.flutter_message,
        )
        request.flutter_message["user_query"] = request.user_query
        set_test_context(request.flutter_message)

        state = {
            "messages": [
                {"role": "user", "content": request.user_query}
            ]
        }
        
        config = {"configurable": {"thread_id": "test_thread_1"}}
        
        result = await etown_automation_agent.ainvoke(state, config=config)
        
        tool_output_content = None
        
        for message in reversed(result["messages"]):
            if isinstance(message, ToolMessage):
                tool_output_content = message.content
                break
        
        if not tool_output_content:
            logger.warning("No ToolMessage found in agent execution.")
            last_msg = result["messages"][-1]
            return {
                "Status": "error", 
                "Message": "Tool was not called.", 
                "AgentResponse": last_msg.content
            }

        try:
             if isinstance(tool_output_content, str):
                 json_response = json.loads(tool_output_content)
             else:
                 json_response = tool_output_content
                 
             return json_response
             
        except json.JSONDecodeError:
            logger.error(f"Tool output was not valid JSON: {tool_output_content}", exc_info=True)
            return {"Status": "error", "Message": "Invalid JSON from tool", "Raw": tool_output_content}

    except Exception as e:
        logger.error(f"Error in test_agent: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))