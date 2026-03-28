from __future__ import annotations

import json
from typing import Any

from models import LLMValidationIssue


PLAN_TOP_LEVEL_KEYS = {"steps"}
PLAN_STEP_KEYS = {
    "id",
    "type",
    "title",
    "description",
    "dependencies",
    "scope_hint",
    "expected_outcome",
    "phase",
    "risk_hint",
    "approval_hint",
}
VALID_STEP_TYPES = {"discovery", "execution", "verification"}
REVIEW_TOP_LEVEL_KEYS = {
    "verdict",
    "summary",
    "scope_ok",
    "overbuild_detected",
    "findings",
    "suggested_adjustments",
}
REVIEW_FINDING_KEYS = {"category", "message", "severity", "step_ids"}
VALID_REVIEW_VERDICTS = {"accept", "revise", "caution"}
VALID_REVIEW_SEVERITIES = {"low", "medium", "high"}

DECOMPOSE_TOP_LEVEL_KEYS = {"subtasks"}
DECOMPOSE_SUBTASK_KEYS = {"action", "description", "target_path", "command"}
VALID_DECOMPOSE_ACTIONS = {"create_file", "modify_file", "run_command", "analyze", "restore_snapshot", "unknown"}

REPLAN_TOP_LEVEL_KEYS = {"is_complete", "reasoning", "new_subtasks"}
COMPRESS_TOP_LEVEL_KEYS = {"summary"}

def parse_json_object(raw_text: str) -> tuple[dict[str, Any] | None, list[LLMValidationIssue]]:
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    if not stripped:
        return None, [LLMValidationIssue(code="empty_response", message="The model response was empty.")]
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as caught:
        return None, [
            LLMValidationIssue(
                code="invalid_json",
                message=f"Response was not valid JSON: {caught.msg}.",
                path=f"line {caught.lineno} col {caught.colno}",
            )
        ]
    if not isinstance(payload, dict):
        return None, [LLMValidationIssue(code="wrong_type", message="Response JSON must be an object.", path="$")]
    return payload, []


def validate_plan_payload(payload: dict[str, Any]) -> list[LLMValidationIssue]:
    issues: list[LLMValidationIssue] = []
    extra_top_level = set(payload.keys()) - PLAN_TOP_LEVEL_KEYS
    if extra_top_level:
        issues.append(LLMValidationIssue(code="extra_keys", message=f"Unexpected top-level keys: {sorted(extra_top_level)}.", path="$"))

    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        issues.append(LLMValidationIssue(code="missing_required_field", message="The 'steps' field must be a non-empty list.", path="steps"))
        return issues

    for index, step in enumerate(steps):
        path = f"steps[{index}]"
        if not isinstance(step, dict):
            issues.append(LLMValidationIssue(code="wrong_type", message="Each step must be an object.", path=path))
            continue
        extra_keys = set(step.keys()) - PLAN_STEP_KEYS
        if extra_keys:
            issues.append(LLMValidationIssue(code="extra_keys", message=f"Unexpected keys: {sorted(extra_keys)}.", path=path))
        for key in PLAN_STEP_KEYS:
            if key not in step:
                issues.append(LLMValidationIssue(code="missing_required_field", message=f"Missing required field '{key}'.", path=f"{path}.{key}"))
        if "type" in step and step["type"] not in VALID_STEP_TYPES:
            issues.append(LLMValidationIssue(code="wrong_type", message="Step type must be discovery, execution, or verification.", path=f"{path}.type"))
        for string_key in ["id", "title", "description", "scope_hint", "expected_outcome", "phase", "risk_hint", "approval_hint"]:
            if string_key in step and not isinstance(step[string_key], str):
                issues.append(LLMValidationIssue(code="wrong_type", message=f"Field '{string_key}' must be a string.", path=f"{path}.{string_key}"))
        dependencies = step.get("dependencies")
        if dependencies is not None:
            if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
                issues.append(LLMValidationIssue(code="wrong_type", message="Field 'dependencies' must be a list of strings.", path=f"{path}.dependencies"))
    return issues

def validate_compress_payload(payload: dict[str, Any]) -> list[LLMValidationIssue]:
    issues: list[LLMValidationIssue] = []
    extra_top_level = set(payload.keys()) - COMPRESS_TOP_LEVEL_KEYS
    if extra_top_level:
        issues.append(LLMValidationIssue(code="extra_keys", message=f"Unexpected top-level keys: {sorted(extra_top_level)}.", path="$"))

    if "summary" not in payload:
         issues.append(LLMValidationIssue(code="missing_required_field", message="Missing required field 'summary'.", path="summary"))
    elif not isinstance(payload["summary"], str):
         issues.append(LLMValidationIssue(code="wrong_type", message="Field 'summary' must be a string.", path="summary"))

    return issues

