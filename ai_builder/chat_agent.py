from typing import Any, Dict, List
from ai_builder.ai_client import AIClient
from ai_builder.generator import WorkflowGenerator

_SYSTEM_PROMPT = """\
You are an expert workflow automation engineer assistant. 
Your goal is to build a complete JSON workflow for the user, but you must NOT guess missing information.
If the user's intent is too vague, or lacks details (like specific integrations, email addresses, channel names, conditions, or data mappings), you MUST ask a clarifying question.
If you have enough information to build a robust, scalable workflow (including fallbacks, HITL approvals, etc.), you will return the workflow JSON.

OUTPUT FORMAT (You must return strict JSON):
Option 1 - Ask a Question:
{
  "type": "question",
  "content": "Your clarifying question here."
}

Option 2 - Generate Workflow:
{
  "type": "workflow",
  "workflow_json": { 
      "name": "...",
      "description": "...",
      "nodes": [ ... ] 
  }
}

Important for Workflow JSON:
- Must have "nodes" (list) and "edges" (list).
- Auto-add fallback/error handling (e.g. email fallback if SMS fails) if appropriate.
- Node types available: `action`, `condition`, `ai`, `notification`, `webhook`, `human_approval`, `loop`, `transform`.
- For Human-in-the-Loop (HITL): use node type `human_approval`.
- For Dynamic Personalization (altering text per user): use node type `ai`.
- For Chat with Data / Segmentation: use node type `action` or `webhook` to fetch data, followed by a `loop`.
"""

class ChatAgent:
    def __init__(self, client: AIClient):
        self._client = client
        self._generator = WorkflowGenerator(client)

    def chat(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        # Perform RAG retrieval based on the latest user message
        from ai_builder.rag import search_context
        
        last_user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        rag_context_docs = search_context(last_user_msg) if last_user_msg else []
        
        rag_prompt = ""
        if rag_context_docs:
            rag_prompt = "\n\nCRITICAL BRAND GUIDELINES & PAST KNOWLEDGE:\n" + "\n".join(rag_context_docs)
            
        system_content = _SYSTEM_PROMPT + rag_prompt
        
        api_messages = [{"role": "system", "content": system_content}]
        api_messages.extend(messages)
        
        res = self._client.chat_json(api_messages, max_tokens=4000)
        
        if res.get("type") == "workflow" and "workflow_json" in res:
            wf = self._generator._post_process(res["workflow_json"])
            return {
                "type": "workflow",
                "workflow_json": wf,
                "node_count": len(wf.get("nodes", []))
            }
            
        return {
            "type": "question",
            "content": res.get("content", "Could you provide more details?")
        }
