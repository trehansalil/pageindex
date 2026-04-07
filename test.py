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
            "url": "http://pageindex.aiwithsalil.work/mcp",
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
        "messages": [{"role": "user", "content": "What can you tell me about Ravi Kumar's CV?"}]
    })
    print(response["messages"][-1].content)

asyncio.run(main())
