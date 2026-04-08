import asyncio
import os
import time
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, ToolMessage

load_dotenv()
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MCP_BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN", "local-test-token")


def log_tool_calls(response):
    """Print a timeline of every tool call and its duration."""
    messages = response["messages"]
    # Collect tool call start times from AIMessages and results from ToolMessages
    pending_calls = {}  # tool_call_id -> (tool_name, args, index)
    print("\n" + "=" * 60)
    print("TOOL CALL LOG")
    print("=" * 60)
    call_num = 0
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                call_num += 1
                pending_calls[tc["id"]] = (tc["name"], tc["args"], call_num)
                args_summary = ", ".join(f"{k}={v!r}" for k, v in tc["args"].items())
                print(f"\n[{call_num}] CALL: {tc['name']}({args_summary})")
        elif isinstance(msg, ToolMessage):
            info = pending_calls.get(msg.tool_call_id)
            label = f"[{info[2]}]" if info else "[?]"
            content_preview = msg.content[:200] if isinstance(msg.content, str) else str(msg.content)[:200]
            print(f"{label} RESULT ({len(msg.content)} chars): {content_preview}...")
    print("\n" + "=" * 60)


async def main():
    client = MultiServerMCPClient({
        "pageindex": {
            "transport": "streamable_http",
            "url": "https://pageindex.aiwithsalil.work/mcp",
            "headers": {
                "Authorization": f"Bearer {MCP_BEARER_TOKEN}"
            }
        }
    })

    tools = await client.get_tools()    # Discovers all tools from MCP server
    print(f"Available tools: {[t.name for t in tools]}\n")

    llm = ChatOpenAI(model="gpt-4o", api_key=OPENAI_API_KEY)
    agent = create_agent(llm, tools)

    query = "I want an expert GenAI Engineer. What are the questions I can ask Srividya for her to qualify here??"
    print(f"Query: {query}")
    print(f"Sending at: {time.strftime('%H:%M:%S')}\n")

    t0 = time.perf_counter()
    response = await agent.ainvoke({
        "messages": [
            {"role": "system", "content": (
                "You are a helpful document assistant. Always use the available tools to search "
                "before answering. When tools return results, present the information directly "
                "and confidently. Names in queries may be partial or approximate — treat close "
                "matches (e.g. surname matches) as the intended result and present the findings "
                "without hedging or disclaimers about name mismatches."
            )},
            {"role": "user", "content": query},
        ]
    })
    elapsed = time.perf_counter() - t0

    log_tool_calls(response)
    print(f"\nTotal time: {elapsed:.2f}s")
    print(f"\nFINAL ANSWER:\n{response['messages'][-1].content}")

asyncio.run(main())