def validate_replan_payload(payload: dict[str, Any]) -> list[LLMValidationIssue]:
    issues: list[LLMValidationIssue] = []
    extra_top_level = set(payload.keys()) - REPLAN_TOP_LEVEL_KEYS
    if extra_top_level:
        issues.append(LLMValidationIssue(code="extra_keys", message=f"Unexpected top-level keys: {sorted(extra_top_level)}.", path="$"))

    for key in ["is_complete", "reasoning", "new_subtasks"]:
        if key not in payload:
             issues.append(LLMValidationIssue(code="missing_required_field", message=f"Missing required field '{key}'.", path=key))

    if issues:
         return issues

    if not isinstance(payload["is_complete"], bool):
         issues.append(LLMValidationIssue(code="wrong_type", message="Field 'is_complete' must be a boolean.", path="is_complete"))

    if not isinstance(payload["reasoning"], str):
         issues.append(LLMValidationIssue(code="wrong_type", message="Field 'reasoning' must be a string.", path="reasoning"))

    new_subtasks = payload.get("new_subtasks")
    if not isinstance(new_subtasks, list):
         issues.append(LLMValidationIssue(code="wrong_type", message="Field 'new_subtasks' must be a list.", path="new_subtasks"))
         return issues

    # Only validate new subtasks structure if they exist
    if new_subtasks:
        for index, subtask in enumerate(new_subtasks):
            path = f"new_subtasks[{index}]"
            if not isinstance(subtask, dict):
                issues.append(LLMValidationIssue(code="wrong_type", message="Each subtask must be an object.", path=path))
                continue
            extra_keys = set(subtask.keys()) - DECOMPOSE_SUBTASK_KEYS
            if extra_keys:
                issues.append(LLMValidationIssue(code="extra_keys", message=f"Unexpected keys: {sorted(extra_keys)}.", path=path))
            for key in ["action", "description"]:
                if key not in subtask:
                    issues.append(LLMValidationIssue(code="missing_required_field", message=f"Missing required field '{key}'.", path=f"{path}.{key}"))

            if "action" in subtask:
                if not isinstance(subtask["action"], str):
                     issues.append(LLMValidationIssue(code="wrong_type", message="Field 'action' must be a string.", path=f"{path}.action"))
                elif subtask["action"] not in VALID_DECOMPOSE_ACTIONS:
                     issues.append(LLMValidationIssue(code="invalid_value", message=f"Action must be one of {VALID_DECOMPOSE_ACTIONS}.", path=f"{path}.action"))

            if "description" in subtask and not isinstance(subtask["description"], str):
                 issues.append(LLMValidationIssue(code="wrong_type", message="Field 'description' must be a string.", path=f"{path}.description"))

            for opt_key in ["target_path", "command"]:
                if opt_key in subtask and subtask[opt_key] is not None and not isinstance(subtask[opt_key], str):
                     issues.append(LLMValidationIssue(code="wrong_type", message=f"Field '{opt_key}' must be a string or null.", path=f"{path}.{opt_key}"))

    return issues


def validate_decompose_payload(payload: dict[str, Any]) -> list[LLMValidationIssue]:
    issues: list[LLMValidationIssue] = []
    extra_top_level = set(payload.keys()) - DECOMPOSE_TOP_LEVEL_KEYS
    if extra_top_level:
        issues.append(LLMValidationIssue(code="extra_keys", message=f"Unexpected top-level keys: {sorted(extra_top_level)}.", path="$"))

    subtasks = payload.get("subtasks")
    if not isinstance(subtasks, list) or not subtasks:
        issues.append(LLMValidationIssue(code="missing_required_field", message="The 'subtasks' field must be a non-empty list.", path="subtasks"))
        return issues

    for index, subtask in enumerate(subtasks):
        path = f"subtasks[{index}]"
        if not isinstance(subtask, dict):
            issues.append(LLMValidationIssue(code="wrong_type", message="Each subtask must be an object.", path=path))
            continue
        extra_keys = set(subtask.keys()) - DECOMPOSE_SUBTASK_KEYS
        if extra_keys:
            issues.append(LLMValidationIssue(code="extra_keys", message=f"Unexpected keys: {sorted(extra_keys)}.", path=path))
        for key in ["action", "description"]:
            if key not in subtask:
                issues.append(LLMValidationIssue(code="missing_required_field", message=f"Missing required field '{key}'.", path=f"{path}.{key}"))

        if "action" in subtask:
            if not isinstance(subtask["action"], str):
                 issues.append(LLMValidationIssue(code="wrong_type", message="Field 'action' must be a string.", path=f"{path}.action"))
            elif subtask["action"] not in VALID_DECOMPOSE_ACTIONS:
                 issues.append(LLMValidationIssue(code="invalid_value", message=f"Action must be one of {VALID_DECOMPOSE_ACTIONS}.", path=f"{path}.action"))

        if "description" in subtask and not isinstance(subtask["description"], str):
             issues.append(LLMValidationIssue(code="wrong_type", message="Field 'description' must be a string.", path=f"{path}.description"))

        for opt_key in ["target_path", "command"]:
            if opt_key in subtask and subtask[opt_key] is not None and not isinstance(subtask[opt_key], str):
                 issues.append(LLMValidationIssue(code="wrong_type", message=f"Field '{opt_key}' must be a string or null.", path=f"{path}.{opt_key}"))

    return issues


