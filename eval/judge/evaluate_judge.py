"""LLM Judge Evaluation Module.

Evaluates LLM Judge accuracy, Macro-F1, per-class Precision/Recall,
and Confusion Matrix against ground-truth labels with strict cache bypassing.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import defaultdict
from src.judge.llm_judge import LLMJudge
from src.parsers.requirement_parser import RequirementChunk
from src.parsers.gherkin_parser import ScenarioChunk


class JudgeEvaluator:
    """Evaluates LLM Judge classification and coverage scoring against gold labels."""

    CLASSES = ["COVERED", "PARTIALLY_COVERED", "NOT_COVERED"]

    def __init__(
        self,
        judge: LLMJudge,
        gold_labels_path: Optional[str or Path] = None,
        gold_dataset_path: Optional[str or Path] = None,
    ):
        self.judge = judge
        target_path = gold_dataset_path or gold_labels_path
        self.gold_path = Path(target_path or Path(__file__).parent / "labels.json")

    def load_gold_labels(self) -> List[Dict[str, Any]]:
        with open(self.gold_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _normalize_status(status_str: str) -> str:
        s = (status_str or "").strip().upper()
        if "PARTIAL" in s:
            return "PARTIALLY_COVERED"
        if "NOT" in s or "UNCOVERED" in s or "NONE" in s:
            return "NOT_COVERED"
        if "COVERED" in s or "FULL" in s:
            return "COVERED"
        return "NOT_COVERED"

    def evaluate(self, bypass_cache: bool = True) -> Dict[str, Any]:
        gold_cases = self.load_gold_labels()
        if not gold_cases:
            return {
                "total_cases": 0,
                "accuracy": 0.0,
                "macro_f1": 0.0,
                "confusion_matrix": {},
                "case_details": [],
            }

        confusion_matrix = {g: {p: 0 for p in self.CLASSES} for g in self.CLASSES}
        case_details = []
        correct_count = 0
        mae_errors = []
        total_cases = len(gold_cases)
        for idx, case in enumerate(gold_cases, start=1):
            case_id = case.get("case_id") or case.get("requirement_id", "JUDGE-TEST")
            if idx % 5 == 0 or idx == 1 or idx == total_cases:
                print(f"   [Judge {idx}/{total_cases}] Evaluating case: {case_id}...", flush=True)
            r_dict = case["requirement"]
            req = RequirementChunk(
                req_id=r_dict.get("id", "REQ-1"),
                title=r_dict.get("title", ""),
                category=r_dict.get("category", "Functional"),
                description=r_dict.get("description", ""),
                acceptance_criteria=r_dict.get("acceptance_criteria", []),
                business_rules=r_dict.get("business_rules", []),
                source_file=r_dict.get("brd_path") or r_dict.get("source_file") or "eval_doc.md",
                line_number=1,
                full_text=f"{r_dict.get('title', '')}\n{r_dict.get('description', '')}",
            )

            # Build ScenarioChunk candidates
            candidates = []
            for sc_dict in case.get("candidate_scenarios", []):
                sc = ScenarioChunk(
                    scenario_id=f"eval_sc_{sc_dict['scenario_name'][:10]}",
                    repository_id="eval_repo",
                    file_path=sc_dict.get("file_path", "test.feature"),
                    feature_name="Eval Feature",
                    scenario_name=sc_dict.get("scenario_name", "Scenario"),
                    canonical_text=sc_dict.get("raw_gherkin", ""),
                    raw_gherkin=sc_dict.get("raw_gherkin", ""),
                )
                candidates.append((sc, 1.0, {}))

            # Evaluate with LLM Judge (enforcing cache bypass)
            evaluation = self.judge.judge_requirement(
                requirement=req,
                candidates=candidates,
                repo_id="eval_repo",
                bypass_cache=bypass_cache,
            )

            pred_status = self._normalize_status(evaluation.overall_classification)
            gold_status = self._normalize_status(case.get("gold_status", "NOT_COVERED"))

            is_correct = (pred_status == gold_status)
            if is_correct:
                correct_count += 1

            confusion_matrix[gold_status][pred_status] += 1

            gold_pct = float(case.get("gold_match_percentage", 0.0))
            pred_pct = float(evaluation.match_percentage)
            mae_errors.append(abs(gold_pct - pred_pct))

            case_details.append({
                "requirement_id": case_id,
                "title": req.title,
                "gold_status": gold_status,
                "predicted_status": pred_status,
                "gold_match_pct": gold_pct,
                "predicted_match_pct": pred_pct,
                "is_correct": is_correct,
                "evaluation_reasoning": evaluation.reasoning[:200] if evaluation.reasoning else "",
            })

        n = len(gold_cases)
        accuracy = correct_count / n if n > 0 else 0.0
        mean_mae = sum(mae_errors) / n if n > 0 else 0.0

        # Calculate per-class Precision, Recall, F1
        per_class = {}
        f1_scores = []
        for cls in self.CLASSES:
            tp = confusion_matrix[cls][cls]
            fp = sum(confusion_matrix[other][cls] for other in self.CLASSES if other != cls)
            fn = sum(confusion_matrix[cls][other] for other in self.CLASSES if other != cls)

            prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0

            per_class[cls] = {
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1": round(f1, 4),
                "support": tp + fn,
            }
            if (tp + fn) > 0:
                f1_scores.append(f1)

        macro_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0

        return {
            "total_cases": n,
            "accuracy": round(accuracy, 4),
            "macro_f1": round(macro_f1, 4),
            "mean_absolute_error_pct": round(mean_mae, 2),
            "per_class": per_class,
            "confusion_matrix": confusion_matrix,
            "case_details": case_details,
        }
