import asyncio
import os
import time
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk

load_dotenv()
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL")
TEST_MODEL = os.environ.get("PAGEINDEX_MODEL", "gpt-4o")
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

    tools = await client.get_tools()
    print(f"Available tools: {[t.name for t in tools]}\n")

    llm_kwargs = dict(model=TEST_MODEL, api_key=OPENAI_API_KEY, streaming=True)
    if OPENAI_BASE_URL:
        llm_kwargs["base_url"] = OPENAI_BASE_URL
    llm = ChatOpenAI(**llm_kwargs)
    agent = create_agent(llm, tools)

    query = "I want an expert GenAI Engineer. What are the technical questions I can ask Srividya for her to qualify here?? Give me ideal answers that I should be expecting from her??"
    print(f"Query: {query}")
    print(f"Sending at: {time.strftime('%H:%M:%S')}\n")

    messages = [
        {"role": "system", "content": (
            "You are a helpful document assistant. Always use the available tools to search "
            "before answering. When tools return results, present the information directly "
            "and confidently. Names in queries may be partial or approximate — treat close "
            "matches (e.g. surname matches) as the intended result and present the findings "
            "without hedging or disclaimers about name mismatches."
        )},
        {"role": "user", "content": query},
    ]

    t0 = time.perf_counter()
    ttft = None           # time to first token of final answer
    stream_start = None   # time streaming began (first chunk of any kind)
    call_num = 0
    token_count = 0
    final_answer = ""
    generating_answer = False

    print("=" * 60)
    print("STREAMING TIMELINE")
    print("=" * 60)

    async for event in agent.astream_events(
        {"messages": messages}, version="v2"
    ):
        kind = event["event"]
        elapsed = time.perf_counter() - t0

        # Track first chunk of any kind
        if stream_start is None and kind.startswith("on_"):
            stream_start = elapsed

        # Tool call starts
        if kind == "on_tool_start":
            call_num += 1
            name = event.get("name", "?")
            inputs = event.get("data", {}).get("input", {})
            args_summary = ", ".join(f"{k}={v!r}" for k, v in inputs.items()) if isinstance(inputs, dict) else str(inputs)
            print(f"\n[{elapsed:7.2f}s] TOOL CALL #{call_num}: {name}({args_summary})")

        # Tool call ends
        elif kind == "on_tool_end":
            output = event.get("data", {}).get("output", "")
            content = output.content if hasattr(output, "content") else str(output)
            preview = content[:150] if isinstance(content, str) else str(content)[:150]
            print(f"[{elapsed:7.2f}s] TOOL RESULT #{call_num} ({len(content)} chars): {preview}...")

        # LLM streaming tokens — final answer
        elif kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk and isinstance(chunk, AIMessageChunk):
                text = chunk.content or ""
                # Only count as final answer tokens if no tool calls in this chunk
                if text and not chunk.tool_call_chunks:
                    if ttft is None:
                        ttft = elapsed
                        generating_answer = True
                        print(f"\n[{elapsed:7.2f}s] FIRST TOKEN of final answer")
                        print("-" * 60)
                    if generating_answer:
                        print(text, end="", flush=True)
                        token_count += 1
                        final_answer += text

    total = time.perf_counter() - t0

    print("\n")
    print("=" * 60)
    print("TIMING SUMMARY")
    print("=" * 60)
    print(f"  Stream start:          {stream_start:.2f}s" if stream_start else "  Stream start:          N/A")
    print(f"  Time to first token:   {ttft:.2f}s" if ttft else "  Time to first token:   N/A")
    print(f"  Total response time:   {total:.2f}s")
    print(f"  Streaming duration:    {(total - ttft):.2f}s" if ttft else "  Streaming duration:    N/A")
    print(f"  Tokens streamed:       {token_count}")
    if ttft and token_count > 1:
        streaming_dur = total - ttft
        print(f"  Avg token interval:    {(streaming_dur / token_count * 1000):.1f}ms")
    print(f"  Tool calls:            {call_num}")

asyncio.run(main())
