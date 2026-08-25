"""Live DeepSeek Chat Completions and an explicitly scripted offline replay."""
import json
import re
import urllib.error
import urllib.request


class ProviderError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DeepSeek:
    mode = "live_model"

    def __init__(self, key, model="deepseek-flash", transport=None):
        if not key:
            raise ValueError("DEEPSEEK_API_KEY 未设置")
        self.key, self.model = key, model
        self.transport = transport or self._post

    def _post(self, payload, timeout):
        request = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            json.dumps(payload).encode(),
            {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            method="POST",
        )
        # Never follow a redirect carrying a provider credential.
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ProviderError("response_too_large")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"http_{exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise ProviderError("transport_or_json_error") from None

    def complete(self, messages, tools, timeout):
        payload = {
            "model": self.model, "messages": messages, "tools": tools,
            "tool_choice": "auto", "stream": False, "max_tokens": 1200,
            "thinking": {"type": "disabled"},
        }
        data = self.transport(payload, timeout)
        try:
            choice = data["choices"][0]
            if choice["finish_reason"] not in ("stop", "tool_calls"):
                raise ProviderError("incomplete_completion")
            # Preserve the full assistant message, including reasoning_content if supplied.
            return choice["message"], data.get("usage", {})
        except (KeyError, IndexError, TypeError):
            raise ProviderError("invalid_provider_response") from None


class Replay:
    """Fixed policy for plumbing checks; this is neither a model nor a success benchmark."""
    mode = "scripted_replay"

    def complete(self, messages, tools, timeout):
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not results:
            text = next(m["content"] for m in messages if m["role"] == "user")
            if {t["function"]["name"] for t in tools} == {"search_knowledge"}:
                return {"role":"assistant","content":None,"tool_calls":[{
                    "id":"replay-search-1","type":"function","function":{
                        "name":"search_knowledge","arguments":json.dumps({"query":text},ensure_ascii=False)}}]}, {}
            refs = re.findall(r"\b[OP][0-9]{4,12}\b", text)
            if refs:
                return {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "replay-query-1", "type": "function", "function": {
                        "name": "lookup_order", "arguments": json.dumps({"reference": refs[0]})}}]}, {}
        facts = [e for r in results for e in r.get("evidence", [])]
        answer = {"facts": [{"evidence_id": e["id"], "quote": e["quote"]} for e in facts],
                  "next_step": "handoff" if results else "ask_reference"}
        return {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}, {}