def validate_review_payload(payload: dict[str, Any]) -> list[LLMValidationIssue]:
    issues: list[LLMValidationIssue] = []
    extra_top_level = set(payload.keys()) - REVIEW_TOP_LEVEL_KEYS
    if extra_top_level:
        issues.append(LLMValidationIssue(code="extra_keys", message=f"Unexpected top-level keys: {sorted(extra_top_level)}.", path="$"))

    for key in ["verdict", "summary", "scope_ok", "overbuild_detected", "findings", "suggested_adjustments"]:
        if key not in payload:
            issues.append(LLMValidationIssue(code="missing_required_field", message=f"Missing required field '{key}'.", path=key))
    if issues:
        return issues

    if payload["verdict"] not in VALID_REVIEW_VERDICTS:
        issues.append(LLMValidationIssue(code="wrong_type", message="Field 'verdict' must be accept, revise, or caution.", path="verdict"))
    if not isinstance(payload["summary"], str):
        issues.append(LLMValidationIssue(code="wrong_type", message="Field 'summary' must be a string.", path="summary"))
    if not isinstance(payload["scope_ok"], bool):
        issues.append(LLMValidationIssue(code="wrong_type", message="Field 'scope_ok' must be a boolean.", path="scope_ok"))
    if not isinstance(payload["overbuild_detected"], bool):
        issues.append(LLMValidationIssue(code="wrong_type", message="Field 'overbuild_detected' must be a boolean.", path="overbuild_detected"))
    if not isinstance(payload["suggested_adjustments"], list) or not all(isinstance(item, str) for item in payload["suggested_adjustments"]):
        issues.append(LLMValidationIssue(code="wrong_type", message="Field 'suggested_adjustments' must be a list of strings.", path="suggested_adjustments"))

    findings = payload["findings"]
    if not isinstance(findings, list):
        issues.append(LLMValidationIssue(code="wrong_type", message="Field 'findings' must be a list.", path="findings"))
        return issues

    for index, finding in enumerate(findings):
        path = f"findings[{index}]"
        if not isinstance(finding, dict):
            issues.append(LLMValidationIssue(code="wrong_type", message="Each finding must be an object.", path=path))
            continue
        extra_keys = set(finding.keys()) - REVIEW_FINDING_KEYS
        if extra_keys:
            issues.append(LLMValidationIssue(code="extra_keys", message=f"Unexpected keys: {sorted(extra_keys)}.", path=path))
        for key in REVIEW_FINDING_KEYS:
            if key not in finding:
                issues.append(LLMValidationIssue(code="missing_required_field", message=f"Missing required field '{key}'.", path=f"{path}.{key}"))
        if "category" in finding and not isinstance(finding["category"], str):
            issues.append(LLMValidationIssue(code="wrong_type", message="Field 'category' must be a string.", path=f"{path}.category"))
        if "message" in finding and not isinstance(finding["message"], str):
            issues.append(LLMValidationIssue(code="wrong_type", message="Field 'message' must be a string.", path=f"{path}.message"))
        if "severity" in finding and finding["severity"] not in VALID_REVIEW_SEVERITIES:
            issues.append(LLMValidationIssue(code="wrong_type", message="Field 'severity' must be low, medium, or high.", path=f"{path}.severity"))
        step_ids = finding.get("step_ids")
        if step_ids is not None and (not isinstance(step_ids, list) or not all(isinstance(item, str) for item in step_ids)):
            issues.append(LLMValidationIssue(code="wrong_type", message="Field 'step_ids' must be a list of strings.", path=f"{path}.step_ids"))
    return issues
