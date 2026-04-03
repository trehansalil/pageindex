from .config import settings

async def _llm(prompt: str) -> str:
    client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    r = await client.chat.completions.create(
        model=settings.MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return r.choices[0].message.content.strip()


def _strip_text(nodes: list) -> list:
    """Return tree copy without 'text' fields (keeps search prompt small)."""
    result = []
    for n in nodes:
        copy = {k: v for k, v in n.items() if k != "text"}
        if copy.get("nodes"):
            copy["nodes"] = _strip_text(copy["nodes"])
        result.append(copy)
    return result


async def _rag(query: str, doc_names: list[str]) -> str:
    """Run PageIndex tree-search + answer-generation pipeline."""
    context_parts: list[str] = []

    for name in doc_names:
        tree_slim = _strip_text(documents[name])
        nm        = node_maps[name]

        search_prompt = (
            "You are given a question and a document tree.\n"
            "Each node has a node_id, title, and summary.\n"
            "Find all node_ids whose content likely answers the question.\n\n"
            f"Question: {query}\n"
            f"Document: {name}\n"
            f"Tree:\n{json.dumps(tree_slim, indent=2)}\n\n"
            'Reply ONLY in JSON: {"thinking": "<reasoning>", "node_list": ["id1", "id2"]}'
        )

        raw = await _llm(search_prompt)

        # Strip markdown code fences if the model adds them
        clean = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        clean = re.sub(r"\n?```$", "", clean).strip()

        try:
            ids = json.loads(clean).get("node_list", [])
        except Exception:
            ids = []

        text = "\n\n".join(
            nm[i]["text"] for i in ids if i in nm and "text" in nm[i]
        )
        if text:
            context_parts.append(f"=== {name} ===\n{text}")

    if not context_parts:
        return "No relevant content found for the query."

    answer_prompt = (
        "Answer the question based only on the context below.\n\n"
        f"Question: {query}\n\n"
        f"Context:\n{chr(10).join(context_parts)}"
    )
    return await _llm(answer_prompt)