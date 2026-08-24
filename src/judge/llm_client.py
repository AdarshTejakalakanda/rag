"""Multi-provider LLM client supporting Gemini, OpenAI, Anthropic, Ollama, and Mock fallback.

Reads API keys and configuration directly from environment variables (.env).
"""

import os
import json
import re
import time
from pathlib import Path
from typing import Dict, Any, Optional
import urllib.request
import urllib.error
from dotenv import load_dotenv
from src.config import JudgeConfig

# Ensure .env is loaded
load_dotenv()


SUPPORTED_GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

INVALID_GEMINI_MODELS = set()


class LLMClient:
    """Dispatches completion requests to Gemini, OpenAI, Anthropic, Ollama, or Mock fallback."""

    def __init__(self, config: Optional[JudgeConfig] = None):
        self.config = config or JudgeConfig()
        self.provider = self.config.provider.lower()

    def generate_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Generates a structured JSON response from the configured LLM provider."""
        provider = self._resolve_provider()
        if provider == "gemini":
            return self._call_gemini(system_prompt, user_prompt)
        elif provider in ("openai", "azure", "deepseek", "groq"):
            return self._call_openai(system_prompt, user_prompt)
        elif provider == "anthropic":
            return self._call_anthropic(system_prompt, user_prompt)
        elif provider == "ollama":
            return self._call_ollama(system_prompt, user_prompt)
        else:
            return self._call_mock(system_prompt, user_prompt)

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        """Generates conversational text response from the configured LLM provider."""
        provider = self._resolve_provider()
        if provider == "gemini":
            return self._call_gemini_text(system_prompt, user_prompt)
        elif provider in ("openai", "azure", "deepseek", "groq"):
            return self._call_openai_text(system_prompt, user_prompt)
        elif provider == "anthropic":
            return self._call_anthropic_text(system_prompt, user_prompt)
        elif provider == "ollama":
            return self._call_ollama_text(system_prompt, user_prompt)
        else:
            return self._call_mock_text(system_prompt, user_prompt)

    def _resolve_provider(self) -> str:
        if self.provider and self.provider != "auto":
            return self.provider
        if os.getenv("GEMINI_API_KEY"):
            return "gemini"
        elif os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("GROQ_API_KEY"):
            return "openai"
        elif os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        elif os.getenv("OLLAMA_BASE_URL"):
            return "ollama"
        return "mock"

    def _clean_and_parse_json(self, raw_text: str) -> Dict[str, Any]:
        """Strips markdown backticks and parses JSON."""
        cleaned = raw_text.strip()
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if m:
            cleaned = m.group(1).strip()
        try:
            return json.loads(cleaned)
        except Exception:
            m_brace = re.search(r"(\{[\s\S]*\})", cleaned)
            if m_brace:
                try:
                    return json.loads(m_brace.group(1))
                except Exception:
                    pass
            return {
                "evaluations": [],
                "overall_summary": {
                    "primary_citation": None,
                    "covered_criteria": [],
                    "missing_gaps": ["Unparseable LLM output"],
                    "suggested_tests": []
                }
            }

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv(self.config.gemini.api_key_env)
        if not api_key:
            return self._call_mock(system_prompt, user_prompt)

        primary_model = os.getenv("GEMINI_MODEL") or self.config.gemini.model
        models_to_try = [primary_model] + [m for m in SUPPORTED_GEMINI_MODELS if m != primary_model and m not in INVALID_GEMINI_MODELS]

        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            for m in models_to_try:
                if m in INVALID_GEMINI_MODELS:
                    continue
                try:
                    response = client.models.generate_content(
                        model=m,
                        contents=f"{system_prompt}\n\n{user_prompt}",
                    )
                    if response and response.text:
                        return self._clean_and_parse_json(response.text)
                except Exception as model_err:
                    err_str = str(model_err)
                    if "404" in err_str:
                        INVALID_GEMINI_MODELS.add(m)
                        continue
                    if "429" in err_str or "503" in err_str:
                        time.sleep(1.0)
                        continue
                    break
        except Exception as e:
            print(f"[LLMClient] Gemini notice ({e}). Operating in local mock fallback mode.")
        return self._call_mock(system_prompt, user_prompt)

    def _call_openai(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        api_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("GROQ_API_KEY")
            or os.getenv(self.config.openai.api_key_env)
        )
        if not api_key:
            return self._call_mock(system_prompt, user_prompt)

        model_name = os.getenv("OPENAI_MODEL") or self.config.openai.model
        base_url = os.getenv("OPENAI_BASE_URL") or self.config.openai.base_url

        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=model_name,
                temperature=self.config.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"} if ("gpt" in model_name or "deepseek" in model_name) else None,
            )
            return self._clean_and_parse_json(response.choices[0].message.content)
        except Exception as e:
            print(f"[LLMClient] OpenAI notice ({e}). Operating in local mock fallback mode.")
            return self._call_mock(system_prompt, user_prompt)

    def _call_anthropic(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return self._call_mock(system_prompt, user_prompt)

        model_name = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=model_name,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=self.config.temperature,
            )
            return self._clean_and_parse_json(response.content[0].text)
        except Exception as e:
            print(f"[LLMClient] Anthropic notice ({e}). Operating in local mock fallback mode.")
            return self._call_mock(system_prompt, user_prompt)

    def _call_ollama(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        base_url = (os.getenv("OLLAMA_BASE_URL") or self.config.ollama.base_url).rstrip("/")
        model_name = os.getenv("OLLAMA_MODEL") or self.config.ollama.model

        payload = {
            "model": model_name,
            "system": system_prompt,
            "prompt": user_prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": self.config.temperature},
        }

        try:
            req = urllib.request.Request(
                f"{base_url}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return self._clean_and_parse_json(data.get("response", "{}"))
        except Exception as e:
            print(f"[LLMClient] Ollama notice ({e}). Operating in local mock fallback mode.")
            return self._call_mock(system_prompt, user_prompt)

    def _call_gemini_text(self, system_prompt: str, user_prompt: str) -> str:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv(self.config.gemini.api_key_env)
        if not api_key:
            return self._call_mock_text(system_prompt, user_prompt)
        primary_model = os.getenv("GEMINI_MODEL") or self.config.gemini.model
        models_to_try = [primary_model] + [m for m in SUPPORTED_GEMINI_MODELS if m != primary_model]
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            for m in models_to_try:
                try:
                    response = client.models.generate_content(
                        model=m,
                        contents=f"{system_prompt}\n\n{user_prompt}",
                    )
                    if response and response.text:
                        return response.text.strip()
                except Exception as model_err:
                    err_str = str(model_err)
                    if "429" in err_str or "503" in err_str or "404" in err_str:
                        continue
                    break
        except Exception as e:
            print(f"[LLMClient] Notice: Live Gemini text generation unavailable ({e}). Serving via local evaluation.")
        return self._call_mock_text(system_prompt, user_prompt)

    def _call_openai_text(self, system_prompt: str, user_prompt: str) -> str:
        api_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("GROQ_API_KEY")
            or os.getenv(self.config.openai.api_key_env)
        )
        if not api_key:
            return self._call_mock_text(system_prompt, user_prompt)
        model_name = os.getenv("OPENAI_MODEL") or self.config.openai.model
        base_url = os.getenv("OPENAI_BASE_URL") or self.config.openai.base_url
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=model_name,
                temperature=self.config.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            if response.choices and response.choices[0].message.content:
                return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[LLMClient] OpenAI text error: {e}")
        return self._call_mock_text(system_prompt, user_prompt)

    def _call_anthropic_text(self, system_prompt: str, user_prompt: str) -> str:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return self._call_mock_text(system_prompt, user_prompt)
        model_name = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=model_name,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=self.config.temperature,
            )
            if response.content and response.content[0].text:
                return response.content[0].text.strip()
        except Exception as e:
            print(f"[LLMClient] Anthropic text error: {e}")
        return self._call_mock_text(system_prompt, user_prompt)

    def _call_ollama_text(self, system_prompt: str, user_prompt: str) -> str:
        base_url = (os.getenv("OLLAMA_BASE_URL") or self.config.ollama.base_url).rstrip("/")
        model_name = os.getenv("OLLAMA_MODEL") or self.config.ollama.model
        payload = {
            "model": model_name,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {"temperature": self.config.temperature},
        }
        try:
            req = urllib.request.Request(
                f"{base_url}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("response", "").strip()
        except Exception as e:
            print(f"[LLMClient] Ollama text error: {e}")
        return self._call_mock_text(system_prompt, user_prompt)

    def _call_mock_text(self, system_prompt: str, user_prompt: str) -> str:
        eval_res = self._call_mock(system_prompt, user_prompt)
        evals = eval_res.get("evaluations", [])
        if not evals:
            return "No automated test scenarios were found matching your inquiry in this repository."
        top = max(evals, key=lambda x: x.get("match_percentage", 0))
        lines = [
            f"Based on repository analysis, found **{len(evals)} relevant scenario(s)**.",
            f"\n### Top Matched Test:\n- **Feature:** `{top.get('feature_name')}`\n- **Scenario:** **{top.get('scenario_name')}** (`{Path(top.get('file_path', '')).name}:{top.get('line_number', 1)}`)\n- **Status:** `{top.get('status')}` ({top.get('match_percentage')}% match)",
            f"\n**Reasoning:** {top.get('reasoning')}",
        ]
        if top.get("missing_gaps"):
            lines.append(f"\n**Identified Test Gaps:**\n" + "\n".join(f"- {g}" for g in top["missing_gaps"]))
        return "\n".join(lines)

    def _call_mock(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Deterministic heuristic evaluation mode for zero-cost testing and airgapped environments."""
        candidates = []
        # Support both header formats
        pattern1 = r"---\s*\[Candidate #\d+\]\s*ID:\s*([^\s|]+)\s*\|\s*Feature:\s*([^|]+)\|\s*File:\s*([^:]+):(\d+)\s*---\s*Canonical Representation:\s*([\s\S]*?)\s*Raw Gherkin Evidence:\s*([\s\S]*?)(?=(?:---\s*\[Candidate|\Z))"
        matches1 = list(re.finditer(pattern1, user_prompt))

        if matches1:
            for m in matches1:
                raw_g = m.group(6).strip()
                s_name_m = re.search(r"Scenario(?: Outline)?:\s*([^\n]+)", raw_g)
                s_name = s_name_m.group(1).strip() if s_name_m else "Scenario"
                candidates.append({
                    "scenario_id": m.group(1).strip(),
                    "feature_name": m.group(2).strip(),
                    "file_path": m.group(3).strip(),
                    "line_number": int(m.group(4).strip()),
                    "scenario_name": s_name,
                    "raw_gherkin": raw_g,
                })
        else:
            pattern_sid = (
                r"---\s*\[Candidate #\d+\]\s*Scenario ID:\s*([^\n]+?)\s*---\s*"
                r"Feature Name:\s*([^\n]+)\s*"
                r"Scenario Name:\s*([^\n]+)\s*"
                r"File Path:\s*([^\n]+)\s*"
                r"Location:\s*Lines\s*(\d+)[^\n]*\s*"
                r"(?:Canonical Steps Representation:\s*([\s\S]*?))?"
                r"(?:Raw Gherkin Evidence:\s*([\s\S]*?))?(?=(?:---\s*\[Candidate|\Z))"
            )
            matches_sid = list(re.finditer(pattern_sid, user_prompt))
            if matches_sid:
                for m in matches_sid:
                    raw_g = (m.group(7) or m.group(6) or "").strip()
                    candidates.append({
                        "scenario_id": m.group(1).strip(),
                        "feature_name": m.group(2).strip(),
                        "scenario_name": m.group(3).strip(),
                        "file_path": m.group(4).strip(),
                        "line_number": int(m.group(5).strip()),
                        "raw_gherkin": raw_g,
                    })
            else:
                pattern2 = r"--- Candidate Scenario:\s*([a-zA-Z0-9_\-]+)\s*---\s*File:\s*([^\n]+)\s*Feature:\s*([^\n]+)\s*Scenario:\s*([^\n]+)\s*Line:\s*(\d+)\s*Raw Gherkin:\s*([\s\S]*?)(?=(?:--- Candidate Scenario:|\Z))"
                matches2 = list(re.finditer(pattern2, user_prompt))
                if matches2:
                    for m in matches2:
                        candidates.append({
                            "scenario_id": m.group(1).strip(),
                            "file_path": m.group(2).strip(),
                            "feature_name": m.group(3).strip(),
                            "scenario_name": m.group(4).strip(),
                            "line_number": int(m.group(5).strip()),
                            "raw_gherkin": m.group(6).strip(),
                        })
                else:
                    pattern3 = r"\[\d+\]\s*Feature:\s*([^\n]+)\n\s*Scenario:\s*([^(]+)\s*\(([^:]+):(\d+)\)\n\s*Steps:\n([\s\S]*?)(?=(?:\[\d+\]|\n\nUSER|\Z))"
                    for m in re.finditer(pattern3, user_prompt):
                        candidates.append({
                            "scenario_id": f"chat_sc_{len(candidates)}",
                            "file_path": m.group(3).strip(),
                            "feature_name": m.group(1).strip(),
                            "scenario_name": m.group(2).strip(),
                            "line_number": int(m.group(4).strip()),
                            "raw_gherkin": m.group(5).strip(),
                        })

        # Extract clean query text
        q_match = re.search(r"(?:USER QUESTION / REQUIREMENT|BUSINESS REQUIREMENT|Business Requirement|REQUIREMENT):\s*(?:ID:[^\n]+\s*Title:\s*([^\n]+)|([^\n]+))", user_prompt, re.IGNORECASE)
        q_text = ""
        if q_match:
            q_text = (q_match.group(1) or q_match.group(2) or "").strip()
        elif "USER QUESTION / REQUIREMENT:" in user_prompt:
            q_text = user_prompt.split("USER QUESTION / REQUIREMENT:")[-1].split("Please provide")[0].strip()

        if not q_text:
            lines = [l.strip() for l in user_prompt.strip().split("\n") if l.strip()]
            q_text = lines[-1] if lines else user_prompt

        ac_block = re.search(
            r"Acceptance Criteria:\s*([\s\S]*?)(?:Business Rules:|\={10,}|CANDIDATE)",
            user_prompt,
            re.IGNORECASE,
        )
        ac_items = []
        if ac_block:
            for line in ac_block.group(1).splitlines():
                cleaned = re.sub(r"^[-*•\d.\s]+", "", line).strip()
                if cleaned and cleaned.lower() not in ("none specified", "none"):
                    ac_items.append(cleaned)

        stopwords = {"show", "me", "the", "a", "an", "is", "in", "of", "and", "for", "to", "coverage", "what", "are", "about", "how", "can", "we", "verify", "test", "tests"}
        all_q_tokens = set(re.findall(r"\b[a-zA-Z0-9]+\b", q_text.lower()))
        q_tokens = all_q_tokens - stopwords or all_q_tokens

        evaluations = []
        for cand in candidates:
            c_text = f"{cand['feature_name']} {cand['scenario_name']} {cand['raw_gherkin']}"
            c_tokens = set(re.findall(r"\b[a-zA-Z0-9]+\b", c_text.lower()))

            if ac_items:
                covered_criteria = []
                for ac in ac_items:
                    ac_tokens = set(re.findall(r"\b[a-zA-Z0-9]+\b", ac.lower())) - stopwords
                    ac_tokens = ac_tokens or set(re.findall(r"\b[a-zA-Z0-9]+\b", ac.lower()))
                    if ac_tokens and len(ac_tokens.intersection(c_tokens)) >= max(1, len(ac_tokens) // 3):
                        covered_criteria.append(ac)
                match_pct = int(round((len(covered_criteria) / len(ac_items)) * 100))
                missing_gaps = [ac for ac in ac_items if ac not in covered_criteria]
            else:
                overlap = q_tokens.intersection(c_tokens)
                overlap_ratio = len(overlap) / max(len(q_tokens), 1)
                if overlap_ratio >= 0.5 or (len(overlap) >= 2 and overlap_ratio >= 0.3):
                    match_pct = 100
                    covered_criteria = ["Core business flow verified"]
                    missing_gaps = []
                elif overlap_ratio >= 0.2 or len(overlap) >= 1:
                    match_pct = 50
                    covered_criteria = ["Partial workflow automated"]
                    missing_gaps = ["Boundary conditions and edge validations missing"]
                else:
                    match_pct = 0
                    covered_criteria = []
                    missing_gaps = ["No automated coverage in this scenario"]

            if match_pct >= 100:
                status = "FULLY_COVERED"
            elif match_pct > 0:
                status = "PARTIALLY_COVERED"
            else:
                status = "NOT_RELEVANT"

            if status == "FULLY_COVERED":
                reason = "Scenario independently evidences all supplied acceptance criteria."
            elif status == "PARTIALLY_COVERED":
                reason = f"Scenario independently covers: {', '.join(covered_criteria) or 'partial flow'}."
            else:
                reason = "Scenario tests unrelated domain logic."

            evaluations.append({
                "scenario_id": cand["scenario_id"],
                "status": status,
                "match_percentage": match_pct,
                "reasoning": reason,
                "evidence": [l.strip() for l in cand["raw_gherkin"].strip().split("\n")[:3] if l.strip()],
                "covered_criteria": covered_criteria,
                "missing_gaps": missing_gaps,
            })

        union_covered = []
        seen_union = set()
        coverage_map = []
        for ev in evaluations:
            contrib = []
            for item in ev.get("covered_criteria") or []:
                key = item.lower()
                if key not in seen_union:
                    seen_union.add(key)
                    union_covered.append(item)
                contrib.append(item)
            if contrib:
                cand = next((c for c in candidates if c["scenario_id"] == ev["scenario_id"]), {})
                coverage_map.append({
                    "scenario_id": ev["scenario_id"],
                    "file_path": cand.get("file_path", ""),
                    "covers": contrib,
                })

        if ac_items:
            union_pct = int(round((len(union_covered) / len(ac_items)) * 100)) if ac_items else 0
            missing_union = [ac for ac in ac_items if ac not in union_covered]
        else:
            missing_union = []
            for ev in evaluations:
                missing_union.extend(ev.get("missing_gaps") or [])
            total_bits = len(union_covered) + len(missing_union)
            union_pct = int(round((len(union_covered) / total_bits) * 100)) if total_bits else 0

        connecting = []
        for row in coverage_map:
            connecting.append(f"{row['file_path'] or row['scenario_id']} covers {'; '.join(row['covers'])}")
        connecting_narrative = (
            "Complementary files combine as: " + " | ".join(connecting)
            if connecting else "No complementary coverage across retrieved files."
        )

        summary = {
            "union_match_percentage": union_pct,
            "connecting_narrative": connecting_narrative,
            "coverage_map": coverage_map,
            "covered_criteria": union_covered,
            "missing_gaps": missing_union or (
                ["No automated Gherkin scenarios found in repository"] if not evaluations else []
            ),
            "suggested_test_intents": (
                [f"Add tests for remaining gaps: {', '.join(missing_union[:3])}"] if missing_union else []
            ),
        }

        return {
            "evaluations": evaluations,
            "overall_summary": summary,
        }
