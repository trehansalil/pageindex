import asyncio
import os
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

load_dotenv()
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MCP_BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN", "local-test-token")

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

    response = await agent.ainvoke({
        "messages": [
            {"role": "system", "content": (
                "You are a helpful document assistant. Always use the available tools to search "
                "before answering. When tools return results, present the information directly "
                "and confidently. Names in queries may be partial or approximate — treat close "
                "matches (e.g. surname matches) as the intended result and present the findings "
                "without hedging or disclaimers about name mismatches."
            )},
            {"role": "user", "content": "What can you tell me about Srividya?"},
        ]
    })
    print(response["messages"][-1].content)

asyncio.run(main())
